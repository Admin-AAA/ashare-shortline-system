import asyncio
from pathlib import Path

from app.agents.agno_skill_agent import SkillReviewOutput, _build_user_prompt, _parse_to_output
from app.agents.review_runner import run_skill_reviews
from app.agents.session_registry import AgentSessionRegistry
from app.engine.skill_loader import StrategySkill


def _skill(skill_id: str = "test-skill") -> StrategySkill:
    return StrategySkill(
        skill_id=skill_id,
        name="测试 Skill",
        version="0.1.0",
        path=Path(__file__).parent,
        manifest={},
        skill_frontmatter={},
        skill_body="## 原则\n- 先看市场，再看个股",
        skill_hash="abc123",
        reference_files=[],
        contract={"id": skill_id, "name": "测试规则"},
        contract_hash="contract123",
        enabled=True,
    )


def test_run_skill_reviews_uses_agno_agent_and_primes_session(monkeypatch):
    skill = _skill()
    stock = {
        "code": "000001",
        "name": "平安银行",
        "price": 12.34,
        "turnover_rate": 3.2,
    }
    snapshot = {
        "trade_date": "2026-05-15",
        "breadth": {"regime": "多头"},
        "limit_up_count": 12,
        "limit_down_count": 2,
    }
    rule_signals = {
        skill.skill_id: {
            "phase": "发酵",
            "score": 72.0,
            "evidence": ["主线热点延续"],
            "runtime": "strategy_yaml",
        }
    }

    AgentSessionRegistry.register(
        session_id="sess_test_review_runner",
        session_factory=lambda: None,
        skills=[skill],
        snapshot=snapshot,
    )

    created = []

    def fake_create_skill_agent(skill_obj, session_id):
        created.append((skill_obj.skill_id, session_id))
        return {"skill_id": skill_obj.skill_id, "session_id": session_id}

    async def fake_run_skill_agent(*, agent, stock, snapshot, rule_signal, session_id, skill_id):
        ctx = AgentSessionRegistry.get(session_id)
        assert ctx is not None
        assert ctx.stocks_cache["000001"]["name"] == "平安银行"
        assert ctx.strategy_signals_cache[f"{skill_id}:000001"]["score"] == 72.0
        assert agent["skill_id"] == skill_id
        assert rule_signal["phase"] == "发酵"
        return SkillReviewOutput(
            stance="support",
            score=78,
            confidence=0.81,
            evidence=["主线热点延续"],
            risks=["分歧日留意承接"],
            summary="轻仓试错，观察分歧后是否转一致。",
            market_regime_judgment="发酵期",
            action="轻仓试错",
            position_range="5-15%",
            entry_style="分歧低吸",
            invalidation_conditions=["若主线掉队，撤回试错"],
            next_day_plan=["竞价不强不做一致追高"],
        )

    monkeypatch.setattr("app.agents.review_runner.create_skill_agent", fake_create_skill_agent)
    monkeypatch.setattr("app.agents.review_runner.run_skill_agent", fake_run_skill_agent)

    try:
        results = asyncio.run(
            run_skill_reviews(
                stock=stock,
                skills=[skill],
                snapshot=snapshot,
                rule_signals_by_skill=rule_signals,
                session_id="sess_test_review_runner",
            )
        )
    finally:
        AgentSessionRegistry.unregister("sess_test_review_runner")

    assert created == [(skill.skill_id, "sess_test_review_runner")]
    assert len(results) == 1
    review = results[0]["review"]
    assert review["runtime"] == "agent_skill_agno"
    assert review["supports"] == ["主线热点延续"]
    assert review["evidence"] == ["主线热点延续"]
    assert review["score_delta"] == 28.0
    assert review["action"] == "轻仓试错"
    assert review["position_range"] == "5-15%"
    assert review["entry_style"] == "分歧低吸"
    assert review["invalidation_conditions"] == ["若主线掉队，撤回试错"]
    assert review["next_day_plan"] == ["竞价不强不做一致追高"]
    assert results[0]["error"] is None


def test_user_prompt_requires_minimum_tool_sequence():
    prompt = _build_user_prompt(
        stock={"code": "600000", "name": "浦发银行"},
        snapshot={"trade_date": "2026-05-15"},
        rule_signal={"phase": "启动", "score": 65},
        history=None,
        skill_id="bank-skill",
    )

    assert 'query_market_snapshot()' in prompt
    assert 'query_stock_detail("600000")' in prompt
    assert 'audit_data_quality("600000")' in prompt
    assert 'query_skill_signals("600000", "bank-skill")' in prompt
    assert "不要跳过前四步" in prompt


def test_parse_to_output_fills_trading_plan_defaults():
    output = _parse_to_output(
        {
            "stance": "support",
            "score": 73,
            "confidence": 0.72,
            "evidence": ["主线延续"],
            "risks": ["若承接不足则转弱"],
            "summary": "轻仓试错，等待分歧转强。",
            "market_regime_judgment": "发酵期",
        }
    )

    assert output.action == "轻仓试错"
    assert output.position_range == "10-20%"
    assert output.entry_style == "分歧低吸或小仓跟随"
    assert output.invalidation_conditions == ["若承接不足则转弱"]
    assert output.next_day_plan

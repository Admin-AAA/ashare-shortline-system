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


def test_run_skill_reviews_uses_direct_path_and_primes_session(monkeypatch):
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

    direct_calls = []

    async def fake_run_skill_agent_direct(*, skill, stock, snapshot, rule_signal):
        direct_calls.append((skill.skill_id, stock["code"], rule_signal["phase"] if rule_signal else None))
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

    monkeypatch.setattr("app.agents.review_runner.run_skill_agent_direct", fake_run_skill_agent_direct)

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

    assert direct_calls == [(skill.skill_id, "000001", "发酵")]
    assert len(results) == 1
    review = results[0]["review"]
    assert review["runtime"] == "agent_skill_agno"
    assert review["supports"] == ["主线热点延续"]
    assert review["evidence"] == ["主线热点延续"]
    assert review["score_delta"] == 16.0
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

"""T5 一致性（self-improve，2026-07-21）：反对票(oppose)不得为股票加分。

回归锁：LLM review 的 stance==oppose 时，其 score_delta 的正向贡献必须被钳到 0，
禁止"反对票把股票推过 score_line"。规则分本身已 ≥ 正式地板(72)的股票仍应过线
（隐藏强票被保留），只有"靠 oppose 加分才勉强过线"的边际票被挡下。
"""

from app.engine.agent_skill_runner import (
    agent_only_signal,
    enrich_signal_with_agent_review,
)


def _review(stance: str, score_delta: float) -> dict:
    return {
        "stance": stance,
        "score_delta": score_delta,
        "confidence": 0.6,
        "summary": "测试",
        "risks": [],
        "supports": [],
    }


def test_oppose_does_not_boost_enrich_path():
    """enrich 路径：rule_score=70(<地板) + oppose(+5) → 不得过线，score 保持 70。"""
    signal = {"score": 70.0, "score_cap": 100.0}
    out = enrich_signal_with_agent_review(signal, _review("oppose", 5.0))
    assert out["score"] == 70.0  # 加分被钳到 0，未推过线


def test_support_still_boosts_enrich_path():
    """对照：support(+5) 仍正常加分（70→75）。"""
    signal = {"score": 70.0, "score_cap": 100.0}
    out = enrich_signal_with_agent_review(signal, _review("support", 5.0))
    assert out["score"] == 75.0


def test_oppose_penalty_still_applied_enrich_path():
    """oppose(-5) 仍记为减分（70→65）。"""
    signal = {"score": 70.0, "score_cap": 100.0}
    out = enrich_signal_with_agent_review(signal, _review("oppose", -5.0))
    assert out["score"] == 65.0


def test_rule_clearing_winner_preserved_enrich_path():
    """规则分已 ≥ 地板(80) 的隐藏强票，oppose(+5) 不挡下（保持 80，未再加分也未误杀）。"""
    signal = {"score": 80.0, "score_cap": 100.0}
    out = enrich_signal_with_agent_review(signal, _review("oppose", 5.0))
    assert out["score"] == 80.0


def test_oppose_does_not_boost_agent_only_path():
    """agent_only 路径：oppose(+5) → 封顶到中性 50，绝不赖加分过线。"""
    out = agent_only_signal(_skill(), {"code": "000001"}, _review("oppose", 5.0))
    assert out["score"] == 50.0


def test_support_still_boosts_agent_only_path():
    """对照：agent_only 的 support(+5) → 55。"""
    out = agent_only_signal(_skill(), {"code": "000001"}, _review("support", 5.0))
    assert out["score"] == 55.0


def _skill():
    from pathlib import Path

    from app.engine.skill_loader import StrategySkill

    return StrategySkill(
        skill_id="test-skill",
        name="测试 Skill",
        version="0.1.0",
        path=Path(__file__).parent,
        manifest={},
        skill_frontmatter={},
        skill_body="## 原则",
        skill_hash="abc123",
        reference_files=[],
        contract=None,
        contract_hash=None,
        enabled=True,
    )

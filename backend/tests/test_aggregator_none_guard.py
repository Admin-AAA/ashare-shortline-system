"""Regression: aggregate() must never crash on None component scores.

T12 (07-22 03:52 失败正式 run `run_20260722_035223_3c4be8`): 市场分偶发
None 触发 `TypeError: type NoneType doesn't define __round__ method`，使整条
推荐管线在首个候选(progress 0/55)崩溃。防御性守卫后，None 应被中性默认值
吸收，返回合法卡片，纯故障隔离、不改动策略/门线/聚合权重。
"""
import pytest

from app.engine.aggregator import aggregate


def _stock(code="600519", name="测试", price=100.0, **kwargs):
    return {"code": code, "name": name, "price": price, "industry": "白酒", **kwargs}


def _signal(stance="support", score=75.0, review_score=None, hard_veto=False):
    review = {
        "stance": stance,
        "score": review_score if review_score is not None else score,
        "score_delta": 0,
        "confidence": 0.8,
        "evidence": ["均线多头"],
        "risks": [],
        "summary": "测试审查",
    }
    return {
        "strategy_id": "test-skill",
        "strategy_name": "TestSkill",
        "score": score,
        "confidence": 0.7,
        "evidence": [],
        "risk_flags": [],
        "hard_veto": hard_veto,
        "agent_skill_review": review,
        "setup_match": "多头排列",
        "entry_rule": "回踩5日线",
        "invalid_rule": "跌破10日线",
    }


def _run(market_score=None):
    stock = _stock()
    signals = [_signal(stance="support", score=75.0)]
    return aggregate(
        stock=stock,
        signals=signals,
        market_score=market_score,
        agent_review_score=75.0,
        risk_template="balanced",
        calibrated_weights=None,
        market_snapshot=None,
    )


def test_aggregate_market_score_none_does_not_crash():
    """复现 07-22 03:52 生产故障：market_score=None 不应再抛 TypeError。"""
    card = _run(market_score=None)
    assert isinstance(card, dict)
    comp = card.get("component_scores", {})
    # None 退化为中性 50.0，而非崩溃
    assert comp.get("market") == 50.0
    assert isinstance(card.get("final_score"), (int, float))


def test_aggregate_market_score_float_passthrough():
    """正常 float 市场分应原样进入 component_scores（守卫不改变正常行为）。"""
    card = _run(market_score=82.0)
    assert card.get("component_scores", {}).get("market") == 82.0
    assert isinstance(card.get("final_score"), (int, float))


def test_aggregate_evidence_quality_none_safe():
    """stock 缺失 evidence_quality_score 时退化为 60.0 默认，仍返回合法卡片。"""
    stock = _stock(evidence_quality_score=None)
    signals = [_signal(stance="support", score=75.0)]
    card = aggregate(
        stock=stock,
        signals=signals,
        market_score=72.0,
        agent_review_score=75.0,
        risk_template="balanced",
        calibrated_weights=None,
        market_snapshot=None,
    )
    assert isinstance(card, dict)
    assert isinstance(card.get("final_score"), (int, float))

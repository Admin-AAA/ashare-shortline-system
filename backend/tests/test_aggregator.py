"""Tests for signal aggregation, stance mapping, consensus, and bucketing."""
from app.engine.aggregator import (
    DEFAULT_AGG_WEIGHTS,
    STANCE_MULTIPLIER,
    _bucket_for_score,
    _consensus_boost,
    _extract_skill_agent_score,
    aggregate,
    apply_pool_rules,
)
from app.engine.agent_skill_runner import enrich_signal_with_agent_review


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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
    base = {
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
    return base


# ---------------------------------------------------------------------------
# DEFAULT_AGG_WEIGHTS
# ---------------------------------------------------------------------------

def test_default_weights_sum_to_one():
    assert sum(DEFAULT_AGG_WEIGHTS.values()) == 1.0


def test_default_weights_match_aggregator_spec():
    assert DEFAULT_AGG_WEIGHTS == {
        "skill_agent": 0.50,
        "strategy_rule": 0.25,
        "market": 0.10,
        "risk": 0.10,
        "evidence": 0.05,
    }


# ---------------------------------------------------------------------------
# STANCE_MULTIPLIER
# ---------------------------------------------------------------------------

def test_stance_multiplier_values():
    assert STANCE_MULTIPLIER["strong_support"] == 1.15
    assert STANCE_MULTIPLIER["support"] == 1.08
    assert STANCE_MULTIPLIER["neutral_support"] == 1.0
    assert STANCE_MULTIPLIER["neutral"] == 0.85
    assert STANCE_MULTIPLIER["oppose"] == 0.6
    assert STANCE_MULTIPLIER["veto"] == "hard_veto"


# ---------------------------------------------------------------------------
# _extract_skill_agent_score
# ---------------------------------------------------------------------------

def test_extract_skill_agent_score_weighted_average():
    signals = [_signal("support", 80), _signal("neutral", 60)]
    score = _extract_skill_agent_score(signals)
    # support × 1.08 = 86.4, neutral × 0.85 = 51.0, average = 68.7
    assert 65 < score < 72


def test_extract_skill_agent_score_veto_zero():
    signals = [_signal("support", 80), _signal("veto", 30)]
    score = _extract_skill_agent_score(signals)
    # support × 1.08 = 86.4, veto = 0.0, average = 43.2
    assert 40 < score < 47


def test_extract_skill_agent_score_empty_signals():
    assert _extract_skill_agent_score([]) == 50.0


# ---------------------------------------------------------------------------
# _consensus_boost
# ---------------------------------------------------------------------------

def test_consensus_boost_high_agreement():
    signals = [_signal("support", 75), _signal("support", 80), _signal("neutral_support", 72)]
    boost, tags = _consensus_boost(signals)
    # 3 skills >= 70 → boost = 1.0 + 0.1 * 2 = 1.20
    assert boost == 1.20
    assert "high_agreement" in tags


def test_consensus_boost_two_agreeing():
    signals = [_signal("support", 75), _signal("support", 80), _signal("neutral", 50)]
    boost, tags = _consensus_boost(signals)
    # 2 skills >= 70 → boost = 1.0 + 0.1 * 1 = 1.10
    assert boost == 1.10
    assert "high_agreement" in tags


def test_consensus_boost_multi_oppose():
    signals = [_signal("oppose", 45), _signal("oppose", 40)]
    boost, tags = _consensus_boost(signals)
    # 2 opposes → boost *= 0.90
    assert boost == 0.90
    assert "multi_oppose_strike" in tags


def test_consensus_boost_all_bullish_aligned():
    signals = [_signal("support", 80), _signal("strong_support", 85)]
    stances = [{"stance": "support"}, {"stance": "strong_support"}]
    boost, tags = _consensus_boost(signals, stances)
    assert boost >= 1.05  # all_bullish_aligned bonus
    assert "all_bullish_aligned" in tags


def test_consensus_boost_high_controversy():
    signals = [_signal("strong_support", 85), _signal("veto", 10)]
    stances = [{"stance": "strong_support"}, {"stance": "veto"}]
    boost, tags = _consensus_boost(signals, stances)
    assert boost == 0.88
    assert "high_controversy" in tags


def test_consensus_boost_no_agreement():
    signals = [_signal("neutral", 55), _signal("oppose", 45)]
    boost, tags = _consensus_boost(signals)
    assert boost == 1.0
    assert not tags


# ---------------------------------------------------------------------------
# _bucket_for_score
# ---------------------------------------------------------------------------

def test_bucket_capacity_core():
    assert _bucket_for_score(90) == "容量核心"
    assert _bucket_for_score(84) == "容量核心"


def test_bucket_high_elastic():
    assert _bucket_for_score(83) == "容量核心"
    assert _bucket_for_score(78) == "高弹观察"
    assert _bucket_for_score(72) == "高弹观察"
    assert _bucket_for_score(64) == "高弹观察"


def test_bucket_insufficient_evidence():
    assert _bucket_for_score(49) == "证据不足"
    assert _bucket_for_score(35) == "证据不足"
    assert _bucket_for_score(31) == "证据不足"
    assert _bucket_for_score(28) == "明确回避"


def test_bucket_explicit_avoid():
    assert _bucket_for_score(28) == "明确回避"
    assert _bucket_for_score(0) == "明确回避"


# ---------------------------------------------------------------------------
# aggregate — end-to-end unit
# ---------------------------------------------------------------------------

def test_aggregate_basic():
    stock = _stock(price=100.0)
    signals = [_signal("support", 80)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=75)
    assert card["code"] == "600519"
    assert card["final_score"] > 0
    assert card["bucket"] in {"容量核心", "高弹观察", "证据不足", "明确回避"}


def test_aggregate_hard_veto():
    stock = _stock()
    signals = [_signal("veto", 10, hard_veto=True)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=10)
    assert card["final_score"] == 0.0
    assert card["bucket"] == "明确回避"


def test_aggregate_veto_stance():
    stock = _stock()
    signals = [_signal("veto", 10, review_score=10)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=10)
    assert card["final_score"] == 0.0
    assert card["bucket"] == "明确回避"


def test_aggregate_component_scores():
    stock = _stock(evidence_quality_score=80.0)
    signals = [_signal("support", 75)]
    card = aggregate(stock, signals, market_score=65, agent_review_score=70)
    cs = card.get("component_scores", {})
    assert "skill_agent" in cs
    assert "strategy_rule" in cs
    assert "market" in cs
    assert "risk" in cs
    assert "evidence" in cs


def test_aggregate_exposes_trade_price_levels():
    stock = _stock(price=100.0, stop_loss=94.5, take_profit=108.0)
    signals = [_signal("support", 75)]
    card = aggregate(stock, signals, market_score=65, agent_review_score=70)
    assert card["recommend_price"] == 100.0
    assert card["stop_loss"] == 94.5
    assert card["take_profit"] == 108.0


def test_aggregate_calibrated_weights_override():
    stock = _stock(evidence_quality_score=80.0)
    signals = [_signal("support", 75)]
    # default weights produce one score; overriding with all-zero except market
    calibrated = {"skill_agent": 0.0, "strategy_rule": 0.0, "market": 1.0, "risk": 0.0, "evidence": 0.0}
    card = aggregate(stock, signals, market_score=80, agent_review_score=70, calibrated_weights=calibrated)
    # final_score should be close to market_score=80 (since only market gets weight)
    assert abs(card["final_score"] - 80) < 5


def test_aggregate_authenticity_discount():
    stock = _stock(data_authenticity=0.4)
    signals = [_signal("support", 75)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=70)
    # authenticity < 0.7 → ×0.85, < 0.5 → ×0.75 → total 0.6375 × original
    assert card["data_authenticity"] == 0.4


def test_aggregate_authenticity_bucket_downgrade():
    stock = _stock(
        data_authenticity=0.4,
        data_quality={"source_breakdown": {"OBSERVED": 3, "SYNTHETIC": 7}},
    )
    signals = [_signal("strong_support", 90)]
    card = aggregate(stock, signals, market_score=85, agent_review_score=90)
    # authenticity < 0.5 and SYNTHETIC > 50% → bucket downgrade to 证据不足
    assert card["bucket"] == "证据不足"
    assert card["authenticity_bucket_downgrade"] is True


def test_aggregate_bucket_capacity_core_via_score():
    stock = _stock()
    signals = [_signal("strong_support", 95)]
    card = aggregate(stock, signals, market_score=90, agent_review_score=95)
    # High score should land in 容量核心
    assert card["bucket"] in {"容量核心", "高弹观察"}


def test_aggregate_empty_signals():
    stock = _stock()
    card = aggregate(stock, [], market_score=70, agent_review_score=50)
    assert card["final_score"] == 0.0
    assert card["bucket"] == "明确回避"


def test_aggregate_stance_summary():
    stock = _stock()
    signals = [_signal("strong_support", 85), _signal("neutral", 55)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=70)
    summary = card.get("stance_summary", [])
    assert len(summary) == 2
    assert summary[0]["stance"] == "strong_support"
    assert summary[1]["stance"] == "neutral"


def test_aggregate_consensus_tags_empty_when_no_skills():
    stock = _stock()
    # Signal without agent_skill_review
    signals = [{
        "strategy_id": "test",
        "score": 70,
        "confidence": 0.5,
        "evidence": [],
        "risk_flags": [],
    }]
    card = aggregate(stock, signals, market_score=70, agent_review_score=70)
    assert isinstance(card.get("consensus_tags"), list)


# ---------------------------------------------------------------------------
# apply_pool_rules
# ---------------------------------------------------------------------------

def test_apply_pool_rules_bucket_limits():
    cards = [
        {"code": c, "final_score": s, "bucket": b}
        for c, s, b in [
            ("A", 95, "容量核心"),
            ("B", 90, "容量核心"),
            ("C", 85, "容量核心"),
            ("D", 80, "高弹观察"),
            ("E", 75, "高弹观察"),
        ]
    ]
    result = apply_pool_rules(cards, {"容量核心": 2, "高弹观察": 1}, output_count=3)
    assert len(result) == 3
    buckets = [r["bucket"] for r in result]
    assert buckets.count("容量核心") <= 2
    assert buckets.count("高弹观察") <= 1


def test_apply_pool_rules_output_count_limit():
    cards = [
        {"code": c, "final_score": s, "bucket": "证据不足"}
        for c, s in [
            ("A", 70), ("B", 65), ("C", 60), ("D", 55), ("E", 50), ("F", 45), ("G", 40),
        ]
    ]
    result = apply_pool_rules(cards, {}, output_count=3)
    assert len(result) == 3
    # Should be top 3 by score
    assert result[0]["code"] == "A"


def test_apply_pool_rules_sorted_by_score():
    cards = [
        {"code": "C", "final_score": 60, "bucket": "证据不足"},
        {"code": "A", "final_score": 95, "bucket": "容量核心"},
        {"code": "B", "final_score": 80, "bucket": "高弹观察"},
    ]
    result = apply_pool_rules(cards, {}, output_count=10)
    assert result[0]["code"] == "A"
    assert result[1]["code"] == "B"
    assert result[2]["code"] == "C"


# ---------------------------------------------------------------------------
# risk template penalty
# ---------------------------------------------------------------------------

def test_risk_template_conservative_penalty():
    from app.engine.aggregator import RISK_TEMPLATE_PENALTY

    assert RISK_TEMPLATE_PENALTY["conservative"] == 1.4
    assert RISK_TEMPLATE_PENALTY["balanced"] == 1.0
    assert RISK_TEMPLATE_PENALTY["aggressive"] == 0.7

    stock = _stock()
    signals = [_signal("support", 75, hard_veto=False)]
    signals[0]["risk_flags"] = ["风险1", "风险2"]
    card_conservative = aggregate(stock, signals, market_score=70, agent_review_score=70, risk_template="conservative")
    card_aggressive = aggregate(stock, signals, market_score=70, agent_review_score=70, risk_template="aggressive")
    # conservative should have lower score due to higher risk penalty
    assert card_conservative["final_score"] <= card_aggressive["final_score"]

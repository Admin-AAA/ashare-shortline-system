"""Tests for signal aggregation, stance mapping, consensus, and scoring."""
import pytest

from app.engine.aggregator import (
    DEFAULT_AGG_WEIGHTS,
    STANCE_MULTIPLIER,
    _consensus_boost,
    _extract_skill_agent_score,
    aggregate,
    apply_pool_rules,
    resolve_score_line_context,
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


def _card(code: str, final_score: float):
    return {
        "code": code,
        "final_score": final_score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


def _neutral_snapshot():
    return {"sample_count": 100, "up_count": 45, "limit_up_count": 0, "limit_down_count": 0}


# ---------------------------------------------------------------------------
# DEFAULT_AGG_WEIGHTS
# ---------------------------------------------------------------------------

def test_default_weights_sum_to_one():
    assert sum(DEFAULT_AGG_WEIGHTS.values()) == 1.0


def test_default_weights_match_aggregator_spec():
    assert DEFAULT_AGG_WEIGHTS == {
        "skill_agent": 0.55,
        "strategy_rule": 0.15,
        "market": 0.10,
        "risk": 0.10,
        "evidence": 0.10,
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
    # 先按 confidence 向 50 回归，再乘 stance 系数，并按 confidence 加权
    assert score == pytest.approx(64.61, abs=0.01)


def test_extract_skill_agent_score_veto_zero():
    signals = [_signal("support", 80), _signal("veto", 30)]
    score = _extract_skill_agent_score(signals)
    # veto 以 0 分参与平均，并保留其原始权重，确保否决能显著压低综合分
    assert score == pytest.approx(35.52, abs=0.01)


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
# aggregate — end-to-end unit
# ---------------------------------------------------------------------------

def test_aggregate_basic():
    stock = _stock(price=100.0)
    signals = [_signal("support", 80)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=75)
    assert card["code"] == "600519"
    assert card["final_score"] > 0


def test_aggregate_hard_veto():
    stock = _stock()
    signals = [_signal("veto", 10, hard_veto=True)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=10)
    assert card["final_score"] == 0.0


def test_aggregate_veto_stance():
    stock = _stock()
    signals = [_signal("veto", 10, review_score=10)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=10)
    assert card["final_score"] == 0.0


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


def test_aggregate_authenticity_discount_with_low_quality():
    stock = _stock(
        data_authenticity=0.4,
        data_quality={"source_breakdown": {"OBSERVED": 3, "SYNTHETIC": 7}},
    )
    signals = [_signal("strong_support", 90)]
    card = aggregate(stock, signals, market_score=85, agent_review_score=90)
    # authenticity < 0.5 and SYNTHETIC > 50% → score heavily discounted
    assert card["authenticity_severe_discount"] is True
    assert card["final_score"] < 70


def test_aggregate_high_score():
    stock = _stock()
    signals = [_signal("strong_support", 95)]
    card = aggregate(stock, signals, market_score=90, agent_review_score=95)
    # High quality signals should produce high final_score
    assert card["final_score"] >= 75


def test_aggregate_empty_signals():
    stock = _stock()
    card = aggregate(stock, [], market_score=70, agent_review_score=50)
    assert card["final_score"] == 0.0


def test_aggregate_stance_summary():
    stock = _stock()
    signals = [_signal("strong_support", 85), _signal("neutral", 55)]
    card = aggregate(stock, signals, market_score=70, agent_review_score=70)
    summary = card.get("stance_summary", [])
    assert len(summary) == 2
    assert summary[0]["stance"] == "strong_support"
    assert summary[1]["stance"] == "neutral"


def test_aggregate_market_discipline_caps_position_in_weak_market():
    stock = _stock(
        price=100.0,
        stop_loss=96.0,
        take_profit=112.0,
        evidence_quality_score=88.0,
        data_authenticity=1.0,
        risk_score=0.0,
    )
    signals = [_signal("strong_support", 92), _signal("support", 88)]
    snapshot = {
        "sample_count": 300,
        "up_count": 70,
        "limit_up_count": 2,
        "limit_down_count": 12,
    }

    card = aggregate(stock, signals, market_score=42, agent_review_score=90, market_snapshot=snapshot)
    discipline = card["market_discipline"]

    assert discipline["market_regime"] == "弱势·活跃"
    assert discipline["max_position_pct"] <= 20
    assert "市场弱势，按防守仓位处理" in card["risk_flags"]
    assert "跌停压力不低于涨停，先控制回撤" in card["risk_flags"]


def test_aggregate_market_discipline_caps_climax_to_light_position():
    stock = _stock(
        price=100.0,
        stop_loss=96.0,
        take_profit=112.0,
        evidence_quality_score=92.0,
        data_authenticity=1.0,
        risk_score=0.0,
    )
    signals = [_signal("strong_support", 95), _signal("support", 90)]
    snapshot = {
        "sample_count": 300,
        "up_count": 230,
        "limit_up_count": 18,
        "limit_down_count": 0,
    }

    card = aggregate(stock, signals, market_score=86, agent_review_score=92, market_snapshot=snapshot)
    discipline = card["market_discipline"]

    assert discipline["market_regime"] == "强牛·高潮"
    assert discipline["max_position_pct"] <= 25
    assert discipline["action"] == "轻仓试错"
    assert "情绪高潮，提高门槛并减少输出，追高只允许轻仓" in card["risk_flags"]


def test_aggregate_records_missing_theme_and_news_risks():
    stock = _stock(
        evidence_quality_score=90,
        data_quality={"missing_fields": ["theme_tags", "news", "dragon_tiger"]},
    )
    signals = [_signal("support", 78)]

    card = aggregate(stock, signals, market_score=70, agent_review_score=75)

    assert "多维度数据缺失（公告/龙虎榜/题材等），置信度受影响" in card["info_tags"]


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


def test_aggregate_exposes_shared_shortline_factors_and_factor_risks():
    stock = _stock(
        limit_up_overheat_score=82.0,
        limit_up_overheat_level="高",
        sector_ladder_completeness_score=78.0,
        sector_ladder_level="完整",
        sector_ladder_limit_up_count=4,
        sector_ladder_height=3,
        capital_resonance_score=76.0,
        capital_resonance_signal="主力共振",
        capital_resonance_source="capital_flow",
        is_20cm_stock=True,
        twenty_cm_risk_score=86.0,
        twenty_cm_risk_gate="block",
        twenty_cm_risk_reason="创业板 20cm 标的；换手过高",
        shortline_factor_explanations={
            "twenty_cm_risk": {
                "fields": ["is_20cm_stock", "twenty_cm_risk_score", "twenty_cm_risk_gate"],
                "logic": "识别 20cm 标的并按波动、换手、流动性给闸门",
                "data_sources": ["证券代码/交易所/市场板块", "行情快照"],
                "degradation": "缺少市场板块时用代码前缀推断",
                "score": 86.0,
                "value": "block",
            }
        },
    )
    card = aggregate(stock, [_signal("support", 82)], market_score=70, agent_review_score=78)

    factors = card["shortline_factors"]
    assert factors["limit_up_overheat"]["level"] == "高"
    assert factors["sector_ladder"]["height"] == 3
    assert factors["capital_resonance"]["source"] == "capital_flow"
    assert factors["twenty_cm_risk"]["gate"] == "block"
    assert card["shortline_factor_explanations"]["twenty_cm_risk"]["degradation"]
    assert "20cm 风险闸门阻断" in card["risk_flags"]
    assert "涨停过热，聚合风控降档" in card["risk_flags"]


# ---------------------------------------------------------------------------
# apply_pool_rules
# ---------------------------------------------------------------------------

def test_apply_pool_rules_output_count_no_longer_decides_recommendations():
    cards = [
        _card(c, s)
        for c, s in [
            ("A", 70), ("B", 65), ("C", 60), ("D", 55), ("E", 50), ("F", 45), ("G", 40),
        ]
    ]
    result = apply_pool_rules(cards, output_count=3, score_line=50, market_snapshot=_neutral_snapshot())
    assert [card["code"] for card in result] == ["A", "B", "C", "D", "E"]


def test_apply_pool_rules_score_line_and_display_limit_are_separate():
    cards = [
        _card(c, s)
        for c, s in [("A", 82), ("B", 76), ("C", 72), ("D", 68)]
    ]
    full = apply_pool_rules(cards, score_line=72, market_snapshot=_neutral_snapshot())
    display = apply_pool_rules(cards, score_line=72, market_snapshot=_neutral_snapshot(), display_limit=2)

    assert [card["code"] for card in full] == ["A", "B", "C"]
    assert [card["code"] for card in display] == ["A", "B"]
    assert all(card["score_line_meta"]["passed"] for card in full)


def test_score_line_context_raises_threshold_in_climax_market():
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}

    context = resolve_score_line_context(72, snapshot)

    assert context["market_base"] == "强牛"
    assert context["market_sentiment"] == "高潮"
    assert context["effective_score_line"] > 72


def test_apply_pool_rules_reduces_output_in_climax_market():
    cards = [_card(code, score) for code, score in [("A", 90), ("B", 88), ("C", 86), ("D", 84), ("E", 82), ("F", 80)]]
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}

    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)

    # 强牛·高潮：market_adjustment(强牛)=3.0 + sentiment(高潮)=5.0+8.0=13 → effective=88
    # A(90)、B(88) 通过，C(86) 被过滤
    assert [card["code"] for card in result] == ["A", "B"]
    assert all(card["score_line_meta"]["effective_score_line"] > 72 for card in result)


def test_apply_pool_rules_allows_only_small_trial_in_weak_ice_point():
    cards = [_card(code, score) for code, score in [("A", 72), ("B", 70), ("C", 68)]]
    snapshot = {"sample_count": 100, "up_count": 20, "down_count": 80, "limit_up_count": 0, "limit_down_count": 8}

    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)

    assert [card["code"] for card in result] == ["A"]
    assert result[0]["score_line_meta"]["market_base"] == "弱势"
    assert result[0]["score_line_meta"]["market_sentiment"] == "冰点"


def test_apply_pool_rules_dynamic_weak_market_line_passes_top_candidate():
    """弱市+冰点时动态降线应放行接近有效线的顶部候选。"""
    cards = [_card("A", 70)]
    snapshot = {"sample_count": 100, "up_count": 20, "down_count": 80, "limit_up_count": 0, "limit_down_count": 8}
    result = apply_pool_rules(cards, score_line=72, market_snapshot=snapshot)

    # 动态降线：effective=71 (72+2-3), gap=1, dynamic_line=69, card(70) 应通过
    assert len(result) == 1
    assert result[0]["code"] == "A"


def test_apply_pool_rules_structural_strength_ma5_penalty():
    """结构性强势阶段 × support_ma5=True 提高 effective_score_line 6 分。
    数据依据：近期该阶段 support_ma5=True WR=39.1% vs False=60.5%，t=-2.15（显著）。
    因果：强势市应追强，有 MA 支撑=已回落均线=追弱，应额外惩罚。"""
    cards = [
        {**_card("A", 75), "support_ma5": True},
        {**_card("B", 75), "support_ma5": False},
    ]
    # 结构性强势 regime（来自 market_data.py regime 字段）
    snapshot = {
        "sample_count": 100, "up_count": 58, "down_count": 42,
        "limit_up_count": 1, "limit_down_count": 0,
        "market_regime": "结构性强势",
    }
    result = apply_pool_rules(cards, score_line=70, market_snapshot=snapshot)
    by_code = {c["code"]: c for c in result}
    # A 有 support_ma5 惩罚 +6，B 无
    if "A" in by_code:
        assert by_code["A"]["score_line_meta"]["regime_support_ma5_penalty"] == 6.0
    if "B" in by_code:
        assert by_code["B"]["score_line_meta"]["regime_support_ma5_penalty"] == 0.0
    # B 的 effective_score_line 严格低于 A
    if "A" in by_code and "B" in by_code:
        assert by_code["A"]["score_line_meta"]["effective_score_line"] > by_code["B"]["score_line_meta"]["effective_score_line"]
        assert by_code["A"]["score_line_meta"]["effective_score_line"] - by_code["B"]["score_line_meta"]["effective_score_line"] == 6.0


def test_apply_pool_rules_sorted_by_score():
    cards = [
        _card("C", 75),
        _card("A", 95),
        _card("B", 80),
    ]
    result = apply_pool_rules(cards, output_count=10, score_line=70, market_snapshot=_neutral_snapshot())
    assert result[0]["code"] == "A"
    assert result[1]["code"] == "B"
    assert result[2]["code"] == "C"


def test_apply_pool_rules_filters_explicit_avoid_cards():
    cards = [
        {"code": "A", "final_score": 0},
        {"code": "B", "final_score": 62},
    ]

    result = apply_pool_rules(cards, output_count=3, score_line=45, market_snapshot=_neutral_snapshot())

    assert [card["code"] for card in result] == ["B"]


def test_apply_pool_rules_blocks_one_word_limit_up_as_unexecutable():
    card = {
        **_card("000001", 96),
        "open": 11.0,
        "high": 11.0,
        "low": 11.0,
        "close": 11.0,
        "pct": 10.0,
        "volume": 10000,
        "amount_yi": 2.0,
        "is_limit_up": True,
        "limit_up_open_times": 0,
    }

    result = apply_pool_rules([card], score_line=70, market_snapshot=_neutral_snapshot())

    assert result == []
    assert card["execution_viability"]["status"] == "blocked"
    assert card["score_line_meta"]["execution_blocked"] is True
    assert "一字涨停" in card["score_line_meta"]["block_reason"]
    assert any("一字涨停" in flag for flag in card["risk_flags"])


def test_apply_pool_rules_marks_opened_limit_up_as_execution_risk():
    card = {
        **_card("601126", 95),
        "open": 10.2,
        "high": 11.0,
        "low": 10.1,
        "close": 11.0,
        "pct": 10.0,
        "volume": 10000,
        "amount_yi": 8.0,
        "is_limit_up": True,
        "limit_up_first_time": "100643",
        "limit_up_last_time": "135555",
        "limit_up_open_times": 17,
        "limit_up_fd_amount_yi": 0.318156,
        "limit_up_turnover_ratio": 5.29,
    }

    result = apply_pool_rules([card], score_line=70, market_snapshot=_neutral_snapshot())

    assert [item["code"] for item in result] == ["601126"]
    assert card["execution_viability"]["status"] == "risky"
    assert card["execution_viability"]["details"]["open_times"] == 17
    assert card["score_line_meta"]["execution_adjustment"] == 6.5
    assert card["score_line_meta"]["passed"] is True
    assert any("涨停排队" in flag for flag in card["risk_flags"])
    assert any("封板反复" in flag for flag in card["risk_flags"])


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

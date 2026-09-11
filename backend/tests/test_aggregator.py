"""Tests for signal aggregation, stance mapping, consensus, and scoring."""
from copy import deepcopy

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
    assert STANCE_MULTIPLIER["neutral"] == 0.6
    assert STANCE_MULTIPLIER["oppose"] == 0.6
    assert STANCE_MULTIPLIER["veto"] == "hard_veto"


# ---------------------------------------------------------------------------
# _extract_skill_agent_score
# ---------------------------------------------------------------------------

def test_extract_skill_agent_score_weighted_average():
    signals = [_signal("support", 80), _signal("neutral", 60)]
    score = _extract_skill_agent_score(signals)
    # 先按 confidence 向 50 回归，再乘 stance 系数，并按 confidence 加权
    # neutral multiplier 0.85→0.6 后期望值从 64.61 → 57.36
    assert score == pytest.approx(57.36, abs=0.01)


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
    assert boost == pytest.approx(1.1)
    assert "all_bullish_aligned" not in tags


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


def test_llm_review_score_is_risk_overlay_not_alpha_ranker():
    stock = _stock(evidence_quality_score=80.0)
    low_review = [_signal("support", score=78.0, review_score=40.0)]
    high_review = [_signal("support", score=78.0, review_score=95.0)]

    low_card = aggregate(stock, low_review, market_score=65, agent_review_score=40)
    high_card = aggregate(stock, high_review, market_score=65, agent_review_score=95)

    assert low_card["final_score"] == high_card["final_score"]
    assert low_card["llm_influence_mode"] == "risk_overlay"
    assert low_card["aggregation_weights"]["skill_agent"] == 0.0


def test_missing_llm_review_does_not_reduce_score_win_rate_or_position():
    stock = _stock(
        evidence_quality_score=88.0,
        data_authenticity=1.0,
        risk_score=0.0,
    )
    reviewed_signal = _signal("support", score=78.0, review_score=78.0)
    rule_only_signal = deepcopy(reviewed_signal)
    rule_only_signal.pop("agent_skill_review")
    snapshot = {
        "sample_count": 100,
        "up_count": 55,
        "limit_up_count": 2,
        "limit_down_count": 1,
    }

    reviewed = aggregate(stock, [reviewed_signal], market_score=65, agent_review_score=78, market_snapshot=snapshot)
    rule_only = aggregate(stock, [rule_only_signal], market_score=65, agent_review_score=78, market_snapshot=snapshot)

    assert rule_only["final_score"] == reviewed["final_score"]
    assert rule_only["market_discipline"]["estimated_win_rate"] == reviewed["market_discipline"]["estimated_win_rate"]
    assert rule_only["market_discipline"]["max_position_pct"] == reviewed["market_discipline"]["max_position_pct"]
    assert all("降权" not in flag for flag in rule_only["risk_flags"])


def test_missing_optional_auction_dimension_does_not_reduce_production_score():
    stock = _stock(evidence_quality_score=88.0, data_authenticity=1.0)
    with_optional_gap = deepcopy(stock)
    with_optional_gap["data_quality"] = {
        "missing_fields": [],
        "optional_missing_fields": ["auction"],
    }
    signal = _signal("support", score=78.0)

    baseline = aggregate(stock, [deepcopy(signal)], market_score=65, agent_review_score=78)
    optional_gap = aggregate(
        with_optional_gap,
        [deepcopy(signal)],
        market_score=65,
        agent_review_score=78,
    )

    assert optional_gap["final_score"] == baseline["final_score"]
    assert "集合竞价数据未覆盖，相关风险需人工复核" in optional_gap["info_tags"]


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
    assert card["entry_rule"] == ""


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
        # T28：活跃须 lu/ld≥1.2；旧夹具 lu=2 ld=12 现为平淡
        "limit_up_count": 8,   # 2.7%
        "limit_down_count": 4,  # lu/ld=2 → 活跃
    }

    card = aggregate(stock, signals, market_score=42, agent_review_score=90, market_snapshot=snapshot)
    discipline = card["market_discipline"]

    assert discipline["market_regime"] == "弱势·活跃"
    # 弱势防守仓 30%（跌停压力旗需 ld≥max(5,lu)，与活跃 lu/ld≥1.2 互斥，不再叠 20%）
    assert discipline["max_position_pct"] <= 30
    assert "市场弱势，按防守仓位处理" in card["risk_flags"]


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


def test_apply_pool_rules_excludes_negative_ev_buckets_in_20cm():
    """chao-gu-yang-jia-skill（负期望）在 20cm universe 被过滤，10cm 保留。

    Regression for the 20cm strategy-filter extension: daily-stock-analysis-skill
    已在 excluded；chao-gu-yang-jia-skill 因 clean≥72 n=15 WR=33% avg=-1.43%
    （跨 9 交易日分散非单日聚集）加入。过滤只针对 20cm board。
    """
    snapshot = _neutral_snapshot()
    dsa_card = {**_card("300001", 78), "is_20cm_stock": True, "bucket": "daily-stock-analysis-skill"}
    cgy_card = {**_card("300002", 78), "is_20cm_stock": True, "bucket": "chao-gu-yang-jia-skill"}
    good_card = {**_card("300003", 78), "is_20cm_stock": True, "bucket": "dragon-tiger-smart-money"}
    # 10cm 的炒股养家应保留（过滤只针对 20cm）
    cgy_10cm = {**_card("600001", 78), "is_20cm_stock": False, "bucket": "chao-gu-yang-jia-skill"}

    result = apply_pool_rules(
        [dsa_card, cgy_card, good_card],
        score_line=72, market_snapshot=snapshot, board_universe="20cm",
    )
    codes = [card["code"] for card in result]
    assert "300001" not in codes  # DSA 排除
    assert "300002" not in codes  # 炒股养家 20cm 排除
    assert "300003" in codes      # 正常策略保留

    # 非 20cm board（10cm 池）：炒股养家 10cm 推荐应保留（过滤只针对 20cm）
    result_10cm = apply_pool_rules(
        [cgy_10cm], score_line=72, market_snapshot=snapshot,
    )
    assert "600001" in [card["code"] for card in result_10cm]


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
    cards = [_card(code, score) for code, score in [("A", 95), ("B", 92), ("C", 86), ("D", 84), ("E", 82), ("F", 80)]]
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}

    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)

    # 强牛·高潮：market_adjustment(强牛)=6.0 + sentiment(高潮)=5.0+8.0=19 → effective=91
    # A(95)、B(92) 通过，C(86) 被过滤
    assert [card["code"] for card in result] == ["A", "B"]
    assert all(card["score_line_meta"]["effective_score_line"] > 72 for card in result)


def test_apply_pool_rules_allows_only_small_trial_in_weak_ice_point():
    cards = [_card(code, score) for code, score in [("A", 72), ("B", 70), ("C", 68)]]
    snapshot = {"sample_count": 100, "up_count": 20, "down_count": 80, "limit_up_count": 0, "limit_down_count": 8}

    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)

    assert [card["code"] for card in result] == ["A"]
    assert result[0]["score_line_meta"]["market_base"] == "弱势"
    assert result[0]["score_line_meta"]["market_sentiment"] == "冰点"


def test_apply_pool_rules_climax_watchlist_surfaces_near_miss_candidates():
    cards = [_card(code, score) for code, score in [
        ("A", 95), ("B", 92), ("C", 88), ("D", 86), ("E", 84), ("F", 70),
    ]]
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}
    out = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot, return_watchlist=True)
    assert isinstance(out, dict)
    # A/B 过线成为正式推荐
    assert [c["code"] for c in out["recommendations"]] == ["A", "B"]
    # 高潮阶段：最接近过线且未封死的候选进入观察池（F 距线 -21 超出 -15 容差，不入）
    wl_codes = [c["code"] for c in out["watchlist"]]
    assert "C" in wl_codes and "D" in wl_codes and "E" in wl_codes
    assert "F" not in wl_codes
    assert all(c.get("watchlist") for c in out["watchlist"])


def test_apply_pool_rules_llm_availability_never_changes_formal_selection():
    """LLM 只做可选语义审查；开关状态不得改变规则选股结果。"""
    rule_only_cards = [_degraded_card(code, score) for code, score in [
        ("600001", 105.0), ("600002", 104.0),
    ]]
    reviewed_cards = [_normal_card(card["code"], card["final_score"]) for card in rule_only_cards]
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}
    without_llm = apply_pool_rules(
        rule_only_cards, output_count=6, score_line=80, market_snapshot=snapshot,
    )
    with_llm = apply_pool_rules(
        reviewed_cards, output_count=6, score_line=80, market_snapshot=snapshot,
    )

    assert [c["code"] for c in without_llm] == [c["code"] for c in with_llm]
    assert [c["score_line_meta"]["effective_score_line"] for c in without_llm] == [
        c["score_line_meta"]["effective_score_line"] for c in with_llm
    ]


def test_apply_pool_rules_climax_stays_silent_below_score_line():
    """高潮阶段保持严格：宁可零推荐也不靠 soft floor 追高。"""
    cards = [_card(code, score) for code, score in [("A", 89.01), ("B", 87.0)]]
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}
    result = apply_pool_rules(
        cards, output_count=6, score_line=80, market_snapshot=snapshot,
    )
    # 完整系统高潮阶段不过线候选 → 空（保留原意图：不追高）
    assert result == []


def test_apply_pool_rules_oscillation_climax_soft_floor_disabled():
    """2026-07-21 T3：震荡·高潮 soft floor 净负，必须禁用。

    实证：07-09 单日 12 笔 soft 票 WR=33.3% avg=-1.85%（有效线~85 被压到~65）。
    反事实剔除后全量 WR 71.2%→74.5%。
    本测：震荡（up_ratio∈[0.35,0.50)）+ 高潮（涨停率高）
    → soft floor 不得放行低分票，正式推荐为空。
    """
    # 震荡：up_ratio=0.40；高潮：lu_rate=0.05 > 1.2% 且 ld=0
    snapshot = {
        "sample_count": 100,
        "up_count": 40,
        "limit_up_count": 5,
        "limit_down_count": 0,
    }
    # 有效线 = 72 + 0(震荡) + 13(高潮 5+8) = 85；候选最高 78 < 85
    cards = [_card(code, score) for code, score in [
        ("A", 78.0), ("B", 72.0), ("C", 66.0), ("D", 56.0),
    ]]
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot,
    )
    assert result == [], (
        "震荡·高潮 soft floor 必须禁用，不得放行 score<有效线 的低分票"
    )


def test_apply_pool_rules_oscillation_active_soft_floor_disabled():
    """2026-07-21 T13：震荡·活跃 soft 同机制净负，全震荡档禁用 soft。

    实证：07-16 soft@震荡·活跃 11 笔 WR=27.3% avg=-2.04%。与高潮合 2 日 23 笔
    WR=30.4%。常规 soft（非高潮/强牛、非降级）在震荡档也不得启用。
    """
    # 震荡：up_ratio=0.40；活跃：lu_rate=0.008（>0.5% 活跃阈值）
    snapshot = {
        "sample_count": 1000,
        "up_count": 400,
        "limit_up_count": 8,
        "limit_down_count": 2,
    }
    # 有效线 = 72 + 0(震荡) + 3(活跃) = 75；候选 70 < 75，旧逻辑会 soft 放行
    cards = [_card(code, score) for code, score in [("A", 70.0), ("B", 68.0)]]
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot,
    )
    assert result == [], "震荡·活跃 soft floor 必须禁用"


def test_apply_pool_rules_weak_climax_is_llm_neutral():
    """弱势·高潮是否启用 soft floor 也不能取决于 LLM 是否可用。"""
    # 弱势：up_ratio=0.20；高潮：lu_rate=0.05
    snapshot = {
        "sample_count": 100,
        "up_count": 20,
        "limit_up_count": 5,
        "limit_down_count": 0,
    }
    # 有效线 = 72 + 2(弱势) + 5(高潮无 extra) = 79；候选低于有效线。
    without_llm = apply_pool_rules(
        [_degraded_card("600001", 74.0), _degraded_card("600002", 70.0)],
        output_count=6, score_line=72, market_snapshot=snapshot,
    )
    with_llm = apply_pool_rules(
        [_normal_card("600001", 74.0), _normal_card("600002", 70.0)],
        output_count=6, score_line=72, market_snapshot=snapshot,
    )
    assert [c["code"] for c in without_llm] == [c["code"] for c in with_llm]


def test_apply_pool_rules_watchlist_empty_when_not_climax():
    cards = [_card(code, score) for code, score in [("A", 60), ("B", 55)]]
    out = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=_neutral_snapshot(), return_watchlist=True,
    )
    assert isinstance(out, dict)
    assert out["recommendations"] == []
    assert out["watchlist"] == []


def test_apply_pool_rules_panic_day_veto_blocks_all_when_limit_down_over_100():
    """limit_down_count>100 = 系统性恐慌日，score_line +12→84，且不参与软降线兜底。
    反事实：2026-05-29(ld=134) 27 笔 LIVE formal 全失败 WR=0% 最高分 76.24，
    84 线全部过滤（panic 日无候选过线）。"""
    cards = [_card(code, score) for code, score in [("A", 80), ("B", 76), ("C", 72)]]
    # 弱势恐慌日：limit_down=134
    snapshot = {"sample_count": 100, "up_count": 20, "down_count": 80,
                "limit_up_count": 5, "limit_down_count": 134}
    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)
    # 全部被 panic_day 否决过滤（最高分 80 < 84）；软降线不兜底 panic_day_penalty
    assert result == []
    # 即使软降线在无候选时本会触发，panic_day_penalty 卡片仍被排除
    for card in cards:
        assert card["score_line_meta"]["panic_day_penalty"] == 12.0
        assert card["score_line_meta"]["effective_score_line"] >= 84.0


def test_apply_pool_rules_panic_day_veto_lets_through_high_score():
    """ld>100 时 score_line 提到 84，真正高分（>=84）的候选仍可通过（极少见）。"""
    cards = [_card(code, score) for code, score in [("A", 90), ("B", 80), ("C", 72)]]
    snapshot = {"sample_count": 100, "up_count": 20, "down_count": 80,
                "limit_up_count": 5, "limit_down_count": 134}
    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)
    # 仅 A(90>=84) 通过；B(80<84) 被 panic 否决；C(72<84) 被否决
    assert [c["code"] for c in result] == ["A"]
    assert result[0]["score_line_meta"]["panic_day_penalty"] == 12.0


def test_apply_pool_rules_no_panic_veto_below_threshold():
    """ld<=100 不触发恐慌日否决（81-100 是 regime-dependent 弱信号，留策略层处理）。"""
    cards = [_card(code, score) for code, score in [("A", 80), ("B", 72)]]
    # ld=84（81-100 区间，不触发 panic veto）
    snapshot = {"sample_count": 100, "up_count": 20, "down_count": 80,
                "limit_up_count": 5, "limit_down_count": 84}
    result = apply_pool_rules(cards, output_count=6, score_line=72, market_snapshot=snapshot)
    # 不应有 panic_day_penalty
    for c in cards:
        assert c["score_line_meta"]["panic_day_penalty"] == 0.0


def _limit_up_risky_card(code: str, final_score: float) -> dict:
    """涨停 + 执行风险：open_times/弱封单/尾盘封板 → execution_adjustment ≈ +7。"""
    card = _card(code, final_score)
    card.update({
        "is_limit_up": True,
        "limit_up_open_times": 3,
        "limit_up_fd_amount_yi": 0.3,
        "limit_up_last_time": 144500,
        "chg20": 25.0,
        "pos60": 80.0,
    })
    return card


def test_soft_floor_engages_when_top_above_market_line_but_per_card_penalty_blocks():
    """T12 回归：gap<=0 时 soft floor 也应启用。

    场景：弱势·活跃 market_eff=77，top=82 已过市场线，但涨停执行惩罚 +7
    把单票有效线抬到 84 → 全部 blocked；旧条件 `0 < gap <= 20` 因 gap=-5
    永不触发 soft floor → 全场 0 推荐。修复后应放行 top。

    硬否决（panic/弱势×chg20/震荡×pos60）仍跳过，本测无硬否决。
    """
    snapshot = {
        "sample_count": 5000,
        "up_count": 1500,
        "down_count": 3400,
        "limit_up_count": 40,   # 0.8%
        "limit_down_count": 20,  # lu/ld=2 ≥1.2 → 活跃（T28）；非恐慌
        "breadth": {"regime": "弱势", "up_ratio": 0.30},
    }
    ctx = resolve_score_line_context(72, snapshot)
    assert ctx["effective_score_line"] == 77.0
    assert ctx["market_base"] == "弱势"

    cards = [
        _limit_up_risky_card("000001", 82.0),
        _limit_up_risky_card("000002", 80.0),
        _limit_up_risky_card("000003", 79.0),
    ]
    # 验证 per-card 有效线确实 > top，且 top 过市场线（复现 gap<=0）
    # 先跑一次拿 meta（会被 soft floor 修改），所以用副本断言 pre-soft 状态无必要——
    # 直接断言修复后输出非空 + soft_floor_reason=per_card_penalty
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot,
    )
    assert [c["code"] for c in result] == ["000001"], (
        f"gap<=0（top 过市场线但 per-card 执行惩罚挡光）应启用 soft floor 放行 top，"
        f"实际 rec={[c['code'] for c in result]}"
    )
    sm = result[0]["score_line_meta"]
    assert sm["dynamic_soft_floor_applied"] is True
    assert sm.get("soft_floor_reason") == "per_card_penalty"
    assert sm["execution_adjustment"] > 0
    # soft floor 把有效线压到 top-1，top 必须过线
    assert sm["effective_score_line"] <= 82.0
    assert sm["passed"] is True


def test_soft_floor_gap_le_0_still_skips_hard_veto_panic():
    """对照：gap<=0 修复不得放松 panic 硬否决。

    top 过市场线，但全员 panic_day_penalty → soft floor 触发后仍跳过硬否决票 → rec=0。
    """
    snapshot = {
        "sample_count": 4955,
        "up_count": 1685,
        "down_count": 3200,
        "limit_up_count": 68,
        "limit_down_count": 479,  # 恐慌
        "breadth": {"regime": "弱势", "up_ratio": 0.34},
    }
    # top=78 > market_eff=77 → gap=-1；但 panic +12 且硬跳过
    cards = [_card(code, score) for code, score in [("A", 78.0), ("B", 76.0), ("C", 75.0)]]
    for c in cards:
        c["chg20"] = 25.0  # 避免弱势×chg20 再加惩罚，专注 panic
        c["pos60"] = 80.0
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot,
    )
    assert result == [], "恐慌日硬否决不得被 gap<=0 soft floor 放行"
    for c in cards:
        assert c["score_line_meta"]["panic_day_penalty"] == 12.0


def test_soft_floor_gap_le_0_rescues_near_top_limit_up_pair():
    """两只涨停 risky 票：soft floor 按 dynamic_line=top-1 放行近顶票。

    top=82 → dynamic=81，82/80 过线、79 不过。验证 soft floor 不只放 top。
    """
    snapshot = {
        "sample_count": 5000,
        "up_count": 1500,
        "down_count": 3400,
        "limit_up_count": 40,
        "limit_down_count": 20,  # 活跃（lu/ld=2），非恐慌
        "breadth": {"regime": "弱势", "up_ratio": 0.30},
    }
    cards = [
        _limit_up_risky_card("300001", 82.0),
        _limit_up_risky_card("300002", 80.0),
        _limit_up_risky_card("300003", 79.0),
    ]
    result = apply_pool_rules(
        cards, output_count=6, score_line=72, market_snapshot=snapshot,
    )
    codes = [c["code"] for c in result]
    assert codes == ["300001"], (
        f"dynamic_line=top-1=81 只放行 82，80/79 仍低于 81；实际 {codes}"
    )
    assert result[0]["score_line_meta"].get("soft_floor_reason") == "per_card_penalty"


def test_apply_pool_rules_watchlist_excludes_blocked_cards():
    card = _card("Z", 88)
    # 一字涨停：open==high==low==close 且无开板，触发入场封死判定 → 不入观察池
    card["is_limit_up"] = True
    card["open"] = 11.0
    card["high"] = 11.0
    card["low"] = 11.0
    card["close"] = 11.0
    card["pct"] = 10.0
    card["limit_up_open_times"] = 0
    card["limit_up_seal_time"] = "093001"
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}
    out = apply_pool_rules([card], output_count=6, score_line=72, market_snapshot=snapshot, return_watchlist=True)
    assert all(c["code"] != "Z" for c in out["watchlist"])


def test_apply_pool_rules_watchlist_includes_multi_hit_despite_gap():
    card = _card("M", 70)  # 距线 70-91=-21，超出 -15 容差
    card["strategy_signals"] = [{"score": 80}, {"score": 75}, {"score": 70}, {"score": 65}]
    snapshot = {"sample_count": 100, "up_count": 72, "limit_up_count": 5, "limit_down_count": 0}
    out = apply_pool_rules([card], output_count=6, score_line=72, market_snapshot=snapshot, return_watchlist=True)
    assert any(c["code"] == "M" for c in out["watchlist"])



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


def test_apply_pool_rules_limit_up_exempt_from_support_ma5_penalty():
    """涨停股豁免 support_ma5 交叉惩罚。

    因果：support_ma5 惩罚针对"强势市已回落均线=追弱"，但涨停股=+10% 必然高于 MA5，
    support_ma5=True 是动能副产品而非追弱信号。limit-up-reseal-relay 历史因
    82(强牛高潮) + 8(强势) + 5(涨停执行风险) + 6(support_ma5) = 101 门槛拒绝 92 分候选，
    导致 0 产出。修复：涨停卡豁免 support_ma5 +6（执行风险+8 保留，是真实成交约束）。

    验证：相同高分数/support_ma5/regime，涨停卡 vs 普通卡的 effective_score_line
    差 6 分（普通卡 +6 惩罚，涨停卡豁免）。
    """
    snapshot = {
        "sample_count": 100, "up_count": 58, "down_count": 42,
        "limit_up_count": 5, "limit_down_count": 0,
        "market_regime": "强势",
    }
    # 高分确保两张卡都进入结果（涨停卡 effective=83+5+8=96，普通卡 +6=102）
    cards = [
        {**_card("LIMIT", 110), "support_ma5": True, "is_limit_up": True},
        {**_card("NORMAL", 110), "support_ma5": True, "is_limit_up": False},
    ]
    result = apply_pool_rules(cards, score_line=70, market_snapshot=snapshot)
    by_code = {c["code"]: c for c in result}
    # 涨停卡豁免 support_ma5 惩罚，普通卡仍 +6（核心断言）
    assert by_code["LIMIT"]["score_line_meta"]["regime_support_ma5_penalty"] == 0.0
    assert by_code["NORMAL"]["score_line_meta"]["regime_support_ma5_penalty"] == 6.0


def test_apply_pool_rules_oscillation_no_veto_plain_line():
    """震荡 regime：veto 关闭后按普通线评估（2026-08-02 适配）。

    原语义为震荡全局 hard veto（999 全拦）；用户决策关闭后，震荡日卡片按
    普通线评估。注：regime_oscillation_low_pos_penalty 当前仅有 meta 字段
    无惩罚实现（原由 veto 999 覆盖），此处锁定 veto 关闭后的真实行为。
    """
    snapshot = {
        "sample_count": 100, "up_count": 45, "down_count": 55,
        "limit_up_count": 1, "limit_down_count": 0,
        "market_regime": "震荡",
    }
    # veto 关闭：两张 80 分卡均按 72 线产出，无 veto 标记
    cards = [
        {**_card("LOW_POS", 80), "pos60": 40, "is_limit_up": False, "support_ma5": True},
        {**_card("HIGH_POS", 80), "pos60": 80, "is_limit_up": False, "support_ma5": True},
    ]
    result = apply_pool_rules(cards, score_line=72, market_snapshot=snapshot)
    by_code = {c["code"]: c for c in result}
    assert "LOW_POS" in by_code and "HIGH_POS" in by_code, "veto 关闭后震荡日应按线产出"
    assert by_code["LOW_POS"]["score_line_meta"]["regime_oscillation_hard_veto"] is False
    assert by_code["LOW_POS"]["score_line_meta"]["regime_oscillation_low_pos_penalty"] == 0.0



def test_apply_pool_rules_oscillation_low_pos60_exemption():
    """弱势 regime × pos60<50 不触发惩罚（超跌反弹在弱势有效，WR=100%）。

    pos60<50 效果是 regime-dependent：弱势超跌反弹有效，震荡超跌继续跌。
    惩罚只在震荡 regime 触发，弱势/结构性强势不触发。
    """
    snapshot_weak = {
        "sample_count": 100, "up_count": 30, "down_count": 70,
        "limit_up_count": 1, "limit_down_count": 2,
        "market_regime": "弱势",
    }
    cards = [{**_card("LOW_POS_WEAK", 72), "pos60": 40, "chg20": 20, "is_limit_up": False, "support_ma5": True}]
    result = apply_pool_rules(cards, score_line=72, market_snapshot=snapshot_weak)
    by_code = {c["code"]: c for c in result}
    # 弱势 regime 不触发震荡低 pos 惩罚
    assert "LOW_POS_WEAK" in by_code, "弱势 × pos60<50 不应被震荡惩罚过滤"
    assert by_code["LOW_POS_WEAK"]["score_line_meta"]["regime_oscillation_low_pos_penalty"] == 0.0


def test_apply_pool_rules_weak_low_chg20_penalty():
    """弱势 regime × chg20<15 交叉惩罚（+12 门槛）。

    第150轮 T3 失败归因：弱势 chg20<15 WR=33.3%（33笔 avg=-0.58%），
    chg20>=15 WR=65.5%（55笔 avg=2.10%），五档完全单调（33/33/56/61/76%）。
    因果：弱势资金抱团强势龙头，半强股无资金关注继续跌。
    涨停股豁免（limit-up-reseal-relay 策略靠封板动能不靠 chg20）。
    门槛 +12（72→84）模拟整体 WR 56.6%→63.1%。
    """
    snapshot = {
        "sample_count": 100, "up_count": 30, "down_count": 70,
        "limit_up_count": 1, "limit_down_count": 2,
        "market_regime": "弱势",
    }
    # 三张卡同分 72，弱势 regime：低 chg20 / 高 chg20 / 涨停低 chg20
    cards = [
        {**_card("LOW_CHG", 72), "chg20": 8, "is_limit_up": False, "support_ma5": True},
        {**_card("HIGH_CHG", 72), "chg20": 25, "is_limit_up": False, "support_ma5": True},
        {**_card("LIMIT_LOW_CHG", 72), "chg20": -5, "is_limit_up": True, "support_ma5": True},
    ]
    result = apply_pool_rules(cards, score_line=72, market_snapshot=snapshot)
    by_code = {c["code"]: c for c in result}
    # 高 chg20 通过，低 chg20 被过滤（+12 门槛→effective 84 > 72），涨停低 chg20 豁免通过
    assert "HIGH_CHG" in by_code, "弱势 × chg20>=15 应通过"
    assert "LOW_CHG" not in by_code, "弱势 × chg20<15 应被 +12 门槛过滤"
    assert "LIMIT_LOW_CHG" in by_code, "涨停股豁免弱势低 chg20 惩罚"
    assert by_code["HIGH_CHG"]["score_line_meta"]["regime_weak_low_chg20_penalty"] == 0.0
    assert by_code["LIMIT_LOW_CHG"]["score_line_meta"]["regime_weak_low_chg20_penalty"] == 0.0


def test_apply_pool_rules_oscillation_no_veto_plain_line_high_score():
    """震荡 regime：veto 关闭后按普通线评估（2026-08-02 适配）。

    原语义为震荡全局 hard veto（999 全拦）；用户决策关闭后，震荡日卡片按
    普通线评估——pos60>=50 且无其他惩罚的高分卡正常产出。
    """
    snapshot_oscillation = {
        "sample_count": 100, "up_count": 45, "down_count": 55,
        "limit_up_count": 1, "limit_down_count": 0,
        "market_regime": "震荡",
    }
    # veto 关闭：80 分卡过 72 线产出，且无 veto 标记
    cards = [{**_card("LOW_CHG_OSC", 80), "pos60": 60, "chg20": 8, "is_limit_up": False, "support_ma5": True}]
    result = apply_pool_rules(cards, score_line=72, market_snapshot=snapshot_oscillation)
    by_code = {c["code"]: c for c in result}
    assert "LOW_CHG_OSC" in by_code, "震荡日 veto 关闭后应按线产出"
    assert by_code["LOW_CHG_OSC"]["score_line_meta"]["regime_oscillation_hard_veto"] is False


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


def test_apply_sector_diversity_penalizes_same_industry():
    """回归测试：板块去重惩罚——同行业第2只扣5、第3+只扣10、第6只硬剔。

    防止未来误删 _apply_sector_diversity 或改坏常量。
    因果机制：同行业股票高度相关，集中持有没有分散效果。
    """
    from app.engine.aggregator import _apply_sector_diversity, _MAX_PER_INDUSTRY

    # 6 笔元器件 + 1 笔半导体
    cards = [
        {"code": f"00{i}", "industry": "元器件", "final_score": 85.0 - i * 2, "feature_snapshot": {}}
        for i in range(1, 7)  # 6 笔元器件
    ]
    cards.append({"code": "007", "industry": "半导体", "final_score": 70.0, "feature_snapshot": {}})

    result = _apply_sector_diversity(cards)

    # 元器件和半导体同属"科技硬件"关联组，合计 7 只，_MAX_PER_INDUSTRY=5 → 保留 5 只
    # 第 6 只元器件（科技硬件第6只）和半导体（科技硬件第7只）都被硬剔
    yuanjian = [c for c in result if c["industry"] == "元器件"]
    assert len(yuanjian) == _MAX_PER_INDUSTRY, f"元器件应保留 {_MAX_PER_INDUSTRY} 只（第6只硬剔），实际 {len(yuanjian)}"

    # 半导体被硬剔（科技硬件组第7只，count > _MAX_PER_INDUSTRY）
    semiconductor = [c for c in result if c["industry"] == "半导体"]
    assert len(semiconductor) == 0, f"半导体属于科技硬件组第7只，应被硬剔，实际保留 {len(semiconductor)}"

    # 元器件第1只无 penalty，第2只扣5，第3+只扣10
    yuanjian_sorted = sorted(yuanjian, key=lambda c: -c["final_score"])
    # 按 penalty 分组验证
    penalties = sorted(c.get("sector_diversity_penalty", 0) for c in yuanjian)
    assert penalties == [0, 5, 10, 10, 10], f"penalty 应为 [0,5,10,10,10]，实际 {penalties}"

    # feature_snapshot 双写
    assert yuanjian_sorted[1]["feature_snapshot"]["sector_diversity_penalty"] == 5
    # 分数口径必须可追溯：selection_score 用于过线，portfolio_rank_score 用于组合排序。
    penalized = next(c for c in yuanjian if c.get("sector_diversity_penalty") == 5)
    assert penalized["final_score"] == penalized["selection_score"]
    assert penalized["selection_score"] == penalized["portfolio_rank_score"] + 5
    assert penalized["feature_snapshot"]["selection_score"] == penalized["selection_score"]
    assert penalized["feature_snapshot"]["portfolio_rank_score"] == penalized["portfolio_rank_score"]


def test_apply_sector_diversity_skips_unknown_industry():
    """无 industry 的卡片不参与去重，直接保留无 penalty。"""
    from app.engine.aggregator import _apply_sector_diversity

    cards = [
        {"code": "001", "industry": "", "final_score": 80.0, "feature_snapshot": {}},
        {"code": "002", "industry": "unknown", "final_score": 75.0, "feature_snapshot": {}},
        {"code": "003", "industry": "半导体", "final_score": 70.0, "feature_snapshot": {}},
    ]
    result = _apply_sector_diversity(cards)
    assert len(result) == 3
    for c in result:
        assert c.get("sector_diversity_penalty", 0) == 0



def test_apply_pool_rules_respects_same_day_industry_counts_across_runs():
    """同日其他 formal run 已占满行业组时，本 run 不得再把同组股票放进正式推荐。"""
    cards = [
        {
            **_card("688001", 92),
            "industry": "半导体",
            "feature_snapshot": {},
        },
        {
            **_card("002001", 90),
            "industry": "元器件",
            "feature_snapshot": {},
        },
        {
            **_card("600001", 88),
            "industry": "白酒",
            "feature_snapshot": {},
        },
    ]

    result = apply_pool_rules(
        cards,
        output_count=10,
        score_line=70,
        market_snapshot=_neutral_snapshot(),
        same_day_industry_counts={"科技硬件": 4},
    )

    assert [card["code"] for card in result] == ["600001", "688001"]
    kept_hw = next(card for card in result if card["code"] == "688001")
    assert kept_hw["sector_diversity_penalty"] == 10.0
    assert all(card["code"] != "002001" for card in result)


# ---------------------------------------------------------------------------
# LLM 可用性与规则选股解耦
# ---------------------------------------------------------------------------

def _climax_snapshot():
    """强牛·高潮市场快照（up_ratio>=0.65 → 强牛；涨停率>阈值 → 高潮）。"""
    return {
        "sample_count": 4947,
        "up_count": 3337,
        "down_count": 1531,
        "limit_up_count": 149,
        "limit_down_count": 42,
    }


def _degraded_card(code, final_score):
    """模拟 LLM Skill Agent 暂不可用走回退的候选卡。"""
    return {
        "code": code,
        "name": f"股票{code}",
        "final_score": final_score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "strategy_signals": [
            {"strategy_id": "s1", "agent_review_unavailable": True, "runtime": "agent_skill_fallback_prelim"}
        ],
        "review_status": "degraded",
    }


def _normal_card(code, final_score):
    """LLM 正常审查的候选卡。"""
    return {
        "code": code,
        "name": f"股票{code}",
        "final_score": final_score,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
        "strategy_signals": [
            {"strategy_id": "s1", "agent_skill_review": {"stance": "support", "score": 75}}
        ],
    }


def test_agent_unavailable_climax_matches_reviewed_rule_selection():
    """同一规则分数下，Agent 是否可用不得改变高潮期正式推荐。"""
    from app.engine.aggregator import apply_pool_rules

    degraded_cards = [
        _degraded_card("600001", 67.85),
        _degraded_card("600002", 60.78),
        _degraded_card("600003", 59.92),
        _degraded_card("600004", 59.89),
    ]
    reviewed_cards = [
        _normal_card(card["code"], card["final_score"])
        for card in degraded_cards
    ]
    degraded_out = apply_pool_rules(
        degraded_cards,
        score_line=72.0,
        market_snapshot=_climax_snapshot(),
        return_watchlist=True,
    )
    reviewed_out = apply_pool_rules(
        reviewed_cards,
        score_line=72.0,
        market_snapshot=_climax_snapshot(),
        return_watchlist=True,
    )
    assert [c["code"] for c in degraded_out["recommendations"]] == [
        c["code"] for c in reviewed_out["recommendations"]
    ]
    assert [c["code"] for c in degraded_out["watchlist"]] == [
        c["code"] for c in reviewed_out["watchlist"]
    ]
    assert [c["code"] for c in degraded_out["degraded_watchlist"]] == [
        "600001", "600002", "600003", "600004",
    ]


def test_agent_unavailable_non_climax_keeps_rule_recommendation_flow():
    """非高潮阶段也不能因 LLM 全失败而阻断规则推荐。"""
    from app.engine.aggregator import apply_pool_rules

    cards = [
        _degraded_card("600001", 68.0),
        _degraded_card("600002", 60.0),
    ]
    # 弱势·平淡：允许 soft floor（非 震荡 / 非 强牛·高潮 禁区）
    weak_flat = {
        "sample_count": 100, "up_count": 25, "limit_up_count": 0, "limit_down_count": 2,
    }
    out = apply_pool_rules(
        cards,
        score_line=72.0,
        market_snapshot=weak_flat,
        return_watchlist=True,
    )
    recs = out["recommendations"] if isinstance(out, dict) else out
    assert [c["code"] for c in recs] == ["600001"]
    assert [c["code"] for c in out["degraded_watchlist"]] == ["600002"]


def test_climax_risk_control_is_preserved_for_reviewed_cards():
    """强牛·高潮阶段按规则保持严格，不依赖 LLM 是否可用。"""
    from app.engine.aggregator import apply_pool_rules

    cards = [
        _normal_card("600001", 60.0),
        _normal_card("600002", 55.0),
    ]
    out = apply_pool_rules(
        cards,
        score_line=72.0,
        market_snapshot=_climax_snapshot(),
        return_watchlist=True,
    )
    recs = out["recommendations"] if isinstance(out, dict) else out
    assert len(recs) == 0, "强牛·高潮阶段不应降线兜底"


def test_no_penalty_inflation_on_agent_unavailable():
    """agent_unavailable_fallback 不再抬高分数线（reason 保留但 evidence_adjustment 不加 8.5）。"""
    from app.engine.aggregator import _card_score_line_adjustment

    card = _degraded_card("600001", 67.85)
    adj = _card_score_line_adjustment(card)
    assert "agent_unavailable_fallback" in adj["reasons"]
    assert adj["evidence_adjustment"] < 8.5, "LLM 暂不可用不应再抬高有效线"


# ---------------------------------------------------------------------------
# 跨 run 行业集中控制：same_day_industry_counts 基数必须正确计入，否则
# _apply_sector_diversity 的硬上限失效（2026-07-09 半导体板块退潮 D 项失败归因）
# ---------------------------------------------------------------------------

def _industry_card(code: str, industry: str, final_score: float):
    """带 industry 字段的测试卡片（_card 默认无 industry，不参与 sector diversity）。"""
    return {
        "code": code,
        "final_score": final_score,
        "industry": industry,
        "evidence_quality_score": 92.0,
        "data_completeness_score": 100.0,
        "data_authenticity": 1.0,
        "data_quality": {"missing_fields": []},
    }


def test_apply_pool_rules_same_day_industry_base_caps_concentration():
    """同日跨 run 基数正确时，本 run 推第 6 只同行业股应被硬剔除。

    因果：2026-07-09 06:30 run start 时 01:39 run 已落库 5 只科技硬件，
    若 same_day_industry_counts 基数正确计入（=5），本 run 第 1 只半导体
    count=6 > _MAX_PER_INDUSTRY(5) 应被剔除 → 0 只半导体输出。
    旧 bug：基数漏算（=0）时 count 1/2/3 < 5 全保留 → 跨 run 累积 26 只科技硬件。
    """
    cards = [
        _industry_card("300136", "元器件", 75.0),
        _industry_card("301308", "半导体", 75.0),
        _industry_card("600460", "半导体", 75.0),
    ]
    result = apply_pool_rules(
        cards,
        output_count=6,
        score_line=72.0,
        market_snapshot=_neutral_snapshot(),
        same_day_industry_counts={"科技硬件": 5},
    )
    # 基数 5 → 第 1 只半导体 count=6 > 5 全部剔除（含软降线兜底后再过 sector diversity）
    assert result == [], (
        "同日基数 5 时本 run 半导体应被 sector diversity 硬剔除，避免跨 run 累积集中"
    )


def test_apply_pool_rules_zero_base_lets_single_run_industry_through():
    """基数 0（无跨 run 前序）时，单 run 内前 5 只同行业股应保留（_MAX_PER_INDUSTRY=5）。

    对照组：基数 0 是合法的（当日首个 formal run），本 run 推 3 只半导体 < 5 应保留。
    避免修复过度收紧导致单 run 正常推荐被误杀。
    """
    cards = [
        _industry_card("300136", "元器件", 75.0),
        _industry_card("301308", "半导体", 75.0),
        _industry_card("600460", "半导体", 75.0),
    ]
    result = apply_pool_rules(
        cards,
        output_count=6,
        score_line=72.0,
        market_snapshot=_neutral_snapshot(),
        same_day_industry_counts={},
    )
    codes = [c["code"] for c in result]
    # 基数 0 → 3 只 count 1/2/3 < 5 全保留（元器件+半导体同属科技硬件组）
    assert set(codes) == {"300136", "301308", "600460"}

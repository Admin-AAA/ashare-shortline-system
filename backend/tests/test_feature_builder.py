"""Tests for 基础短线策略 scoring, risk scoring, shared factors, and authenticity."""
from app.services.feature_builder import (
    TREND_SCORE_MAP,
    VOLUME_SCORE_MAP,
    MACD_SCORE_MAP,
    _bias_score,
    _compute_authenticity,
    _risk_score,
    _rsi_score,
    _tag_features,
    build_features,
    market_regime_score,
)
from app.services.factors import default_registry, iter_explainable_factors


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _stock(code="600519", name="测试", price=100.0, **kwargs):
    defaults = {
        "code": code,
        "name": name,
        "price": price,
        "close": price,
        "pct": 1.5,
        "industry": "白酒",
        "amount_yi": 5.0,
        "turnover_rate": 3.0,
        "chg20": 12.0,
        "dist20": 6.0,
        "pos20": 60,
        "eff20": 0.5,
        "breakout20": True,
        "vol5_20": 1.2,
        "max_dd20": -8.0,
        "upper_shadow_count20": 2,
        "announcement_negative_count": 0,
        "news_risk_count": 0,
    }
    defaults.update(kwargs)
    defaults.setdefault("dsa_profile", {})
    return defaults


def _snapshot(**kwargs):
    defaults = {
        "sample_count": 300,
        "up_count": 180,
        "down_count": 100,
        "limit_up_count": 12,
        "limit_down_count": 3,
        "main_industries": ["白酒", "电子"],
        "trade_date": "2026-05-12",
        "breadth": {"regime": "多头"},
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# market_regime_score
# ---------------------------------------------------------------------------

def test_market_regime_score_bullish():
    snap = _snapshot(up_count=200, sample_count=300)
    # up_ratio = 200/300 ≈ 0.67 → >= 0.65 → 强牛不友好 → 32.0
    assert market_regime_score(snap) == 32.0


def test_market_regime_score_neutral():
    snap = _snapshot(up_count=150, sample_count=300)
    # up_ratio = 0.5 → 0.50-0.65 偏多不友好 → 42.0
    assert market_regime_score(snap) == 42.0


def test_market_regime_score_bearish():
    snap = _snapshot(up_count=30, sample_count=300)
    # up_ratio = 0.1 → < 0.35 弱势友好 → 82.0
    assert market_regime_score(snap) == 82.0


def test_market_regime_score_small_sample():
    snap = _snapshot(sample_count=15)
    score = market_regime_score(snap)
    assert score < 55.0


def test_market_regime_score_range_friendly():
    # 0.35-0.50 震荡市场友好 → 72.0
    snap = _snapshot(up_count=120, sample_count=300)  # up_ratio = 0.4
    assert market_regime_score(snap) == 72.0


# ---------------------------------------------------------------------------
# 基础短线策略 scoring
# ---------------------------------------------------------------------------

def test_dsa_trend_score_bullish():
    # 多头排列 (MA5 > MA10 > MA20 with dist20 <= 10 or eff20 <= 0.5)
    item = _stock(price=100, dist20=6, eff20=0.4, chg20=12, pct=1.5)
    # MA from bias... but default bias_ma5 will compute from dist20/pct
    item["dsa_profile"] = {"bias_ma5": 1.0, "bias_ma10": 3.0, "bias_ma20": 6.0,
                           "ma5": 99, "ma10": 97, "ma20": 94}
    features = build_features([item], _snapshot())
    stock = features[0]
    assert stock["dsa_trend_score"] == TREND_SCORE_MAP.get(stock["trend_status"], 12)


def test_dsa_bias_score_optimal():
    # 数据驱动（2026-07-04 + 反事实验证 T2 第77轮）：
    # bias > 5: WR=77.8-100% → 20 分
    # 3 < bias <= 5: WR=66.7% → 18 分
    # 1.5 < bias <= 3: WR=59.0% → 16 分
    # -2 <= bias <= 1.5: 被推荐后 WR=63.3%（安全区间）→ 20 分（保持旧值，降分误杀盈利股）
    assert _bias_score(6.0) == 20  # bias > 5 强势
    assert _bias_score(4.0) == 18  # 3 < bias <= 5
    assert _bias_score(2.0) == 16  # 1.5 < bias <= 3
    assert _bias_score(0.0) == 20  # -2 <= bias <= 1.5（被推荐后 WR=63.3%，保持20分）
    assert _bias_score(-1.0) == 20


def test_dsa_bias_score_far():
    # 旧逻辑错误：bias>8 给 2 分（实际 WR=100%）；新逻辑给 20 分
    assert _bias_score(9.0) == 20  # bias > 5 强势股
    assert _bias_score(-6.0) == 8  # -7 <= bias < -2 弱势


def test_dsa_volume_score_surge_up():
    item = _stock(price=100, pct=3.0, vol5_20=1.8)
    item["dsa_profile"] = {"volume_ratio_5d": 1.8}
    features = build_features([item], _snapshot())
    stock = features[0]
    # vol_ratio >= 1.5 & pct > 0 → 放量上涨 → 12
    assert stock["volume_status"] == "放量上涨"
    assert stock["dsa_volume_score"] == 12


def test_dsa_macd_score_golden_cross():
    item = _stock(price=100, breakout20=True, chg20=15, pct=2.0)
    item["dsa_profile"] = {"macd_status": "零轴上金叉", "macd_dif": 0.5, "macd_dea": 0.3, "macd_bar": 0.4}
    features = build_features([item], _snapshot())
    stock = features[0]
    assert stock["macd_status"] == "零轴上金叉"
    assert stock["dsa_macd_score"] == 15


def test_dsa_macd_score_data_driven():
    # 数据驱动（T3 第80轮）：多头/下穿零轴被推荐后 WR 高，提分；空头 WR=0% 降分
    assert MACD_SCORE_MAP["零轴上金叉"] == 15
    assert MACD_SCORE_MAP["金叉"] == 12
    assert MACD_SCORE_MAP["多头"] == 12  # 提分（原8）：被推荐后 WR=100%
    assert MACD_SCORE_MAP["下穿零轴"] == 5  # 提分（原0）：被推荐后 WR=75%
    assert MACD_SCORE_MAP["空头"] == 0  # 降分（原2）：WR=0%
    assert MACD_SCORE_MAP["死叉"] == 0


def test_dsa_rsi_score():
    # 数据驱动（T3 第78轮）：rsi[40,50) 提分到 10（被推荐后 WR=100%）
    assert _rsi_score(55) == 8   # [50,70]
    assert _rsi_score(45) == 10  # [40,50) 提分（原 5）：被推荐后 WR=100%
    assert _rsi_score(25) == 10  # < 30
    assert _rsi_score(75) == 0   # > 70


def test_rsi_zero_pollution_guard():
    """RSI=0 是上游数据污染（stk_factor 缺失被 _f 转 0），feature_builder 必须清洗。

    2026-07-07 Tushare stk_factor 批量返回空 RSI，daily_kbars 写入 0.0。
    rsi_12 原有 <1 or >99 → 50.0 守卫；rsi_6/rsi_24 曾缺守卫保留 0.0，污染快照。
    本测验证三者一致清洗：0.0 不应出现在最终 item 的 rsi_* 字段。
    """
    from app.services.feature_builder import _apply_dsa_context

    item = _stock(price=100.0)
    # profile 里 rsi 全为 0.0（数据污染指纹）
    profile = {"rsi_6": 0.0, "rsi_12": 0.0, "rsi_24": 0.0, "bias_ma5": 1.0}
    _apply_dsa_context(item, profile, {})
    # rsi_12=0 → 守卫清洗为 50.0；rsi_6/rsi_24=0 → 用基于 rsi_12 的默认值
    assert item["rsi_12"] == 50.0
    assert 1 <= item["rsi_6"] <= 99, f"rsi_6 应被清洗出 [1,99]，实际 {item['rsi_6']}"
    assert 1 <= item["rsi_24"] <= 99, f"rsi_24 应被清洗出 [1,99]，实际 {item['rsi_24']}"
    assert item["rsi_6"] != 0.0 and item["rsi_24"] != 0.0

    # 对照：正常 rsi 值不被清洗
    item2 = _stock(price=100.0)
    profile2 = {"rsi_6": 65.0, "rsi_12": 60.0, "rsi_24": 55.0, "bias_ma5": 1.0}
    _apply_dsa_context(item2, profile2, {})
    assert item2["rsi_6"] == 65.0
    assert item2["rsi_12"] == 60.0
    assert item2["rsi_24"] == 55.0


def test_dsa_support_score():
    item = _stock(price=100, dist20=6, pct=1.5)
    item["dsa_profile"] = {"bias_ma5": 1.0, "bias_ma10": 2.0, "ma5": 99, "ma10": 98, "ma20": 94}
    features = build_features([item], _snapshot())
    stock = features[0]
    # support_ma5: bias_ma5 in [-2,2] → True
    # support_ma10 may also be True
    assert stock["dsa_support_score"] >= 5


def test_dsa_signal_score_weighted():
    item = _stock(price=100, dist20=6, breakout20=True, chg20=15, pct=2.0, vol5_20=1.8)
    item["dsa_profile"] = {"bias_ma5": 1.0, "bias_ma10": 3.0, "bias_ma20": 6.0,
                           "ma5": 99, "ma10": 97, "ma20": 94,
                           "volume_ratio_5d": 1.8}
    features = build_features([item], _snapshot())
    stock = features[0]
    assert "dsa_signal_score" in stock
    assert stock["dsa_signal_score"] > 0


def test_dsa_momentum_decay():
    item = _stock(price=100, dist20=6, breakout20=True, chg20=15, pct=2.0, vol5_20=1.0)
    item["dsa_profile"] = {"bias_ma5": 1.0, "bias_ma10": 3.0, "bias_ma20": 6.0,
                           "ma5": 99, "ma10": 97, "ma20": 94}
    item["days_since_golden_cross"] = 15  # > 10 triggers decay
    features_no_decay = build_features([dict(item, days_since_golden_cross=0)], _snapshot())
    features_decay = build_features([item], _snapshot())
    assert features_decay[0]["dsa_signal_score"] < features_no_decay[0]["dsa_signal_score"]


def test_pos20_bias_ma5_scoring_rules():
    """Test that pos20 80-95 and bias_ma5 0-3 rules boost score."""
    # Stock with pos20=85 (80-95 range) and bias_ma5=2 (0-3 range)
    item_good = _stock(price=100, dist20=6, pct=2.0, vol5_20=1.0,
                        pos20=85, bias_ma5=2.0, avg_cost=120)
    item_good["dsa_profile"] = {"bias_ma5": 2.0, "bias_ma10": 4.0, "bias_ma20": 7.0,
                                 "ma5": 98, "ma10": 96, "ma20": 93}
    # Stock with pos20=50 (not in 80-95) and bias_ma5=8 (not in 0-3)
    item_bad = _stock(price=100, dist20=6, pct=2.0, vol5_20=1.0,
                       pos20=50, bias_ma5=8.0, avg_cost=120)
    item_bad["dsa_profile"] = {"bias_ma5": 8.0, "bias_ma10": 5.0, "bias_ma20": 7.0,
                                "ma5": 92, "ma10": 95, "ma20": 93}
    features_good = build_features([item_good], _snapshot())
    features_bad = build_features([item_bad], _snapshot())
    assert features_good[0]["dsa_signal_score"] > features_bad[0]["dsa_signal_score"]


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

def test_risk_score_news():
    item = _stock(news_risk_count=1)
    assert _risk_score(item) >= 25


def test_risk_score_announcement():
    item = _stock(announcement_negative_count=2)
    assert _risk_score(item) >= 35


def test_risk_score_turnover():
    item = _stock(turnover_rate=15)
    assert _risk_score(item) >= 20


def test_risk_score_max_cap():
    item = _stock(news_risk_count=3, announcement_negative_count=2, turnover_rate=20, max_dd20=-20,
                  upper_shadow_count20=6, dsa_bias_score=2, dsa_macd_score=0)
    assert _risk_score(item) <= 100.0


def test_risk_score_macd_weak():
    item = _stock(dsa_macd_score=0)
    assert _risk_score(item) >= 12


# ---------------------------------------------------------------------------
# Shared shortline factors
# ---------------------------------------------------------------------------

def test_shared_shortline_factor_specs_are_explainable():
    # 解释规格已迁入因子 metadata；逐个可解释因子校验：描述文本复用 metadata，
    # fields / value_field / score_field 由解释规格声明。
    explainable = iter_explainable_factors(default_registry)
    assert explainable, "应至少有一个声明了解释规格的因子"
    for factor, spec in explainable:
        assert spec["fields"]
        assert spec["value_field"]
        assert spec["score_field"]
        assert factor.metadata["logic"]
        assert factor.metadata["data_sources"]
        assert factor.metadata["degradation"]


def test_feature_builder_does_not_emit_buy_decision_fields():
    stock = build_features([_stock()], _snapshot())[0]

    assert "buy_signal" not in stock
    assert "signal_reasons" not in stock
    assert "risk_factors" not in stock
    assert "suggested_position" not in stock
    assert "operation_advice" not in stock


def test_limit_up_overheat_factor_has_spec_and_degrades_from_snapshot():
    stock = _stock(
        is_limit_up=True,
        limit_up_consecutive_days=5,
        limit_up_open_times=9,
        pct=10.0,
        pos60=98,
        dist60=42,
    )

    enriched = build_features([stock], _snapshot(sample_count=300, limit_up_count=28, limit_down_count=8))[0]
    spec = enriched["shortline_factor_explanations"]["limit_up_overheat"]

    limit_up_spec = next(
        s for _, s in iter_explainable_factors(default_registry) if s["key"] == "limit_up_overheat"
    )
    assert enriched["limit_up_overheat_score"] >= 70
    assert enriched["limit_up_overheat_level"] == "高"
    assert spec["fields"] == limit_up_spec["fields"]
    assert spec["data_sources"]
    assert spec["degradation"]


def test_sector_ladder_factor_uses_shared_sector_pool_once():
    stocks = [
        _stock(code="000001", industry="机器人", is_limit_up=True, limit_up_consecutive_days=1, pct=10),
        _stock(code="000002", industry="机器人", is_limit_up=True, limit_up_consecutive_days=2, pct=10),
        _stock(code="000003", industry="机器人", is_limit_up=True, limit_up_consecutive_days=4, pct=10),
        _stock(code="000004", industry="机器人", pct=4),
    ]

    enriched = build_features(stocks, _snapshot(main_industries=["机器人"]))[0]
    spec = enriched["shortline_factor_explanations"]["sector_ladder"]

    assert enriched["sector_ladder_completeness_score"] >= 75
    assert enriched["sector_ladder_level"] == "完整"
    assert enriched["sector_ladder_limit_up_count"] == 3
    assert enriched["sector_ladder_height"] == 4
    assert spec["logic"]
    assert spec["degradation"]


def test_capital_resonance_factor_marks_source_and_fallback():
    with_flow = _stock(
        main_net_inflow_yi=2.2,
        main_net_inflow_ratio=9.5,
        super_large_net_inflow_yi=0.8,
        large_net_inflow_yi=0.4,
        small_net_inflow_yi=-0.5,
        sector_fund_rank=3,
        data_quality={"capital_flow_checked": True, "missing_fields": []},
    )
    fallback = _stock(
        code="000002",
        pct=3.5,
        vol5_20=1.8,
        amount_yi=6,
        data_quality={"missing_fields": ["capital_flow"]},
    )

    enriched = build_features([with_flow, fallback], _snapshot())

    assert enriched[0]["capital_resonance_signal"] == "主力共振"
    assert enriched[0]["capital_resonance_source"] == "capital_flow"
    assert enriched[1]["capital_resonance_source"] == "price_volume_fallback"
    assert enriched[1]["shortline_factor_explanations"]["capital_resonance"]["source"] == "price_volume_fallback"


def test_twenty_cm_risk_gate_identifies_high_volatility_board():
    stock = _stock(
        code="300001",
        market="创业板",
        pct=16,
        is_limit_up=True,
        turnover_rate=22,
        amount_yi=2.0,
        pos60=98,
        dist60=38,
    )

    enriched = build_features([stock], _snapshot())[0]
    spec = enriched["shortline_factor_explanations"]["twenty_cm_risk"]

    assert enriched["is_20cm_stock"] is True
    assert enriched["twenty_cm_risk_gate"] == "block"
    assert enriched["twenty_cm_risk_score"] >= 70
    assert "创业板" in enriched["twenty_cm_risk_reason"]
    assert spec["data_sources"]


# ---------------------------------------------------------------------------
# Feature tags & authenticity
# ---------------------------------------------------------------------------

def test_tag_features_observed():
    item = _stock(price=100.0, ma5=99.0, pct=1.5)
    _tag_features(item)
    tags = item.get("_feature_tags", {})
    assert tags.get("price") == "OBSERVED"
    assert tags.get("ma5") == "OBSERVED"


def test_tag_features_synthetic():
    item = _stock(concentration_90=13.5, revenue_yoy=12.0)
    _tag_features(item)
    tags = item.get("_feature_tags", {})
    # concentration_90 现已接入 cyq_perf 真实数据源（_OBSERVABLE_FIELDS），无缺失时标 OBSERVED
    assert tags.get("concentration_90") == "OBSERVED"
    # revenue_yoy is now OBSERVED (real financial data from fina_indicator)
    assert tags.get("revenue_yoy") == "OBSERVED"


def test_compute_authenticity_all_observed():
    item = {}
    # Fill item with OBSERVED fields
    for f in {"price", "pct", "amount_yi", "turnover_rate", "free_mv_yi", "total_mv_yi",
              "chg20", "chg60", "dist20", "dist60", "pos20", "pos60", "pos120", "eff20",
              "ma5", "ma10", "ma20", "ma60",
              "bias_ma5", "bias_ma10", "bias_ma20",
              "vol5_20", "volume_ratio_5d", "volume_ratio_vs_5d", "volume_ratio_vs_20d",
              "volume_status", "volume_trend", "volume_trend_pct",
              "volume_price_corr", "volume_pattern",
              "breakout20", "breakout60", "upper_shadow_count20", "max_dd20", "max_dd60",
              "rsi_6", "rsi_12", "rsi_24", "rsi_status", "rsi_signal",
              "macd_dif", "macd_dea", "macd_bar", "macd_status", "macd_signal",
              "trend_status", "ma_alignment", "trend_strength",
              "yesterday_high", "yesterday_low", "high_20d", "low_20d", "off_high_pct",
              "limit_up_days20", "is_st",
              "support_ma5", "support_ma10",
              "support_levels", "resistance_levels", "resistance_distance_pct",
              "current_price",
              "avg_up_day_volume", "avg_down_day_volume", "high_volume_days",
              "announcement_negative_count", "news_risk_count",
              "capital_flow_score", "fundamental_score", "intelligence_score",
              "chip_health_score",
              "dsa_trend_score", "dsa_bias_score", "dsa_volume_score",
              "dsa_support_score", "dsa_macd_score", "dsa_rsi_score",
              }:
        item[f] = 0
    _tag_features(item)
    auth = _compute_authenticity(item)
    # Almost all scored features are OBSERVED
    assert auth > 0.5


def test_build_features_with_snapshot():
    stocks = [_stock(), _stock(code="000001", name="平安银行", industry="银行")]
    snap = _snapshot()
    result = build_features(stocks, snap)
    assert len(result) == 2
    for stock in result:
        assert "dsa_signal_score" in stock
        assert "risk_score" in stock
        assert "shortline_factor_explanations" in stock
        assert "limit_up_overheat_score" in stock
        assert "sector_ladder_completeness_score" in stock
        assert "capital_resonance_score" in stock
        assert "twenty_cm_risk_gate" in stock
        assert "_feature_tags" in stock
        assert "data_authenticity" in stock


def test_shortline_enhancement_fields_survive_without_auxiliary_db():
    stocks = [
        _stock(
            code="002897",
            name="意华股份",
            industry="电子",
            pct=6.2,
            amount_yi=9.0,
            turnover_rate=5.5,
            free_mv_yi=90.0,
            chg20=18.0,
            breakout20=True,
            vol5_20=1.8,
            main_net_inflow_yi=1.8,
            main_net_inflow_ratio=9.0,
            super_large_net_inflow_yi=0.6,
            large_net_inflow_yi=0.3,
            small_net_inflow_yi=-0.4,
            sector_fund_rank=4,
            data_quality={"capital_flow_checked": True, "missing_fields": []},
        ),
        _stock(code="002001", industry="电子", is_limit_up=True, limit_up_consecutive_days=1, pct=10),
        _stock(code="002002", industry="电子", is_limit_up=True, limit_up_consecutive_days=2, pct=10),
        _stock(code="002003", industry="电子", is_limit_up=True, limit_up_consecutive_days=4, pct=10),
    ]
    enriched = build_features(stocks, _snapshot(main_industries=["电子"], limit_up_count=28))[0]

    assert enriched["is_main_theme"] is True
    assert enriched["is_shortline_leader_candidate"] is True
    assert enriched["sector_ladder_completeness_score"] >= 75
    assert enriched["capital_resonance_signal"] == "主力共振"
    assert enriched["market_sentiment_phase"] == "高潮"


def test_auxiliary_values_are_not_overwritten_by_dsa_defaults():
    stock = _stock(
        main_net_inflow_yi=1.6,
        inflow_5d_yi=3.2,
        news_risk_count=1,
        positive_catalyst_count=2,
        latest_news="真实公告标题",
        risk_alerts=["减持风险"],
        positive_catalysts=["订单增长"],
        dragon_tiger_is_on_list=True,
        dragon_tiger_recent_count=1,
    )

    enriched = build_features([stock], _snapshot())[0]

    assert enriched["main_net_inflow_yi"] == 1.6
    assert enriched["inflow_5d_yi"] == 3.2
    assert enriched["news_risk_count"] == 1
    assert enriched["positive_catalyst_count"] == 2
    assert enriched["latest_news"] == "真实公告标题"
    assert enriched["dragon_tiger_is_on_list"] is True
    assert enriched["dragon_tiger_recent_count"] == 1


def test_auxiliary_enrichment_carries_limit_up_execution_fields():
    from app.services.feature_builder import _enrich_with_auxiliary_data

    stock = _stock(code="601126", name="四方股份")
    aux = {
        "limit_up": {
            "601126": {
                "consecutive_days": 1,
                "first_time": "100643",
                "last_time": "135555",
                "open_times": 17,
                "up_stat": "1/1",
                "limit_amount_yi": 1.2,
                "fd_amount_yi": 0.318156,
                "turnover_ratio": 5.29,
            }
        },
        "concept_sectors": {},
        "capital_flows": {},
        "dragon_tigers": {},
        "news": {},
        "auctions": {},
        "pledges": {},
        "broker_recs": {},
        "share_floats_upcoming": {},
        "coverage": {},
    }

    _enrich_with_auxiliary_data([stock], None, _snapshot(), _aux_override=aux)

    assert stock["is_limit_up"] is True
    assert stock["limit_up_first_time"] == "100643"
    assert stock["limit_up_last_time"] == "135555"
    assert stock["limit_up_open_times"] == 17
    assert stock["limit_up_up_stat"] == "1/1"
    assert stock["limit_up_limit_amount_yi"] == 1.2
    assert stock["limit_up_fd_amount_yi"] == 0.318156
    assert stock["limit_up_turnover_ratio"] == 5.29


def test_auxiliary_enrichment_marks_checked_empty_as_covered():
    from app.services.feature_builder import _enrich_with_auxiliary_data

    stock = _stock(
        code="000001",
        name="平安银行",
        industry="银行",
        data_quality={
            "missing_fields": [
                "announcements",
                "capital_flow",
                "dragon_tiger",
                "main_net_inflow",
                "news",
                "theme_tags",
            ]
        },
    )
    aux = {
        "limit_up": {},
        "concept_sectors": {},
        "capital_flows": {
            "000001": {
                "main_net_inflow_yi": 0.12,
                "super_large_net_inflow_yi": 0.08,
                "large_net_inflow_yi": 0.04,
                "medium_net_inflow_yi": -0.01,
                "small_net_inflow_yi": -0.03,
                "main_net_inflow_ratio": 1.8,
                "cumulative_inflow_3d_yi": 0.2,
                "cumulative_inflow_5d_yi": 0.35,
                "cumulative_inflow_10d_yi": 0.5,
            }
        },
        "dragon_tigers": {},
        "news": {},
        "auctions": {},
        "pledges": {},
        "broker_recs": {},
        "share_floats_upcoming": {},
        "coverage": {
            "dragon_tiger_trade_date_covered": True,
            "dragon_tiger_trade_date_count": 80,
        },
    }
    snapshot = _snapshot(
        trade_date="2026-05-12",
        breadth={
            "auxiliary_data_audit": {
                "capital_flows": {"status": "cached", "checked_codes": ["000001"]},
                "news": {"status": "success", "checked_codes": ["000001"]},
                "dragon_tiger": {
                    "status": "cached",
                    "covered_trade_date": "2026-05-12",
                },
            }
        },
    )

    _enrich_with_auxiliary_data([stock], None, snapshot, _aux_override=aux)

    missing = set(stock["data_quality"]["missing_fields"])
    assert "capital_flow" not in missing
    assert "main_net_inflow" not in missing
    assert "dragon_tiger" not in missing
    assert "news" not in missing
    assert "announcements" not in missing
    assert "theme_tags" not in missing
    assert stock["dragon_tiger_is_on_list"] is False
    assert stock["dragon_tiger_recent_count"] == 0
    assert stock["data_quality"]["dragon_tiger_checked_empty"] is True
    assert stock["data_quality"]["news_checked_empty"] is True
    assert stock["data_quality"]["announcements_checked_empty"] is True
    assert stock["data_quality"]["capital_flow_checked"] is True
    assert stock["data_quality"]["theme_tags_fallback_from_industry"] is True
    assert stock["concept_sector_name"] == "银行"
    assert stock["theme_tags"] == ["银行"]


def test_auxiliary_enrichment_populates_theme_tags_from_concepts():
    from app.services.feature_builder import _enrich_with_auxiliary_data

    stock = _stock(
        code="000001",
        industry="银行",
        data_quality={"missing_fields": ["theme_tags"]},
    )
    aux = {
        "limit_up": {},
        "concept_sectors": {
            "000001": [
                {"sector_name": "跨境支付", "heat_rank": 3},
                {"sector_name": "金融科技", "heat_rank": 1},
            ]
        },
        "capital_flows": {},
        "dragon_tigers": {},
        "news": {},
        "auctions": {},
        "pledges": {},
        "broker_recs": {},
        "share_floats_upcoming": {},
        "coverage": {},
    }

    _enrich_with_auxiliary_data([stock], None, _snapshot(), _aux_override=aux)

    assert stock["concept_sector_name"] == "金融科技"
    assert stock["theme_tags"] == ["金融科技", "跨境支付"]
    assert "theme_tags" not in stock["data_quality"]["missing_fields"]
    assert stock["data_quality"]["theme_tags_checked"] is True

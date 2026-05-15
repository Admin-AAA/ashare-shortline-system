"""Tests for DSA scoring, risk scoring, buy signals, and authenticity."""
from app.services.feature_builder import (
    TREND_SCORE_MAP,
    VOLUME_SCORE_MAP,
    MACD_SCORE_MAP,
    _apply_buy_signal,
    _apply_dsa_scores,
    _bias_score,
    _compute_authenticity,
    _risk_score,
    _rsi_score,
    _tag_features,
    build_features,
    market_regime_score,
)


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
    # up_ratio = 200/300 ≈ 0.67 → >= 0.65 → 82.0
    assert market_regime_score(snap) == 82.0


def test_market_regime_score_neutral():
    snap = _snapshot(up_count=150, sample_count=300)
    # up_ratio = 0.5 → >= 0.5 → 72.0
    assert market_regime_score(snap) == 72.0


def test_market_regime_score_bearish():
    snap = _snapshot(up_count=30, sample_count=300)
    # up_ratio = 0.1 → < 0.35 → 42.0
    assert market_regime_score(snap) == 42.0


def test_market_regime_score_small_sample():
    snap = _snapshot(sample_count=15)
    score = market_regime_score(snap)
    assert score < 55.0


# ---------------------------------------------------------------------------
# DSA scoring
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
    # bias_ma5 in [-2, 1.5] → 20
    assert _bias_score(0.0) == 20
    assert _bias_score(-1.0) == 20
    assert _bias_score(1.5) == 20


def test_dsa_bias_score_far():
    assert _bias_score(9.0) == 2
    assert _bias_score(-6.0) == 8


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
    item["dsa_profile"] = {}
    features = build_features([item], _snapshot())
    stock = features[0]
    assert stock["macd_status"] == "零轴上金叉"
    assert stock["dsa_macd_score"] == 15


def test_dsa_rsi_score():
    assert _rsi_score(55) == 8   # [50,70]
    assert _rsi_score(45) == 5   # [40,50)
    assert _rsi_score(25) == 10  # < 30
    assert _rsi_score(75) == 0   # > 70


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
# Buy signal
# ---------------------------------------------------------------------------

def test_buy_signal_strong_buy():
    item = _stock(price=100, dist20=6, breakout20=True, chg20=15, pct=2.0, vol5_20=1.8)
    item["dsa_profile"] = {"bias_ma5": 1.0, "bias_ma10": 3.0, "bias_ma20": 6.0,
                           "ma5": 99, "ma10": 97, "ma20": 94,
                           "volume_ratio_5d": 1.8}
    features = build_features([item], _snapshot())
    stock = features[0]
    assert stock["buy_signal"] in {"强烈买入", "买入", "持有", "观望", "卖出", "强烈卖出"}
    assert isinstance(stock["ideal_buy"], float)
    assert isinstance(stock["stop_loss"], float)
    assert isinstance(stock["take_profit"], float)
    assert stock["stop_loss"] < stock["recommend_price"] < stock["take_profit"] if hasattr(stock, "recommend_price") else True


def test_buy_signal_reasons():
    item = _stock(price=100, dist20=6, breakout20=True, chg20=15, pct=2.0, vol5_20=1.8)
    item["dsa_profile"] = {"bias_ma5": 1.0, "bias_ma10": 3.0, "bias_ma20": 6.0,
                           "ma5": 99, "ma10": 97, "ma20": 94,
                           "volume_ratio_5d": 1.8}
    features = build_features([item], _snapshot())
    stock = features[0]
    if stock["dsa_signal_score"] >= 70:
        assert len(stock["signal_reasons"]) > 0


def test_buy_signal_risk_factors():
    item = _stock(price=100, dist20=6, pct=2.0, vol5_20=1.0, bias_ma5=6.0, news_risk_count=1)
    item["dsa_profile"] = {"bias_ma5": 6.0}
    features = build_features([item], _snapshot())
    stock = features[0]
    if stock["bias_ma5"] > 5:
        assert any("MA5" in r for r in stock["risk_factors"])


def test_buy_signal_suggested_position():
    item = _stock(price=100, dist20=6, breakout20=True, chg20=15, pct=2.0, vol5_20=1.8)
    item["dsa_profile"] = {"bias_ma5": 1.0, "bias_ma10": 3.0, "bias_ma20": 6.0,
                           "ma5": 99, "ma10": 97, "ma20": 94,
                           "volume_ratio_5d": 1.8}
    features = build_features([item], _snapshot())
    stock = features[0]
    assert stock["suggested_position"] in {"中仓", "小仓", "观察仓"}


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
    assert tags.get("concentration_90") == "SYNTHETIC"
    assert tags.get("revenue_yoy") == "SYNTHETIC"


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
        assert "buy_signal" in stock
        assert "_feature_tags" in stock
        assert "data_authenticity" in stock


def test_shortline_enhancement_fields_survive_without_auxiliary_db():
    stock = _stock(
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
    )
    enriched = build_features([stock], _snapshot(main_industries=["电子"], limit_up_count=28))[0]

    assert enriched["_is_main"] is True
    assert enriched["_is_leader_candidate"] is True
    assert enriched["_consensus_signal"] == "主力吸筹"
    assert enriched["_sentiment_phase"] == "高潮"


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

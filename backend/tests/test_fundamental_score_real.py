"""Tests for real fina_indicator data injection into fundamental_score.

验证 feature_builder 的基本面评分使用真实财报数据，而非硬编码常数。
"""
from app.services.feature_builder import (
    _fundamental_score,
    _apply_context_blocks,
    build_features,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _base_item(**kwargs):
    """构造一个 stock item，用于 _fundamental_score 单元测试。"""
    item = {
        "code": "600519",
        "name": "贵州茅台",
        "price": 1500.0,
        "close": 1500.0,
        "pct": 1.5,
        "industry": "白酒",
        "amount_yi": 30.0,
        "turnover_rate": 0.5,
        "is_main_theme": True,
    }
    item.update(kwargs)
    return item


def _context_ready_item(**kwargs):
    """补齐 _apply_context_blocks 依赖的前置字段（由 _apply_dsa_context 前半段设置）。"""
    profile = kwargs.pop("dsa_profile", {}) or {}
    item = {
        "code": "600519",
        "price": 1500.0,
        "close": 1500.0,
        "pct": 1.5,
        "industry": "白酒",
        "amount_yi": 30.0,
        "turnover_rate": 0.5,
        "is_main_theme": True,
        "data_source": "local_pool",
        "data_quality": {"missing_fields": [], "fallback_fields": []},
        # 前置字段（_apply_dsa_context 前半段会设置）
        "ma5": 1480.0, "ma10": 1470.0, "ma20": 1460.0, "ma60": 1450.0,
        "bias_ma5": 1.35, "bias_ma10": 2.04, "bias_ma20": 2.74,
        "support_ma5": True, "support_ma10": True,
        "volume_ratio_5d": 1.2, "volume_ratio_vs_20d": 1.2,
        "vol5_20": 1.2, "off_high_pct": -5.0,
    }
    item.update(kwargs)
    item["dsa_profile"] = profile
    return item, profile


# ---------------------------------------------------------------------------
# _fundamental_score：真实数据算分
# ---------------------------------------------------------------------------

def test_fundamental_score_uses_real_roe_and_growth():
    """有真实 ROE/毛利率/同比时，评分基于真实值，不是常数 11.5/28.0/12.0。"""
    item = _base_item(
        revenue_yoy=6.54,      # 600519 真实营收同比
        net_profit_yoy=1.47,   # 真实净利润同比
        roe=10.57,             # 真实 ROE
        gross_margin=89.76,    # 真实毛利率
    )
    # roe=10.57 < 12 → 不加 8；gross_margin=89.76 >= 25 → +5
    # revenue_yoy=6.54 不 >10 不 <0 → 中性；net_profit_yoy=1.47 不 >10 不 <-10 → 中性
    # 期望：55(base) + 0 + 0 + 0 + 5 = 60.0
    assert _fundamental_score(item) == 60.0


def test_fundamental_score_penalizes_decline():
    """净利润同比大幅下滑时扣分（真实负增长触发惩罚）。"""
    item = _base_item(
        revenue_yoy=-10.40,    # 000001 真实营收同比（负）
        net_profit_yoy=-4.21,  # 真实净利润同比（负但未达 -10）
        roe=8.15,
        gross_margin=None,     # 银行股无毛利率
    )
    # revenue_yoy=-10.40 < 0 → -10；net_profit_yoy=-4.21 不 < -10 → 中性
    # roe=8.15 < 12 → 不加；gross_margin None → _num(None)=0 不 >=25 → 不加
    # 期望：55 - 10 = 45.0
    assert _fundamental_score(item) == 45.0


def test_fundamental_score_rewards_high_growth():
    """高增长 + 高 ROE + 高毛利率时显著加分。"""
    item = _base_item(
        revenue_yoy=50.0,
        net_profit_yoy=60.0,
        roe=20.0,
        gross_margin=60.0,
    )
    # revenue_yoy>10 → +10；net_profit_yoy>10 → +12；roe>=12 → +8；gross_margin>=25 → +5
    # 期望：55 + 10 + 12 + 8 + 5 = 90.0
    assert _fundamental_score(item) == 90.0


# ---------------------------------------------------------------------------
# _fundamental_score：无数据时返回中性 55
# ---------------------------------------------------------------------------

def test_fundamental_score_neutral_when_all_fields_none():
    """无任何真实财务数据时，返回中性 55（诚实表达"无数据"）。"""
    item = _base_item(
        revenue_yoy=None,
        net_profit_yoy=None,
        roe=None,
        gross_margin=None,
    )
    assert _fundamental_score(item) == 55.0


def test_fundamental_score_neutral_when_fields_missing():
    """字段完全缺失时，同样返回中性 55。"""
    item = _base_item()
    assert _fundamental_score(item) == 55.0


# ---------------------------------------------------------------------------
# _apply_context_blocks：注入真实数据到 profile
# ---------------------------------------------------------------------------

def test_apply_context_blocks_reads_real_fina_from_profile():
    """profile 注入真实 fina 后，item 字段是真实值，不是常数 12.0/28.0/11.5。"""
    profile = {
        "revenue_yoy": 6.54,
        "net_profit_yoy": 1.47,
        "roe": 10.57,
        "gross_margin": 89.76,
        "pe_ttm": 25.0,
    }
    item, _ = _context_ready_item(dsa_profile=profile)
    _apply_context_blocks(item, profile)

    assert item["revenue_yoy"] == 6.54
    assert item["net_profit_yoy"] == 1.47
    assert item["roe"] == 10.57
    assert item["gross_margin"] == 89.76
    # 关键：不再是硬编码常数 12.0/10.0/11.5/28.0
    assert item["revenue_yoy"] != 12.0
    assert item["gross_margin"] != 28.0
    assert item["roe"] != 11.5


def test_apply_context_blocks_none_when_no_fina():
    """无 fina 注入时，item 字段为 None（不是常数），has_real_fundamental=False。"""
    item, profile = _context_ready_item(dsa_profile={})
    _apply_context_blocks(item, profile)

    assert item["revenue_yoy"] is None
    assert item["net_profit_yoy"] is None
    assert item["roe"] is None
    assert item["gross_margin"] is None
    assert item["has_real_fundamental"] is False
    # 无数据时基本面分为中性 55
    assert item["fundamental_score"] == 55.0


# ---------------------------------------------------------------------------
# build_features：端到端注入（monkeypatch _enrich_with_auxiliary_data）
# ---------------------------------------------------------------------------

def test_build_features_injects_real_fina(monkeypatch):
    """build_features 通过 monkeypatch 注入真实 fina，最终 fundamental_score 基于真实值。"""
    stock = {
        "code": "600519",
        "name": "贵州茅台",
        "price": 1500.0,
        "close": 1500.0,
        "pct": 1.5,
        "industry": "白酒",
        "amount_yi": 30.0,
        "turnover_rate": 0.5,
        "vol5_20": 1.2,
        "off_high_pct": -5.0,
        "breakout20": False,
        "max_dd20": -8.0,
        "upper_shadow_count20": 2,
        "announcement_negative_count": 0,
        "news_risk_count": 0,
        "pos20": 60,
        "eff20": 0.5,
        "chg20": 12.0,
        "dist20": 6.0,
        "dsa_profile": {},
    }
    snapshot = {
        "sample_count": 300,
        "up_count": 180,
        "down_count": 100,
        "limit_up_count": 12,
        "limit_down_count": 3,
        "main_industries": ["白酒"],
        "trade_date": "2026-07-05",
        "breadth": {"regime": "多头"},
    }
    fina_data = {
        "600519": {
            "roe": 10.57,
            "gross_margin": 89.76,
            "net_profit_yoy": 1.47,
            "revenue_yoy": 6.54,
            "end_date": "20260331",
            "ann_date": "20260425",
        }
    }

    from app.services import feature_builder

    def _fake_enrich(stocks, db, snap, _aux_override=None):
        for s in stocks:
            profile = s.get("dsa_profile", {}) or {}
            fina = fina_data.get(s["code"])
            if fina:
                profile["revenue_yoy"] = fina.get("revenue_yoy")
                profile["net_profit_yoy"] = fina.get("net_profit_yoy")
                profile["roe"] = fina.get("roe")
                profile["gross_margin"] = fina.get("gross_margin")
                s["dsa_profile"] = profile
                s["has_real_fundamental"] = any(
                    fina.get(k) is not None
                    for k in ("revenue_yoy", "net_profit_yoy", "roe", "gross_margin")
                )
            else:
                s["has_real_fundamental"] = False

    monkeypatch.setattr(feature_builder, "_enrich_with_auxiliary_data", _fake_enrich)

    # db 非 None 才会触发 _enrich_with_auxiliary_data
    enriched = build_features([stock], snapshot, db=object())
    item = enriched[0]

    assert item["roe"] == 10.57
    assert item["gross_margin"] == 89.76
    assert item["revenue_yoy"] == 6.54
    assert item["net_profit_yoy"] == 1.47
    assert item["has_real_fundamental"] is True
    # 真实数据算出的基本面分：55 + 0 + 0 + 0 + 5(gross_margin>=25) = 60.0
    assert item["fundamental_score"] == 60.0


def test_build_features_neutral_when_no_fina(monkeypatch):
    """build_features 无 fina 数据时，基本面分为中性 55，has_real_fundamental=False。"""
    stock = {
        "code": "000001",
        "name": "平安银行",
        "price": 12.0,
        "close": 12.0,
        "pct": 0.5,
        "industry": "银行",
        "amount_yi": 5.0,
        "turnover_rate": 1.0,
        "vol5_20": 1.0,
        "off_high_pct": -3.0,
        "breakout20": False,
        "max_dd20": -5.0,
        "upper_shadow_count20": 1,
        "announcement_negative_count": 0,
        "news_risk_count": 0,
        "pos20": 50,
        "eff20": 0.3,
        "chg20": 5.0,
        "dist20": 3.0,
        "dsa_profile": {},
    }
    snapshot = {
        "sample_count": 300,
        "up_count": 180,
        "down_count": 100,
        "limit_up_count": 12,
        "limit_down_count": 3,
        "main_industries": ["白酒"],
        "trade_date": "2026-07-05",
        "breadth": {"regime": "多头"},
    }

    from app.services import feature_builder

    def _fake_enrich(stocks, db, snap, _aux_override=None):
        for s in stocks:
            s["has_real_fundamental"] = False

    monkeypatch.setattr(feature_builder, "_enrich_with_auxiliary_data", _fake_enrich)

    enriched = build_features([stock], snapshot, db=object())
    item = enriched[0]

    assert item["has_real_fundamental"] is False
    assert item["revenue_yoy"] is None
    assert item["roe"] is None
    assert item["fundamental_score"] == 55.0

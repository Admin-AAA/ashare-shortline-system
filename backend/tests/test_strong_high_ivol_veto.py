"""L-093 强牛 regime × 高特质波动(ivol20)横截面否决（production_selection v3）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.production_policy import (
    ProductionSelectionPolicy,
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)
from app.services.risk_features import compute_ivol20, attach_ivol20_pct


def _card(code: str, score: float, ivol_pct: float | None = None) -> dict:
    c = {"code": code, "bucket": "chen-xiaoqun-skill", "final_score": score,
         "evidence_quality_score": 90.0, "data_completeness_score": 100.0,
         "data_authenticity": 1.0, "data_quality": {"missing_fields": []}}
    if ivol_pct is not None:
        c["ivol20_pct"] = ivol_pct
    return c


def _bull() -> dict:
    # up_ratio=0.80 ≥ 0.65 → 强牛
    return {"sample_count": 100, "up_count": 80, "limit_up_count": 0, "limit_down_count": 0}


def _range_mkt() -> dict:
    # up_ratio=0.40 ∈ [0.35,0.50) → 震荡
    return {"sample_count": 100, "up_count": 40, "limit_up_count": 0, "limit_down_count": 0}


def _cards():
    # 11 张降分卡；最高分 600000 为高 ivol（0.95），其余低
    cs = [_card(f"{600000 + i:06d}", 100.0 - i, 0.50) for i in range(11)]
    cs[0]["ivol20_pct"] = 0.95
    return cs


def test_v3_drops_high_ivol_in_strong_bull_and_promotes_next() -> None:
    v3 = load_production_selection_policy(version=3)
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_bull()), v3)
    codes = [c["code"] for c in res.recommendations]
    assert "600000" not in codes          # 高 ivol 最高分被剔
    assert "600010" in codes             # 第 11 名自然递补
    assert len(codes) == 10
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600000"].drop_owner == "strong_high_ivol_veto"


def test_v2_keeps_high_ivol_bit_identical() -> None:
    v2 = load_production_selection_policy(version=2)
    assert v2.strong_high_ivol_veto_enabled is False
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_bull()), v2)
    codes = [c["code"] for c in res.recommendations]
    assert codes[0] == "600000"          # v2 不受 ivol 影响
    assert "600010" not in codes


def test_v3_does_not_veto_outside_strong_bull() -> None:
    v3 = load_production_selection_policy(version=3)
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_range_mkt()), v3)
    codes = [c["code"] for c in res.recommendations]
    assert codes[0] == "600000"          # 震荡日规则不生效


def test_v3_keeps_card_when_ivol_missing() -> None:
    v3 = load_production_selection_policy(version=3)
    cs = [_card(f"{600000 + i:06d}", 100.0 - i, None) for i in range(11)]  # 全无 ivol
    res = evaluate_selection_policy(
        cs, SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_bull()), v3)
    codes = [c["code"] for c in res.recommendations]
    assert codes[0] == "600000"          # 数据不全保守不动


def test_default_dataclass_rule_disabled() -> None:
    p = ProductionSelectionPolicy()
    assert p.strong_high_ivol_veto_enabled is False
    assert tuple(p.strong_high_ivol_regimes) == ("强牛",)
    assert p.strong_high_ivol_pct_threshold == 0.80


def test_attach_helper_sets_field_and_leaves_unknown_blank() -> None:
    cards = [{"code": "000001"}, {"code": "000002"}]
    pmap = {("2025-03-03", "000001"): 0.91}
    attach_ivol20_pct(cards, "2025-03-03", pmap)
    assert cards[0]["ivol20_pct"] == 0.91
    assert "ivol20_pct" not in cards[1]


def test_compute_ivol20_zero_when_stock_moves_exactly_with_market() -> None:
    # r = 1.2*m（纯 beta、无残差）→ ivol ≈ 0
    idx = pd.date_range("2025-01-01", periods=40)
    rng = np.random.default_rng(0)
    m = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    r = 1.2 * m
    iv = compute_ivol20(r, m).dropna()
    assert (iv < 1e-12).all()


def test_compute_ivol20_positive_with_idiosyncratic_noise() -> None:
    idx = pd.date_range("2025-01-01", periods=40)
    rng = np.random.default_rng(1)
    m = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    r = 0.8 * m + pd.Series(rng.normal(0, 0.02, 40), index=idx)
    iv = compute_ivol20(r, m).dropna()
    assert (iv > 0).all()
    # 特质噪声 2% 主导，ivol 应明显大于纯市场波动尺度
    assert iv.iloc[-1] > 0.01

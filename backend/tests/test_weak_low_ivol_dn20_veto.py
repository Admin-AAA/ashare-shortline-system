"""L-109 弱势 regime × 低特质下行半波动(ivol_dn20)横截面否决（production_selection v4）。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.production_policy import (
    ProductionSelectionPolicy,
    SelectionPolicyContext,
    evaluate_selection_policy,
    load_production_selection_policy,
)
from app.services.risk_features import (
    attach_ivol_dn20_pct,
    compute_ivol20,
    compute_ivol_dn20,
)


def _card(code: str, score: float, dn_pct: float | None = None,
          ivol_pct: float | None = None) -> dict:
    c = {"code": code, "bucket": "chen-xiaoqun-skill", "final_score": score,
         "evidence_quality_score": 90.0, "data_completeness_score": 100.0,
         "data_authenticity": 1.0, "data_quality": {"missing_fields": []}}
    if dn_pct is not None:
        c["ivol_dn20_pct"] = dn_pct
    if ivol_pct is not None:
        c["ivol20_pct"] = ivol_pct
    return c


def _weak() -> dict:
    # up_ratio=0.25 < 0.35 → 弱势；涨停率 0.3% ∈ [0.1%,0.5%] → 平淡（避开冰点 2 只限额）
    return {"sample_count": 1000, "up_count": 250, "limit_up_count": 3, "limit_down_count": 0}


def _bull() -> dict:
    # up_ratio=0.80 ≥ 0.65 → 强牛
    return {"sample_count": 1000, "up_count": 800, "limit_up_count": 3, "limit_down_count": 0}


def _range_mkt() -> dict:
    # up_ratio=0.40 ∈ [0.35,0.50) → 震荡
    return {"sample_count": 1000, "up_count": 400, "limit_up_count": 3, "limit_down_count": 0}


def _cards(top_dn: float | None = 0.05, rest_dn: float | None = 0.50):
    """11 张降分卡；最高分 600000 用 top_dn，其余用 rest_dn（None = 不带该字段）。"""
    cs = [_card(f"{600000 + i:06d}", 100.0 - i, rest_dn) for i in range(11)]
    if top_dn is None:
        cs[0].pop("ivol_dn20_pct", None)
    else:
        cs[0]["ivol_dn20_pct"] = top_dn
    return cs


def test_v4_drops_low_ivol_dn20_in_weak_regime_and_promotes_next() -> None:
    v4 = load_production_selection_policy(version=4)
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10,
                                         market_snapshot=_weak()), v4)
    codes = [c["code"] for c in res.recommendations]
    assert "600000" not in codes          # 低特质下行波动最高分被剔
    assert "600010" in codes             # 第 11 名自然递补
    assert len(codes) == 10
    verdicts = {v.code: v for v in res.verdicts}
    assert verdicts["600000"].drop_owner == "weak_low_ivoldn20_veto"


def test_v4_threshold_boundary_is_inclusive_low_side() -> None:
    """分位 <= 0.25 剔除；0.26 放行（边界含等号，方向与 L-093 的 >= 相反）。"""
    v4 = load_production_selection_policy(version=4)
    ctx = SelectionPolicyContext(score_line=40.0, output_count=10, market_snapshot=_weak())
    at = _cards(top_dn=0.25)      # 恰好等于阈值 → 剔（含等号）
    res_at = evaluate_selection_policy(at, ctx, v4)
    assert "600000" not in [c["code"] for c in res_at.recommendations]
    assert len(res_at.recommendations) == 10     # 其余 10 张递补补齐
    above = _cards(top_dn=0.26)   # 略高于阈值 → 留
    res_above = evaluate_selection_policy(above, ctx, v4)
    assert [c["code"] for c in res_above.recommendations][0] == "600000"


def test_v3_keeps_low_ivol_dn20_bit_identical() -> None:
    v3 = load_production_selection_policy(version=3)
    assert v3.weak_low_ivoldn20_veto_enabled is False
    res = evaluate_selection_policy(
        _cards(), SelectionPolicyContext(score_line=40.0, output_count=10,
                                         market_snapshot=_weak()), v3)
    codes = [c["code"] for c in res.recommendations]
    assert codes[0] == "600000"           # v3 不受 ivol_dn20 影响
    assert "600010" not in codes


def test_v4_does_not_veto_outside_weak_regime() -> None:
    v4 = load_production_selection_policy(version=4)
    for snap in (_bull(), _range_mkt()):
        res = evaluate_selection_policy(
            _cards(), SelectionPolicyContext(score_line=40.0, output_count=10,
                                             market_snapshot=snap), v4)
        codes = [c["code"] for c in res.recommendations]
        assert codes[0] == "600000", snap     # 非弱势日该规则不生效


def test_v4_keeps_card_when_ivol_dn20_missing() -> None:
    """数据不全保守放行——绝不因风险特征缺列而误砍候选。"""
    v4 = load_production_selection_policy(version=4)
    cs = [_card(f"{600000 + i:06d}", 100.0 - i, None) for i in range(11)]
    res = evaluate_selection_policy(
        cs, SelectionPolicyContext(score_line=40.0, output_count=10,
                                   market_snapshot=_weak()), v4)
    codes = [c["code"] for c in res.recommendations]
    assert codes[0] == "600000"


def test_v4_still_inherits_v3_strong_bull_high_ivol_veto() -> None:
    """v4 是 v3 的超集：强牛×高 ivol20 否决必须原样保留。"""
    v4 = load_production_selection_policy(version=4)
    cs = _cards(top_dn=0.50)
    cs[0]["ivol20_pct"] = 0.95            # 强牛 + 高双侧特质波动 → 被 L-093 规则剔
    res = evaluate_selection_policy(
        cs, SelectionPolicyContext(score_line=40.0, output_count=10,
                                   market_snapshot=_bull()), v4)
    codes = [c["code"] for c in res.recommendations]
    assert "600000" not in codes
    assert {v.code: v for v in res.verdicts}["600000"].drop_owner == "strong_high_ivol_veto"


def test_default_dataclass_rule_disabled() -> None:
    p = ProductionSelectionPolicy()
    assert p.weak_low_ivoldn20_veto_enabled is False
    assert tuple(p.weak_low_ivoldn20_regimes) == ("弱势",)
    assert p.weak_low_ivoldn20_pct_threshold == 0.25


def test_default_policy_is_v4_and_v4_artifact_differs_from_v3_only_by_new_rule() -> None:
    from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY

    assert DEFAULT_PRODUCTION_SELECTION_POLICY.version == 4
    v3 = load_production_selection_policy(version=3)
    v4 = DEFAULT_PRODUCTION_SELECTION_POLICY
    assert v3.weak_low_ivoldn20_veto_enabled is False
    assert v4.weak_low_ivoldn20_veto_enabled is True
    # 除新规则三字段与 version 外，其余规则逐位一致（v4 = v3 + 本条）
    p3, p4 = v3.canonical_payload(), v4.canonical_payload()
    diff = {k for k in set(p3) | set(p4) if p3.get(k) != p4.get(k)}
    # v4 = v3 + 本条规则：v3 artifact 不含新键 → 阈値/regime 回落到 dataclass 默认
    # （与 v4 取值相同），故 canonical_payload 只差 version 与 enabled 开关。
    diff = {k for k in set(p3) | set(p4) if p3.get(k) != p4.get(k)}
    assert diff == {"version", "weak_low_ivoldn20_veto_enabled"}, diff


def test_attach_helper_sets_field_and_leaves_unknown_blank() -> None:
    cards = [{"code": "000001"}, {"code": "000002"}]
    pmap = {("2025-03-03", "000001"): 0.11}
    attach_ivol_dn20_pct(cards, "2025-03-03", pmap)
    assert cards[0]["ivol_dn20_pct"] == 0.11
    assert "ivol_dn20_pct" not in cards[1]


def test_compute_ivol_dn20_zero_when_all_residuals_positive() -> None:
    """r 恒高于 beta*m → 残差全正 → 下行半波动 0（合法值，语义上"下行从未波动"）。"""
    idx = pd.date_range("2025-01-01", periods=40)
    rng = np.random.default_rng(0)
    m = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    r = 1.2 * m + 0.003                    # 恒定正残差
    dn = compute_ivol_dn20(r, m).dropna()
    iv = compute_ivol20(r, m).dropna()
    assert (dn < 1e-12).all()
    assert (iv < 1e-12).all()              # 恒定残差的 std 也为 0


def test_compute_ivol_dn20_detects_left_tail_that_std_misses() -> None:
    """恒速阴跌：残差每天都是同一个负数 → 双侧 std=0（"零波动"）但下行半波动 = 0.015。

    这正是 L-109 毒格的形态：日线上"安静得像没在动"，实则每天都在小幅失血，
    双侧 ivol20 看不到，下行侧 ivol_dn20 看得到。
    """
    idx = pd.date_range("2025-01-01", periods=40)
    rng = np.random.default_rng(3)
    m = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    r = pd.Series(-0.015, index=idx)          # 恒定残差（cov(r,m)=0 → beta=0）
    dn = compute_ivol_dn20(r, m).dropna()
    iv = compute_ivol20(r, m).dropna()
    assert (dn > 0.014).all()
    assert (iv < 1e-12).all()


def test_compute_ivol_dn20_is_never_negative_and_bounded_by_max_abs_resid() -> None:
    idx = pd.date_range("2025-01-01", periods=60)
    rng = np.random.default_rng(7)
    m = pd.Series(rng.normal(0, 0.012, 60), index=idx)
    r = 0.8 * m + pd.Series(rng.normal(0, 0.02, 60), index=idx)
    dn = compute_ivol_dn20(r, m).dropna()
    assert (dn >= 0).all()
    assert dn.iloc[-1] < 0.05

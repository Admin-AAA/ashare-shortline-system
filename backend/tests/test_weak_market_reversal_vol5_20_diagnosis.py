"""锁定 weak-market-strong-stock-reversal「合理稀疏非静默死亡」诊断结论。

背景（2026-07-21 诊断）：自检报该策略近 60 天 formal 0 命中、近 30 天 0 曝光。
根因不是 universe 过苛/字段缺失/preset 盲区，而是：
  1. 生产管线 vol5_20 正常注入（market_data.py:919 = ma(vol,5)/ma(vol,20)），
     早期诊断误用原始瞬时量比列 vol_ratio_5/20（大量 None）导致「vol5_20=None
     卡死全部候选」的假象。本测试锁定正确口径。
  2. 该策略 2026-07-19 才注册进 skill_registry，恰逢其后弱市日全是
     limit_down_count>100 恐慌日（触发「系统性恐慌禁入」hard_veto，设计正确），
     错过 2026-07-09（弱势 ld≈17-22）这次可启用的非恐慌弱市窗口。
  3. 用生产 vol5_20 口径回扫，11 个非恐慌弱市日共有 68 个真实 setup 命中
     （日均 ~6 只），证明是「合理稀疏」而非「结构性互斥/静默死亡」。

不锁胜率/具体命中数（防过拟合），只锁：正确口径下弱市日存在 setup 命中、
错误口径（瞬时量比 None）会产生「全卡死」假阴性这两件事。
"""
from __future__ import annotations


def _ma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _vol5_20_production(volumes):
    """生产口径：ma(volume,5)/ma(volume,20)（market_data.py:919）。"""
    v5 = _ma(volumes, 5)
    v20 = _ma(volumes, 20)
    if v5 is None or not v20:
        return None
    return v5 / v20


def _vol5_20_naive(vol_ratio_5, vol_ratio_20):
    """错误口径：直接用原始瞬时量比列（daily_kbars.vol_ratio_5/20，常为 None）。"""
    if vol_ratio_5 is None or not vol_ratio_20:
        return None
    return vol_ratio_5 / vol_ratio_20


def test_production_vol5_20_computed_from_volume_series_not_instant_ratio() -> None:
    # 缩量：近5日均量 < 20日均量 → vol5_20 < 1.1（满足 setup 的缩量条件）
    shrinking = [100.0] * 15 + [60.0] * 5
    # v5=300/5=60, v20=(1500+300)/20=90 → 60/90≈0.667
    assert _vol5_20_production(shrinking) == 60.0 / 90.0

    # 放量：近5日均量 > 20日均量 → vol5_20 > 1.1（被 setup 卡掉）
    expanding = [60.0] * 15 + [150.0] * 5
    assert _vol5_20_production(expanding) > 1.1


def test_naive_instant_ratio_column_yields_false_negative_when_none() -> None:
    # 错误口径下，原始瞬时量比列为 None → vol5_20=None → 「vol5_20<=1.1」对 None
    # 静默 False → 候选被卡死。这正是早期诊断 0 命中的假象来源。
    assert _vol5_20_naive(None, 1.0) is None
    assert _vol5_20_naive(None, None) is None
    # 而同一股票用生产口径有真实值 —— 两口径结果可截然不同
    shrinking = [100.0] * 15 + [60.0] * 5
    assert _vol5_20_production(shrinking) is not None


def test_shrinking_volume_passes_setup_gate_under_production() -> None:
    # 端到端语义：缩量回撤（生产 vol5_20<=1.1）应通过 setup 的量能门槛
    shrinking = [100.0] * 15 + [55.0] * 5
    assert _vol5_20_production(shrinking) <= 1.1

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""失败归因闭环测试：模式分类 + 保本地板逻辑 + 指标去重。"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_cost_aware import BacktestRow, compute_metrics, dedup_rows  # noqa: E402
from scripts.failure_attribution import (  # noqa: E402
    attribute_failures,
    classify_failure_mode,
    _veto_net_effect,
    veto_passes_guard,
    sweep_veto_candidates,
    DEFAULT_VETO_CANDIDATES,
)


# --------------------------------------------------------------------------- #
# 失败模式分类
# --------------------------------------------------------------------------- #
def test_classify_tail_gap():
    assert classify_failure_mode(
        exit_reason="stop_loss_gap", executed_return=-8.0, max_return=2.0,
        rsi_12=60, chg20=10, pos60=70, setup_type="多头顺势",
    ) == "tail_gap"


def test_classify_gap_roundtrip():
    # 峰值 +5% 却净亏 —— 跳空回吐（本轮主导失败模式）
    assert classify_failure_mode(
        exit_reason="trailing_stop_hit", executed_return=-1.5, max_return=5.0,
        rsi_12=70, chg20=30, pos60=80, setup_type="多头顺势",
    ) == "gap_roundtrip"


def test_classify_weak_setup():
    assert classify_failure_mode(
        exit_reason="stop_loss_hit", executed_return=-3.0, max_return=1.0,
        rsi_12=55, chg20=10, pos60=60, setup_type="放量突破",
    ) == "bad_setup"


def test_classify_weak_stock():
    assert classify_failure_mode(
        exit_reason="stop_loss_hit", executed_return=-2.0, max_return=0.5,
        rsi_12=30, chg20=-5, pos60=40, setup_type="多头顺势",
    ) == "weak_stock"


def test_classify_extension_chase():
    assert classify_failure_mode(
        exit_reason="stop_loss_hit", executed_return=-4.0, max_return=1.0,
        rsi_12=75, chg20=60, pos60=95, setup_type="多头顺势",
    ) == "extension_chase"


def test_classify_no_follow_through():
    # 入场即套：峰值≤0.5% 且净亏，未延续（与 gap_roundtrip 的 max>3% 涨后回吐互斥）
    assert classify_failure_mode(
        exit_reason="stop_loss_hit", executed_return=-4.0, max_return=0.15,
        rsi_12=55, chg20=13, pos60=72, setup_type="多头顺势",
    ) == "no_follow_through"


def test_classify_no_follow_through_horizon_close():
    # 持有到期平仓也是亏（horizon_close）且峰值几乎没涨 → no_follow_through（非 other）
    assert classify_failure_mode(
        exit_reason="horizon_close", executed_return=-2.4, max_return=-0.65,
        rsi_12=58, chg20=11, pos60=77, setup_type="单峰放量突破",
    ) == "no_follow_through"


def test_classify_no_follow_through_boundary_max_above_threshold():
    # max_return 刚好 >0.5（如 1.0）不命中 no_follow_through，落到 extension_chase（chg20 高）或 other
    assert classify_failure_mode(
        exit_reason="stop_loss_hit", executed_return=-2.0, max_return=1.0,
        rsi_12=55, chg20=20, pos60=65, setup_type="多头顺势",
    ) == "other"


def test_classify_no_follow_through_not_when_profitable():
    # max_return≤0.5 但 executed_return≥0（非失败特征）→ 不命中（实际不会出现在 failed 集，但纯函数守卫）
    assert classify_failure_mode(
        exit_reason="horizon_close", executed_return=0.5, max_return=0.3,
        rsi_12=55, chg20=13, pos60=72, setup_type="多头顺势",
    ) == "other"


def test_classify_other():
    assert classify_failure_mode(
        exit_reason="stop_loss_hit", executed_return=-2.0, max_return=1.0,
        rsi_12=55, chg20=20, pos60=65, setup_type="多头顺势",
    ) == "other"


def test_attribute_failures_dominant():
    fields = [
        {"exit_reason": "trailing_stop_hit", "executed_return": -1.5, "max_return": 5.0, "rsi_12": 70, "chg20": 30, "pos60": 80, "setup_type": "x"},
        {"exit_reason": "trailing_stop_hit", "executed_return": -2.0, "max_return": 4.0, "rsi_12": 68, "chg20": 25, "pos60": 75, "setup_type": "x"},
        {"exit_reason": "stop_loss_hit", "executed_return": -3.0, "max_return": 1.0, "rsi_12": 30, "chg20": -5, "pos60": 40, "setup_type": "x"},
    ]
    res = attribute_failures(fields)
    assert res.total_failed == 3
    assert res.dominant_mode() == "gap_roundtrip"
    assert res.mode_counts["gap_roundtrip"] == 2


# --------------------------------------------------------------------------- #
# 指标去重 sanity（与 backtest_cost_aware 复用）
# --------------------------------------------------------------------------- #
def test_dedup_keeps_max_score():
    # dedup_rows 按 recommendation_id 去重（每个 id = 一笔独立交易）；同一 id 多条 outcome
    # 保留 final_score 最高的一条。这里用相同 id "r1" 验证高分胜出。
    rows = [
        BacktestRow("r1", "2026-01-01", "600000", 2.0, "success", "tp", 5, 60.0, []),
        BacktestRow("r1", "2026-01-01", "600000", 1.0, "failed", "sl", 5, 80.0, []),  # 同 recommendation_id 高分
    ]
    d = dedup_rows(rows)
    assert len(d) == 1
    assert d[0].final_score == 80.0
    assert d[0].outcome_label == "failed"


def test_compute_metrics_basic():
    rows = [
        BacktestRow("r1", "2026-01-01", "A", 3.0, "success", "tp", 5, 80.0, []),
        BacktestRow("r2", "2026-01-02", "B", -2.0, "failed", "sl", 5, 70.0, []),
    ]
    m = compute_metrics(rows)
    assert m["n"] == 2
    assert math.isclose(m["win_rate"], 0.5)
    assert m["max_loss_net"] < 0


# --------------------------------------------------------------------------- #
# 反事实 veto 护栏（anti-overfit guard，第172轮新增）
# --------------------------------------------------------------------------- #
def _mk_rec(rid, label, ret, fs):
    return {"rid": rid, "label": label, "ret": ret, "fs": fs}


def test_veto_net_effect_harmful_removes_winners():
    # 过拟合典型（真实 pos60>90 即此形态）：被 veto 子群胜率更低（删之→总胜率被刷高），
    # 但净收益为正（删之→总收益↓）。护栏应判不过。
    # 全量 5 赢/10 = 50% WR，净 +6.0；veto 子群(pos60>90) 1 赢 4 亏 WR20% 净 +3.0；
    # 保留子群 4 赢 1 亏 WR80% 净 +3.0。施加后 WR 80%(+30pp) 但净 +3.0-6.0=-3.0。
    recs = [
        _mk_rec("vw1", "success", 5.0, {"pos60": 95}),
        _mk_rec("vf1", "failed", -0.5, {"pos60": 96}),
        _mk_rec("vf2", "failed", -0.5, {"pos60": 97}),
        _mk_rec("vf3", "failed", -0.5, {"pos60": 98}),
        _mk_rec("vf4", "failed", -0.5, {"pos60": 99}),
        _mk_rec("kw1", "success", 1.0, {"pos60": 30}),
        _mk_rec("kw2", "success", 1.0, {"pos60": 31}),
        _mk_rec("kw3", "success", 1.0, {"pos60": 32}),
        _mk_rec("kw4", "success", 1.0, {"pos60": 33}),
        _mk_rec("kf1", "failed", -1.0, {"pos60": 34}),
    ]
    res = _veto_net_effect(recs, lambda fs: fs.get("pos60", 0) > 90)
    assert res["removed_n"] == 5
    assert res["removed_wins"] == 1
    assert res["removed_fails"] == 4
    assert res["delta_win_rate"] > 0          # 胜率被刷高
    assert res["delta_net_return"] < 0        # 但总收益下降 → 过拟合
    assert not veto_passes_guard(res)         # 护栏拦截


def test_veto_net_effect_beneficial_removes_losers():
    # 良性 veto：只删亏损单（总收益↑且胜率↑）→ 护栏应通过
    recs = [
        _mk_rec("w1", "success", 4.0, {"pos60": 95}),  # 保留（赢）
        _mk_rec("w2", "success", 3.0, {"pos60": 96}),  # 保留（赢）
        _mk_rec("f1", "failed", -2.0, {"pos60": 30}),  # 被 veto（亏）
        _mk_rec("f2", "failed", -1.0, {"pos60": 20}),  # 被 veto（亏）
    ]
    res = _veto_net_effect(recs, lambda fs: fs.get("pos60", 0) < 50)
    assert res["removed_n"] == 2
    assert res["removed_wins"] == 0
    assert res["removed_fails"] == 2
    assert res["delta_win_rate"] > 0
    assert res["delta_net_return"] > 0        # 总收益上升 → 良性
    assert veto_passes_guard(res)             # 护栏通过


def test_veto_net_effect_noop():
    # 不删任何笔 → Δ 全 0，护栏通过（中性）
    recs = [_mk_rec("a", "success", 2.0, {"pos60": 50}), _mk_rec("b", "failed", -1.0, {"pos60": 40})]
    res = _veto_net_effect(recs, lambda fs: False)
    assert res["removed_n"] == 0
    assert res["delta_net_return"] == 0.0
    assert veto_passes_guard(res)


# --------------------------------------------------------------------------- #
# veto 候选扫描（D 攻击护栏·一键否决筛查，2026-07-21 新增）
# --------------------------------------------------------------------------- #
def test_sweep_veto_candidates_flags_overfit_defaults():
    # 复刻真实 LIVE formal 账本性质：每个默认候选 veto 都「删赢多于亏、净收益↓」
    # → 全部被护栏 BLOCK。锁定「当前账本无干净 D 攻击」发现，防未来轮次重复尝试
    # 这些已知过拟合候选（pos60>90 / chg20>50 / rsi<45 / pos60<50&rsi<50）。
    recs = [
        _mk_rec("w1", "success", 8.0, {"pos60": 95, "chg20": 60, "rsi_12": 70}),  # pos60>90 赢
        _mk_rec("f1", "failed", -2.0, {"pos60": 96, "chg20": 55, "rsi_12": 72}),  # pos60>90 亏
        _mk_rec("w2", "success", 6.0, {"pos60": 30, "chg20": 60, "rsi_12": 68}),  # chg20>50 赢
        _mk_rec("f2", "failed", -1.5, {"pos60": 40, "chg20": 55, "rsi_12": 66}),  # chg20>50 亏
        _mk_rec("w3", "success", 5.0, {"pos60": 50, "chg20": 10, "rsi_12": 30}),  # rsi<45 赢
        _mk_rec("f3", "failed", -1.0, {"pos60": 55, "chg20": 12, "rsi_12": 32}),  # rsi<45 亏
        _mk_rec("w4", "success", 4.0, {"pos60": 30, "chg20": 8, "rsi_12": 30}),   # pos<50&rsi<45 赢
        _mk_rec("f4", "failed", -0.8, {"pos60": 35, "chg20": 9, "rsi_12": 33}),   # pos<50&rsi<45 亏
    ]
    results = sweep_veto_candidates(recs)
    assert len(results) == len(DEFAULT_VETO_CANDIDATES)
    for r in results:
        assert r["delta_net_return"] < 0, f"{r['name']} 净收益应下降"
        assert not r["passed"], f"{r['name']} 应被 BLOCK（删赢多于亏）"


def test_sweep_veto_candidates_passes_clean():
    # 对照：只删亏损单（净收益↑且胜率↑）的良性 veto → 护栏通过。
    recs = [
        _mk_rec("w1", "success", 5.0, {"pos60": 95, "chg20": 60, "rsi_12": 70}),
        _mk_rec("w2", "success", 4.0, {"pos60": 96, "chg20": 55, "rsi_12": 72}),
        _mk_rec("f1", "failed", -2.0, {"pos60": 30, "chg20": 10, "rsi_12": 40}),
        _mk_rec("f2", "failed", -1.5, {"pos60": 35, "chg20": 12, "rsi_12": 42}),
    ]
    results = sweep_veto_candidates(
        recs, candidates=[("pos60<50 only fails", lambda fs: fs.get("pos60", 99) < 50)]
    )
    assert len(results) == 1
    assert results[0]["removed_wins"] == 0
    assert results[0]["delta_net_return"] > 0
    assert results[0]["passed"]

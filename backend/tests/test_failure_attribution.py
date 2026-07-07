#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""失败归因闭环测试：模式分类 + 保本地板逻辑 + 指标去重。"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_cost_aware import BacktestRow, compute_metrics, dedup_rows  # noqa: E402
from scripts.failure_attribution import (  # noqa: E402
    attribute_failures,
    classify_failure_mode,
)


class _Bar:
    """最小 DailyKbar 替身，含 _resolve_exit_with_trailing 用到的字段 + 流动性字段。"""

    def __init__(self, o, h, l, c, d="2026-01-02", pct_chg=2.0):
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.trade_date = d
        self.pct_chg = pct_chg
        self.volume = 1_000_000
        self.amount = 100_000_000


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
# 保本地板逻辑（直接驱动 _resolve_exit_with_trailing）
# --------------------------------------------------------------------------- #
def _sim_trailing(bars, entry_price=100.0):
    from app.services.outcome_service import _resolve_exit_with_trailing

    return _resolve_exit_with_trailing(
        bars, stop_loss_price=entry_price * 0.945,
        take_profit_price=entry_price * 1.05, code="", entry_price=entry_price,
    )


def test_trailing_gap_down_exits_at_gap_not_floored():
    # 回归守卫（失败归因闭环，第136轮）：trailing 激活后跳空低开，出场价取跳空价（保守 min 口径），
    # 绝不地板到棘轮价位——max(trigger, 地板) 会令 trigger_px≤地板恒成立，等效 max() 高估
    # （flat 地板盈亏比 1.98→0.88、棘轮地板 1.98→1.09，均崩）。gap-roundtrip 是市场风险，不靠 exit 地板修复。
    bars = [
        _Bar(100, 103, 102.5, 102.8, "2026-01-02"),  # 激活(peak +3%)，low 102.5>触发价102.2，不触发
        _Bar(97, 97.5, 96, 96.5, "2026-01-03", pct_chg=-3.0),  # 跳空低开，低点跌破触发价
    ]
    exit_px, _, reason = _sim_trailing(bars)
    assert reason == "trailing_stop_hit"
    assert exit_px == pytest.approx(97.0)  # 取跳空价（亏损），不是地板 102.2


def test_trailing_normal_pullback_exits_at_trigger():
    # 非跳空回撤：出场价取 min(open, 触发价)（保守、现实成交）
    exit_px, _, reason = _sim_trailing([_Bar(104, 104, 103.0, 103.5)])
    assert reason == "trailing_stop_hit"
    assert exit_px == pytest.approx(103.2)


def test_trailing_not_activated_uses_stop_loss():
    # 未激活（high 未达 entry+2%）：不触发 trailing，固定止损路径
    exit_px, _, reason = _sim_trailing([_Bar(100, 101, 94, 95)])
    assert reason == "stop_loss_hit"
    assert exit_px == pytest.approx(94.5)


# --------------------------------------------------------------------------- #
# 指标去重 sanity（与 backtest_cost_aware 复用）
# --------------------------------------------------------------------------- #
def test_dedup_keeps_max_score():
    rows = [
        BacktestRow("r1", "2026-01-01", "600000", 2.0, "success", "tp", 5, 60.0, []),
        BacktestRow("r2", "2026-01-01", "600000", 1.0, "failed", "sl", 5, 80.0, []),  # 同 (date,code) 高分
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

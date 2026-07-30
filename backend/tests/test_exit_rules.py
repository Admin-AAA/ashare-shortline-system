#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓周期上限反事实验证测试：纯比较逻辑 + 生产 _simulate_outcome 委托（零拷贝反事实）。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.exit_rules import (  # noqa: E402
    compare_horizons,
    simulate_outcome_with_horizon,
)
from scripts.backtest_cost_aware import BacktestRow  # noqa: E402


# --------------------------------------------------------------------------- #
# 纯函数：多 horizon 指标比较
# --------------------------------------------------------------------------- #
def test_compare_horizons_picks_best_per_metric():
    metrics = {
        2: {"n": 100, "win_rate": 0.60, "avg_net_return": 2.0, "pl_ratio": 1.5, "max_loss_net": -8.0, "sharpe_per_trade": 0.40},
        5: {"n": 100, "win_rate": 0.66, "avg_net_return": 3.0, "pl_ratio": 1.98, "max_loss_net": -7.17, "sharpe_per_trade": 0.52},
    }
    cmp = compare_horizons(metrics, baseline_horizon=5)
    # 胜率 / 平均净收益 / 盈亏比 / Sharpe 在 5 天最优
    assert cmp["best"]["win_rate"]["best_horizon"] == 5
    assert cmp["best"]["avg_net_return"]["best_horizon"] == 5
    assert cmp["best"]["pl_ratio"]["best_horizon"] == 5
    # 最大亏损(净) 越高越好（更浅），2 天的 -8.0 劣于 5 天的 -7.17
    assert cmp["best"]["max_loss_net"]["best_horizon"] == 5
    # 推荐 horizon：胜率不降约束下取盈亏比最高；5 天胜率更高 → 推荐 5
    assert cmp["recommended_horizon"] == 5


def test_compare_horizons_recommends_shorter_when_win_rate_holds():
    # 更短 horizon 胜率不降且盈亏比更高 → 推荐更短（与北极星对齐）
    metrics = {
        2: {"n": 100, "win_rate": 0.70, "avg_net_return": 1.5, "pl_ratio": 2.10, "max_loss_net": -5.0, "sharpe_per_trade": 0.60},
        5: {"n": 100, "win_rate": 0.66, "avg_net_return": 3.0, "pl_ratio": 1.98, "max_loss_net": -7.17, "sharpe_per_trade": 0.52},
    }
    cmp = compare_horizons(metrics, baseline_horizon=5)
    assert cmp["recommended_horizon"] == 2


# --------------------------------------------------------------------------- #
# 生产 _simulate_outcome 委托：horizon 截断确实改变结果（零拷贝反事实守卫）
# --------------------------------------------------------------------------- #
class _Bar:
    """最小 DailyKbar 替身（含流动性字段，避免 no_liquidity 拦截）。"""

    def __init__(self, o, h, l, c, d, pct_chg):
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.trade_date = d
        self.pct_chg = pct_chg
        self.volume = 10_000_000
        self.amount = 1_000_000_000


def _make_rec_and_bars():
    from app.models.tables import DailyKbar, Recommendation

    fs = {
        "ideal_buy": 100.0,
        "secondary_buy": 99.0,
        "market_sentiment_phase": "活跃",
    }
    rec = Recommendation(
        recommendation_id=1,
        code="600000",
        trade_date="2026-01-02",
        recommend_price=100.0,
        final_score=70.0,
        skill_breakdown_json=json.dumps([{"strategy_id": "trend-pullback-continuation"}]),
        feature_snapshot_json=json.dumps(fs),
    )
    bars = [
        DailyKbar(code="600000", trade_date="2026-01-02", open=100, high=101, low=99, close=100, pct_chg=1.0, volume=10_000_000, amount=1_000_000_000),
        DailyKbar(code="600000", trade_date="2026-01-03", open=100, high=101, low=99, close=100, pct_chg=1.0, volume=10_000_000, amount=1_000_000_000),
        DailyKbar(code="600000", trade_date="2026-01-04", open=100, high=102, low=99, close=101, pct_chg=1.0, volume=10_000_000, amount=1_000_000_000),
        # 第3个持仓日(01-05)跳空低开回吐：horizon=5 会持有到此处触发 trailing 止损(净亏)，
        # horizon=2 在 01-04 收盘就离场(小赚)
        DailyKbar(code="600000", trade_date="2026-01-05", open=100, high=101, low=90, close=92, pct_chg=-8.9, volume=10_000_000, amount=1_000_000_000),
        DailyKbar(code="600000", trade_date="2026-01-06", open=92, high=93, low=91, close=92, pct_chg=-1.0, volume=10_000_000, amount=1_000_000_000),
        DailyKbar(code="600000", trade_date="2026-01-07", open=92, high=93, low=91, close=92, pct_chg=-1.0, volume=10_000_000, amount=1_000_000_000),
    ]
    dates = [b.trade_date for b in bars]
    return rec, bars, dates


def test_horizon_truncation_changes_outcome():
    rec, bars, dates = _make_rec_and_bars()
    r2 = simulate_outcome_with_horizon(rec, bars, dates, horizon_days=2)
    r5 = simulate_outcome_with_horizon(rec, bars, dates, horizon_days=5)
    assert r2 is not None and r5 is not None
    # holding_days 记录真实退出日：horizon=2 持有 2 天；horizon=5 虽窗口更长，
    # 但第 3 个持仓日已触发止损，因此不能把退出后的两根 K 线计入持仓期。
    assert r2.holding_days == 2
    assert r5.holding_days == 3
    # 同一笔推荐：紧持仓上限(2天)小赚成功，松持仓上限(5天)被回吐打回净亏失败
    assert r2.outcome_label == "success"
    assert r5.outcome_label == "failed"
    assert r2.executed_return > r5.executed_return


def test_compare_horizons_dedup_invariant():
    # 退化情形：单一 horizon，推荐值应回退到 baseline
    metrics = {5: {"n": 10, "win_rate": 0.5, "avg_net_return": 1.0, "pl_ratio": 1.0, "max_loss_net": -5.0, "sharpe_per_trade": 0.3}}
    cmp = compare_horizons(metrics, baseline_horizon=5)
    assert cmp["recommended_horizon"] == 5

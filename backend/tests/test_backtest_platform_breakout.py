# -*- coding: utf-8 -*-
"""平台突破（缩量横盘+放量突破）回测脚本的纯逻辑单测 + 可选 DB 集成测试。

纯逻辑测试（_compute_metrics / _detect_platform_breakout）不依赖 DB，始终运行；
DB 集成测试在 MySQL 不可达时自动 skip。

设计纪律（见 docs/lessons/LESSONS.md 的 L-018、L-004）：本因子诚实样本外回测结果为**净负**（全样本胜率 ~42%、
滚动窗口仅 25% >50% 胜率、净累计 -8.25%），属 regime 依赖噪声，按第一性原理
「回测证伪噪声」直接丢弃，不纳入 strategy.yaml。集成测试只断言：
  (1) 回测可复现、结构正确、无未来函数（entry_date > t_date）；
  (2) 信号跨多个年份（回归 guard：daily_kbars.ma20/turnover_rate 在 2024/2025 全 NULL，
      若因子直接读 DB 列会被静默杀死全部历史信号 → 信号全挤在 2026；校内计算 ma20_calc
      后信号须分布在多年份）。
"""
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_platform_breakout import (  # noqa: E402
    Params,
    _compute_metrics,
    _detect_platform_breakout,
)


def _bar(close=10.0, high=10.6, low=9.4, open_=10.0, volume=1e6, amount=5e8,
         pct_chg=0.0, ma20_calc=10.0):
    return {
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "amount": amount, "pct_chg": pct_chg,
        "ma20_calc": ma20_calc,
    }


def _tight_window(n=15, close=10.0, vol=1e6, hi_pad=0.08, lo_pad=0.08):
    """窄幅水平横盘窗口：每根 bar 振幅极小、整体水平波动。

    hi_pad/lo_pad 很小 → 单 bar 振幅小；close 漂移 ±0.07 → 横盘区全幅
    (max_high - min_low)/mean(close) ≈ 3% << 12% 阈值，确为「平台」。
    """
    bars = []
    for i in range(n):
        c = close + (i - n // 2) * 0.01  # 微小水平波动
        bars.append(_bar(close=c, high=c + hi_pad, low=c - lo_pad, open_=c, volume=vol))
    return bars


# ───────────────────────── 纯函数：_compute_metrics ─────────────────────────
def test_compute_metrics_empty():
    assert _compute_metrics([], [1.0])["total_trades"] == 0


def test_compute_metrics_basic():
    trades = [{"net_return_pct": 5.0, "accurate": True},
              {"net_return_pct": -3.0, "accurate": False},
              {"net_return_pct": 2.0, "accurate": True}]
    m = _compute_metrics(trades, [1.0, 1.02, 1.0])
    assert m["total_trades"] == 3
    assert m["accuracy"] == pytest.approx(2 / 3 * 100, abs=1e-6)
    assert m["pl_ratio"] == pytest.approx(3.5 / 3.0, abs=1e-6)
    assert m["avg_net"] == pytest.approx((5.0 - 3.0 + 2.0) / 3, abs=1e-6)


# ──────────────────── 纯函数：_detect_platform_breakout ────────────────────
def test_detect_true_platform_breakout():
    """标准平台突破：窄幅横盘 + 缩量 + 放量突破上沿 + 站上 ma20。"""
    window = _tight_window(n=15, close=10.0, vol=1e6)
    # 突破日：收盘创横盘新高、放量、涨 8%、站上 ma20
    t_bar = _bar(close=10.9, high=11.0, low=10.8, open_=10.6, volume=2e6,
                 amount=5e8, pct_chg=8.0, ma20_calc=10.0)
    prior = _tight_window(n=15, close=10.0, vol=2e6)  # 前段均量更大 → 缩量确认
    assert _detect_platform_breakout(window, t_bar, Params(), prior_window=prior) is True


def test_detect_false_wide_range():
    """横盘区过宽（range > 12%）→ 非平台。"""
    window = _tight_window(n=15, close=10.0, vol=1e6, hi_pad=1.2, lo_pad=1.2)
    t_bar = _bar(close=11.5, high=11.6, low=11.3, open_=11.4, volume=2e6,
                 amount=5e8, pct_chg=8.0, ma20_calc=10.0)
    prior = _tight_window(n=15, close=10.0, vol=2e6)
    assert _detect_platform_breakout(window, t_bar, Params(), prior_window=prior) is False


def test_detect_false_no_breakout():
    """突破日收盘未创横盘新高 → 非突破。"""
    window = _tight_window(n=15, close=10.0, vol=1e6)
    # 突破日收盘未达横盘区新高（hh≈10.15），但量/涨幅/ma20 均达标 → 仅因「非突破」判 False
    t_bar = _bar(close=10.1, high=10.12, low=10.0, open_=10.05, volume=2e6,
                 amount=5e8, pct_chg=5.0, ma20_calc=10.0)
    prior = _tight_window(n=15, close=10.0, vol=2e6)
    assert _detect_platform_breakout(window, t_bar, Params(), prior_window=prior) is False


def test_detect_false_no_volume_surge():
    """突破日未放量（量 < 1.8x 横盘均量）→ 非确认突破。"""
    window = _tight_window(n=15, close=10.0, vol=1e6)
    t_bar = _bar(close=10.8, high=10.9, low=10.7, open_=10.6, volume=1.5e6,
                 amount=5e8, pct_chg=8.0, ma20_calc=10.0)
    prior = _tight_window(n=15, close=10.0, vol=2e6)
    assert _detect_platform_breakout(window, t_bar, Params(), prior_window=prior) is False


def test_detect_false_below_ma20():
    """突破日未站上 ma20（受 ma20_align 约束）→ 非上升基座突破。"""
    window = _tight_window(n=15, close=10.0, vol=1e6)
    t_bar = _bar(close=10.8, high=10.9, low=10.7, open_=10.6, volume=2e6,
                 amount=5e8, pct_chg=8.0, ma20_calc=11.5)  # ma20 远高于收盘价
    prior = _tight_window(n=15, close=10.0, vol=2e6)
    assert _detect_platform_breakout(window, t_bar, Params(), prior_window=prior) is False


def test_detect_false_limit_up():
    """突破日为涨停板（一字买不到）→ 跳过。"""
    window = _tight_window(n=15, close=10.0, vol=1e6)
    t_bar = _bar(close=11.0, high=11.0, low=10.9, open_=11.0, volume=2e6,
                 amount=5e8, pct_chg=10.0, ma20_calc=10.0)  # high==close 涨停
    prior = _tight_window(n=15, close=10.0, vol=2e6)
    assert _detect_platform_breakout(window, t_bar, Params(), prior_window=prior) is False


def test_detect_false_dryup_missing():
    """横盘期未缩量（量 > 0.75x 前段）→ 供给未枯竭，非蓄势。"""
    window = _tight_window(n=15, close=10.0, vol=2e6)  # 横盘均量 = 前段 → 不满足 dryup
    t_bar = _bar(close=10.8, high=10.9, low=10.7, open_=10.6, volume=4e6,
                 amount=5e8, pct_chg=8.0, ma20_calc=10.0)
    prior = _tight_window(n=15, close=10.0, vol=2e6)
    assert _detect_platform_breakout(window, t_bar, Params(), prior_window=prior) is False


# ───────────────────────────── DB 集成测试 ──────────────────────────────────
def _db_reachable():
    try:
        from sqlalchemy import text
        from app.db.session import engine
        if engine.dialect.name != "mysql":
            return False
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="MySQL 不可达（无网/CI 环境）")
def test_integration_single_backtest_runs_and_spans_years():
    """集成测试：跑一次全量回测，断言结构正确、无未来函数、信号跨多年份。

    关键回归 guard：若因子直接读 daily_kbars.ma20（2024/2025 全 NULL），历史信号会被
    静默杀死、全部挤在 2026；校内计算 ma20_calc 后信号须分布在 >=2 个年份。
    """
    from app.db.session import SessionLocal
    from scripts.backtest_platform_breakout import (
        _load_data, _precompute_signals, _simulate, START_DATE, END_DATE,
    )

    with SessionLocal() as db:
        data = _load_data(db, START_DATE, END_DATE)
        signals = _precompute_signals(data, Params())
        # 无未来函数：每条信号 entry_date 都 > t_date
        for entry_date, sigs in signals.items():
            for s in sigs:
                assert s["entry_date"] > s["t_date"]
        metrics, trades, pv = _simulate(data, data["trade_dates"], signals)

    assert "total_trades" in metrics
    assert 0 <= metrics["accuracy"] <= 100
    assert metrics["pl_ratio"] >= 0
    assert len(trades) == metrics["total_trades"]
    # 回归 guard：信号须跨 >=2 个年份（数据质量修复后的正确性证据）
    yrs = Counter(d[:4] for d in signals.keys())
    assert len(yrs) >= 2, f"信号仅分布在 {dict(yrs)} —— 疑似历史指标缺失导致信号塌缩"
    assert metrics["total_trades"] >= 50  # 样本量足以做统计判断

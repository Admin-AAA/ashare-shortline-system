# -*- coding: utf-8 -*-
"""放量新高突破（趋势延续）回测脚本的纯逻辑单测 + 可选 DB 集成测试。

纯逻辑测试（_compute_metrics / _build_indicators / _prior_high /
_passes_uptrend_alignment / _passes_new_high / _passes_volume）不依赖 DB，始终运行；
DB 集成测试在 MySQL 不可达时自动 skip。

设计纪律（见 lessons.md [T1]）：本因子诚实样本外回测结果为**净负**（全样本胜率 ~41.8%、
滚动窗口仅 12% >50% 胜率、净累计 -21.3%），属 regime 依赖噪声，按第一性原理
「回测证伪噪声」直接丢弃，不纳入 strategy.yaml。集成测试只断言：
  (1) 回测可复现、结构正确、无未来函数（entry_date > t_date）；
  (2) 信号跨多个年份（回归 guard：daily_kbars.ma20/ma60 在 2023-2025 全 NULL，
      若因子直接读 DB 列会被静默杀死全部历史信号 → 信号全挤在 2026；校内计算 ma20/ma60
      后信号须分布在多年份）。
"""
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_momentum_breakout import (  # noqa: E402
    LOOKBACK,
    _build_indicators,
    _compute_metrics,
    _passes_new_high,
    _passes_uptrend_alignment,
    _passes_volume,
    _prior_high,
)


class _Bar:
    def __init__(self, **kw):
        self.open = kw.get("open", 10.0)
        self.high = kw.get("high", 10.5)
        self.low = kw.get("low", 9.5)
        self.close = kw.get("close", 10.0)
        self.pct_chg = kw.get("pct_chg", 0.0)
        self.amount = kw.get("amount", 5e8)
        self.volume = kw.get("volume", 1e6)


def _ind(ma20=10.0, ma60=9.8, vr=1.0):
    return {"ma20": ma20, "ma60": ma60, "vol_ratio_5": vr}


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


# ───────────────────────── 纯函数：_prior_high ─────────────────────────
def test_prior_high_excludes_t():
    """_prior_high 取 T 之前 lookback 根的最高价，不含 T 本身（无未来函数）。"""
    bars = [_Bar(high=10.0 + k) for k in range(LOOKBACK + 2)]
    # T 在索引 LOOKBACK+1，其 high 故意拉高；prior_high 应只取 [0, LOOKBACK] 的最高
    bars[LOOKBACK + 1].high = 999.0
    ph = _prior_high(bars, LOOKBACK + 1, LOOKBACK)
    assert ph == pytest.approx(10.0 + LOOKBACK, abs=1e-9)
    assert ph != 999.0


# ───────────────────────── 纯函数：趋势多头排列 ─────────────────────────
def test_passes_uptrend_alignment_true():
    """MA20>MA60 且 close>MA20 → 多头排列成立。"""
    b = _Bar(open=10.2, high=10.4, low=10.1, close=10.3)
    assert _passes_uptrend_alignment(b, _ind(ma20=10.0, ma60=9.8)) is True


def test_passes_uptrend_alignment_false_ma_cross():
    """MA20 <= MA60（短期均线下穿）→ 非多头排列。"""
    b = _Bar(close=10.3)
    assert _passes_uptrend_alignment(b, _ind(ma20=9.8, ma60=10.0)) is False


def test_passes_uptrend_alignment_false_below_ma20():
    """close <= MA20（价格跌破短期均线）→ 非多头排列。"""
    b = _Bar(close=9.5)
    assert _passes_uptrend_alignment(b, _ind(ma20=10.0, ma60=9.8)) is False


def test_passes_uptrend_alignment_decoupled_from_corrupted_column():
    """关键回归：bar 无 ma 列（corrupted 列静默为 0）时，只要内存 ind 有效就应判通过——
    证明因子不依赖 daily_kbars.ma20/ma60 列（2023-2025 全 NULL 的数据坑）。"""
    b = _Bar(close=10.3)
    assert _passes_uptrend_alignment(b, _ind(ma20=10.0, ma60=9.8)) is True
    assert _passes_uptrend_alignment(b, _ind(ma20=0.0, ma60=0.0)) is False


# ───────────────────────── 纯函数：放量突破近 N 日新高 ─────────────────────────
def _prior_bars():
    # 近 N 日最高价封顶在 10.95（k=19 时 10.0+19*0.05），T 需 >10.95 才算新高
    return [_Bar(high=10.0 + k * 0.05, low=9.5 + k * 0.05, close=9.8 + k * 0.05)
            for k in range(LOOKBACK)]


def test_passes_new_high_true():
    """T 创近 N 日新高 + 强收盘 → 突破成立。"""
    bars = _prior_bars()
    t = _Bar(open=11.0, high=11.2, low=11.0, close=11.15)  # 新高(>10.95) + 强收盘
    bars.append(t)
    assert _passes_new_high(t, bars, len(bars) - 1) is True


def test_passes_new_high_false_not_new_high():
    """T 未创近 N 日新高 → 非突破。"""
    bars = _prior_bars()
    t = _Bar(open=10.5, high=10.6, low=10.4, close=10.55)  # 低于 prior high 10.95
    bars.append(t)
    assert _passes_new_high(t, bars, len(bars) - 1) is False


def test_passes_new_high_false_weak_close():
    """T 创近 N 日新高但收盘回落（上影长）→ 非强收盘，判否。"""
    bars = _prior_bars()
    # 新高 high=11.2(>10.95)，但收盘 close=10.6 远离低点 10.0 → close < low + 0.55*range
    t = _Bar(open=11.0, high=11.2, low=10.0, close=10.6)
    bars.append(t)
    assert _passes_new_high(t, bars, len(bars) - 1) is False


# ───────────────────────── 纯函数：量能确认 ─────────────────────────
def test_passes_volume_true():
    assert _passes_volume(_Bar(), _ind(vr=2.0)) is True


def test_passes_volume_false_no_surge():
    assert _passes_volume(_Bar(), _ind(vr=1.2)) is False


def test_passes_volume_false_zero():
    assert _passes_volume(_Bar(), _ind(vr=0.0)) is False


# ───────────────────────── 内存指标：_build_indicators ─────────────────────────
def test_build_indicators_ma20_ma60_vol_ratio():
    """_build_indicators 内存重算 MA20/MA60/量比5，不读 corrupted 列；ma20(i) 只用历史。"""
    class B:
        def __init__(self, td, close, volume):
            self.trade_date = td
            self.close = close
            self.volume = volume
    dates = [f"2026-01-{d:02d}" for d in range(1, 22)]  # 21 天
    bars = [B(d, 10.0, 100.0) for d in dates]
    by_code = {"X": bars}
    cache = _build_indicators(by_code)
    ind = cache[("X", "2026-01-21")]
    assert ind["ma20"] == pytest.approx(10.0, abs=1e-6)
    assert ind["ma60"] == 0.0
    assert ind["vol_ratio_5"] == pytest.approx(1.0, abs=1e-6)
    # ma20(i) 只用 bars[0..i]（无未来函数）
    bars[0].close = 20.0
    cache2 = _build_indicators(by_code)
    assert cache2[("X", "2026-01-21")]["ma20"] == pytest.approx(10.0, abs=1e-6)


# ───────────────────────────── DB 集成测试 ──────────────────────────────────
def _db_reachable():
    try:
        from sqlalchemy import text
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="MySQL 不可达（无网/CI 环境）")
def test_integration_single_backtest_runs_and_spans_years():
    """集成测试：跑一次全量回测，断言结构正确、无未来函数、信号跨多年份。

    关键回归 guard：若因子直接读 daily_kbars.ma20/ma60（2023-2025 全 NULL），历史信号会被
    静默杀死、全部挤在 2026；校内计算后信号须分布在 >=2 个年份。
    """
    from app.db.session import SessionLocal
    from scripts.backtest_momentum_breakout import (
        _load_data, _precompute_signals, _simulate, START_DATE, END_DATE,
    )

    with SessionLocal() as db:
        data = _load_data(db, START_DATE, END_DATE)
        signals = _precompute_signals(data)
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

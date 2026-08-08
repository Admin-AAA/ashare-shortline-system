# -*- coding: utf-8 -*-
"""长下影支撑反转（MA20 锤子线）回测脚本的纯逻辑单测 + 可选 DB 集成测试。

纯逻辑测试（_compute_metrics / _is_hammer / _passes_support_trend）不依赖 DB，始终运行；
DB 集成测试在 MySQL 不可达时自动 skip，避免 CI / 无网环境失败。

设计纪律（见 lessons.md [T1]）：本因子与「分歧日低吸」「首板接力」同属 2023-2026 短线因子
样本外坍塌高发区。集成测试只断言回测可复现且结构正确，**不断言胜率必须 >50%**——
避免把测试变成过拟合锁。若全样本/滚动窗口显示净负或 regime 依赖噪声，按第一性原理
「回测证伪噪声」直接丢弃，不纳入 strategy.yaml。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_hammer_support import (  # noqa: E402
    _compute_metrics,
    _is_hammer,
    _passes_support_trend,
    _build_indicators,
)


class _Bar:
    def __init__(self, **kw):
        self.open = kw.get("open", 10.0)
        self.high = kw.get("high", 10.5)
        self.low = kw.get("low", 9.5)
        self.close = kw.get("close", 10.0)
        self.ma20 = kw.get("ma20", 10.0)
        self.ma60 = kw.get("ma60", 9.8)
        self.pct_chg = kw.get("pct_chg", 0.0)
        self.amount = kw.get("amount", 5e8)
        self.turnover_rate = kw.get("turnover_rate", 3.0)
        self.vol_ratio_5 = kw.get("vol_ratio_5", 1.0)


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


def test_is_hammer_true():
    """标准锤子线：长下影、小实体、极小上影。open=10 high=10.2 low=9.0 close=10.1"""
    b = _Bar(open=10.0, high=10.2, low=9.0, close=10.1)
    assert _is_hammer(b) is True


def test_is_hammer_false_no_lower_shadow():
    """无下影（光头光脚阳线）不是锤子线。"""
    b = _Bar(open=10.0, high=10.5, low=10.0, close=10.4)
    assert _is_hammer(b) is False


def test_is_hammer_false_big_upper_shadow():
    """上影过长（射击之星）不是锤子线。"""
    b = _Bar(open=10.0, high=11.5, low=9.85, close=10.05)
    # 下影=0.15（close10.05-open10=0.05, low9.85 → min-lower=0.2），上影=1.45 >> 0.2
    assert _is_hammer(b) is False


def test_is_hammer_true_doji_small_body():
    """极小实体（十字星）下影长也成立。open=close=10 high=10.15 low=9.2"""
    b = _Bar(open=10.0, high=10.15, low=9.2, close=10.0)
    assert _is_hammer(b) is True


def test_passes_support_trend_true():
    """上升趋势中回调至 MA20 支撑：close 刚跌破 ma20、最低精准回踩。"""
    b = _Bar(open=10.0, high=10.1, low=9.95, close=9.97, ma20=10.0, ma60=9.8)
    assert _passes_support_trend(b, {"ma20": b.ma20, "ma60": b.ma60}) is True


def test_passes_support_trend_false_below_band():
    """close 远低于 MA20 支撑带（崩坏），不通过。"""
    b = _Bar(open=9.0, high=9.1, low=8.9, close=9.0, ma20=10.0, ma60=9.8)
    assert _passes_support_trend(b, {"ma20": b.ma20, "ma60": b.ma60}) is False


def test_passes_support_trend_false_broken_ma60():
    """close 跌破 ma60 长期趋势，不通过。"""
    b = _Bar(open=9.0, high=9.1, low=8.95, close=9.2, ma20=9.5, ma60=10.0)
    assert _passes_support_trend(b, {"ma20": b.ma20, "ma60": b.ma60}) is False


def test_passes_support_trend_false_low_not_tagging():
    """最低未回踩到 MA20（下影未打到支撑），不通过。"""
    b = _Bar(open=10.0, high=10.1, low=10.0, close=10.0, ma20=10.0, ma60=9.8)
    assert _passes_support_trend(b, {"ma20": b.ma20, "ma60": b.ma60}) is False


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
    # 第 20 位（索引20）前 20 日 close 全 10 → ma20=10
    ind = cache[("X", "2026-01-21")]
    assert ind["ma20"] == pytest.approx(10.0, abs=1e-6)
    # 数据不足 60 日 → ma60=0
    assert ind["ma60"] == 0.0
    # 量全 100 → 量比5 = 1.0
    assert ind["vol_ratio_5"] == pytest.approx(1.0, abs=1e-6)
    # ma20(i) 只用 bars[0..i]（无未来函数）：改 bars[0]（在窗口外）不影响 ma20@index20
    bars[0].close = 20.0
    cache2 = _build_indicators(by_code)
    assert cache2[("X", "2026-01-21")]["ma20"] == pytest.approx(10.0, abs=1e-6)  # 不受窗口外日影响


def test_passes_support_trend_decoupled_from_corrupted_column():
    """关键回归：bar.ma20=0（被 corrupted 列静默杀死）时，只要内存 ind 有效就应判通过——
    证明因子不再依赖 daily_kbars.ma20/ma60 列（2023-2025 全 NULL 的数据坑）。"""
    b = _Bar(open=10.0, high=10.1, low=9.95, close=9.97, ma20=0.0, ma60=0.0)  # 列已坏
    ind = {"ma20": 10.0, "ma60": 9.8}  # 内存重算有效
    assert _passes_support_trend(b, ind) is True
    # 反例：ind 也坏才判否
    assert _passes_support_trend(b, {"ma20": 0.0, "ma60": 0.0}) is False


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
def test_integration_single_backtest_runs():
    """集成测试：跑一次全量回测，断言指标结构合理、无异常、无未来函数。

    诚实结论取决于实际数据：若全样本胜率不足 / 滚动窗口多数 <50% 胜率 / 均净为负，
    属 regime 依赖噪声，不纳入实盘。此处仅断言回测可复现且结构正确。
    """
    from app.db.session import SessionLocal
    from scripts.backtest_hammer_support import _load_data, _precompute_signals, _simulate

    with SessionLocal() as db:
        data = _load_data(db, "2023-01-01", "2026-06-24")
        signals = _precompute_signals(data)
        # 交叉验证：预计算信号里不存在「用未来数据」——每条信号 entry_date 都 > t_date
        for entry_date, sigs in signals.items():
            for s in sigs:
                assert s["entry_date"] > s["t_date"]
        metrics, trades, pv = _simulate(data, data["trade_dates"], signals)
    assert "total_trades" in metrics
    assert 0 <= metrics["accuracy"] <= 100
    assert metrics["pl_ratio"] >= 0
    assert len(trades) == metrics["total_trades"]
    assert metrics["total_trades"] >= 0

# -*- coding: utf-8 -*-
"""放量长上影试盘线（仙人指路）回测脚本的纯逻辑单测 + 可选 DB 集成测试。

纯逻辑测试（_compute_metrics / _is_upper_probe / _passes_volume_trend / _passes_liquidity）
不依赖 DB，始终运行；DB 集成测试在 MySQL 不可达时自动 skip。

设计纪律（见 docs/lessons/LESSONS.md 的 L-018、L-004）：本因子与「长下影锤子线」「分歧日低吸」「首板接力」同属
2023-2026 短线因子样本外坍塌高发区。集成测试只断言回测可复现、无未来函数、结构正确，
**不断言胜率必须 >50%**——避免把测试变成过拟合锁。若全样本/滚动窗口显示净负或
regime 依赖噪声，按第一性原理「回测证伪噪声」直接丢弃，不纳入 strategy.yaml。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_upper_shadow_probe import (  # noqa: E402
    _compute_metrics,
    _is_upper_probe,
    _passes_volume_trend,
    _passes_liquidity,
)


class _Bar:
    def __init__(self, **kw):
        self.open = kw.get("open", 10.0)
        self.high = kw.get("high", 10.5)
        self.low = kw.get("low", 9.5)
        self.close = kw.get("close", 10.2)
        self.ma20 = kw.get("ma20", 10.0)
        self.pct_chg = kw.get("pct_chg", 1.0)
        self.amount = kw.get("amount", 5e8)
        self.turnover_rate = kw.get("turnover_rate", 3.0)
        self.vol_ratio_5 = kw.get("vol_ratio_5", 2.0)


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


def test_is_upper_probe_true():
    """标准试盘线：长上影、小实体、极小下影。open=10 high=11.0 low=9.9 close=10.2"""
    b = _Bar(open=10.0, high=11.0, low=9.9, close=10.2)
    assert _is_upper_probe(b) is True


def test_is_upper_probe_false_no_upper_shadow():
    """无上影（收在最高附近），不是试盘线。"""
    b = _Bar(open=10.0, high=10.2, low=9.9, close=10.18)
    assert _is_upper_probe(b) is False


def test_is_upper_probe_false_big_lower_shadow():
    """下影过长（纺锤/十字），不是试盘线。"""
    b = _Bar(open=10.0, high=11.0, low=9.0, close=10.2)
    assert _is_upper_probe(b) is False


def test_is_upper_probe_true_doji_small_body():
    """实体极小（十字长上影），放宽后仍为试盘线。"""
    b = _Bar(open=10.1, high=11.0, low=10.05, close=10.1)
    assert _is_upper_probe(b) is True


def test_passes_volume_trend_true():
    b = _Bar(pct_chg=1.5, vol_ratio_5=2.0, ma20=10.0, close=10.2)
    assert _passes_volume_trend(b) is True


def test_passes_volume_trend_false_limit_up():
    """涨停（pct>=9.5）是另一类信号，试盘线应排除。"""
    b = _Bar(pct_chg=10.0, vol_ratio_5=2.0, ma20=10.0, close=11.0)
    assert _passes_volume_trend(b) is False


def test_passes_volume_trend_false_low_volume():
    b = _Bar(pct_chg=1.5, vol_ratio_5=1.0, ma20=10.0, close=10.2)
    assert _passes_volume_trend(b) is False


def test_passes_volume_trend_false_below_ma20():
    b = _Bar(pct_chg=1.5, vol_ratio_5=2.0, ma20=11.0, close=10.2)
    assert _passes_volume_trend(b) is False


def test_passes_liquidity_true():
    b = _Bar(amount=5e8, turnover_rate=3.0)
    assert _passes_liquidity(b) is True


def test_passes_liquidity_false_low_amount():
    b = _Bar(amount=1e8, turnover_rate=3.0)
    assert _passes_liquidity(b) is False


def test_passes_liquidity_false_high_amount():
    """超大盘（>200亿）排除。"""
    b = _Bar(amount=500e8, turnover_rate=3.0)
    assert _passes_liquidity(b) is False


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
@pytest.mark.requires_market_data
def test_integration_single_backtest_runs():
    """集成测试：跑一次全量回测，断言指标结构合理、无未来函数、无异常。

    诚实结论取决于实际数据：若全样本胜率不足 / 滚动窗口多数 <50% 胜率 / 均净为负，
    属 regime 依赖噪声，不纳入实盘。此处仅断言回测可复现且结构正确。
    """
    from app.db.session import SessionLocal
    from scripts.backtest_upper_shadow_probe import _load_data, _precompute_signals, _simulate

    with SessionLocal() as db:
        data = _load_data(db, "2021-05-26", "2026-07-20")
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
    assert metrics["total_trades"] >= 0

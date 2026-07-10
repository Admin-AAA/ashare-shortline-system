# -*- coding: utf-8 -*-
"""低位首板接力回测脚本的纯逻辑单测 + 可选 DB 集成测试。

纯逻辑测试（_compute_metrics / _prior_limit_up）不依赖 DB，始终运行；
DB 集成测试在远程 MySQL 不可达时自动 skip，避免 CI / 无网环境失败。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_first_limit_up import (  # noqa: E402
    _compute_metrics,
    _prior_limit_up,
)


def _trade(net, accurate):
    return {"net_return_pct": net, "accurate": accurate, "exit_reason": "x", "holding_days": 3,
            "entry_date": "2026-01-01", "exit_date": "2026-01-05", "code": "600000",
            "name": "x", "industry": "y", "entry_price": 10.0, "exit_price": 10.0}


def test_compute_metrics_empty():
    assert _compute_metrics([], [1.0], 100000.0)["total_trades"] == 0


def test_compute_metrics_basic():
    trades = [_trade(5.0, True), _trade(-3.0, False), _trade(2.0, True)]
    m = _compute_metrics(trades, [1.0, 1.02, 1.0], 100000.0)
    assert m["total_trades"] == 3
    assert m["accuracy"] == pytest.approx(2 / 3 * 100, abs=1e-6)
    # wins avg = (5+2)/2 = 3.5 ; loss abs = 3 -> pl = 1.1667
    assert m["pl_ratio"] == pytest.approx(3.5 / 3.0, abs=1e-6)
    assert m["avg_net"] == pytest.approx((5.0 - 3.0 + 2.0) / 3, abs=1e-6)


def test_prior_limit_up_detects_recent_board():
    # 用简单对象模拟 DailyKbar，只需 trade_date / pct_chg
    class B:
        def __init__(self, td, pct):
            self.trade_date = td
            self.pct_chg = pct
    bars = [B("2026-01-01", 1.0), B("2026-01-02", 2.0), B("2026-01-03", 3.0),
            B("2026-01-04", 9.8), B("2026-01-05", 3.0)]
    dates = [f"2026-01-0{i}" for i in range(1, 6)]
    idx = {d: i for i, d in enumerate(dates)}
    # 2026-01-05 往前 5 日内有 01-04 涨停 -> True
    assert _prior_limit_up(bars, "2026-01-05", idx, 5) is True
    # 2026-01-02 往前 5 日内无涨停 -> False
    assert _prior_limit_up(bars, "2026-01-02", idx, 5) is False
    # lookback=1 时 01-05 只看 01-04 -> True；01-03 只看 01-02 -> False
    assert _prior_limit_up(bars, "2026-01-05", idx, 1) is True
    assert _prior_limit_up(bars, "2026-01-03", idx, 1) is False


def _db_reachable():
    try:
        import sqlalchemy
        from sqlalchemy import create_engine, text
        url = os.environ.get("DATABASE_URL",
                             "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4")
        e = create_engine(url, pool_pre_ping=True)
        with e.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_reachable(), reason="远程 MySQL 不可达（无网/CI 环境）")
def test_integration_single_backtest_runs():
    """集成测试：跑一次全量回测，断言指标结构合理、无异常。"""
    from scripts.backtest_first_limit_up import _load_data, _simulate
    import os
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    url = os.environ.get("DATABASE_URL",
                         "mysql+pymysql://aowang:1279990918@139.196.242.53:4407/ashare_workbench?charset=utf8mb4")
    engine = create_engine(url, pool_pre_ping=True)
    with Session(engine) as db:
        data = _load_data(db, "2023-01-01", "2026-06-24")
        metrics, trades, pv = _simulate(data, data["trade_dates"])
    assert "total_trades" in metrics
    assert 0 <= metrics["accuracy"] <= 100
    assert metrics["pl_ratio"] >= 0
    assert len(trades) == metrics["total_trades"]
    # 样本外稳健性结论：本因子全样本胜率接近 50%、滚动窗口仅约 1/3 >50%，
    # 属 regime 依赖噪声，不纳入实盘策略（见 scripts/backtest_first_limit_up_report.md）。
    # 此处仅断言回测可复现且结构正确，不断言胜率必须 >50%（避免把测试变成过拟合锁）。
    assert metrics["total_trades"] >= 0

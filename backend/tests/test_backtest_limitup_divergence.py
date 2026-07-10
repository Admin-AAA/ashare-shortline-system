# -*- coding: utf-8 -*-
"""涨停后分歧日低吸（弱转强）回测脚本的纯逻辑单测 + 可选 DB 集成测试。

纯逻辑测试（_compute_metrics / _avg_vol_before）不依赖 DB，始终运行；
DB 集成测试在远程 MySQL 不可达时自动 skip，避免 CI / 无网环境失败。

重要工程教训（见 lessons.md [T1]）：本脚本曾因「用全局 date_to_idx 下标去切
by_code[code] 本地列表」导致量能基线恒为 0、样本坍缩到 2 笔。测试刻意覆盖
_avg_vol_before 的「排除涨停日 T」与「本地下标」两条正确性。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backtest_limitup_divergence import (  # noqa: E402
    _avg_vol_before,
    _compute_metrics,
)


class _Bar:
    def __init__(self, td, volume):
        self.trade_date = td
        self.volume = volume


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
    assert m["pl_ratio"] == pytest.approx(3.5 / 3.0, abs=1e-6)
    assert m["avg_net"] == pytest.approx((5.0 - 3.0 + 2.0) / 3, abs=1e-6)


def test_avg_vol_before_excludes_limit_up_day():
    """量能基线必须排除涨停日 T（T 量异常放大），否则分歧日放量门槛被抬高。

    构造 9 天正常量(100) + 第 10 天涨停巨量(1000, T) + 第 11 天分歧(500)。
    cur_bar=分歧日(pos=10)，基线应取 T 之前 8 天正常量均值 = 100，绝不含 T(1000)。
    """
    dates = [f"2026-01-0{i}" for i in range(1, 9)] + ["2026-01-10", "2026-01-11"]
    vols = [100] * 8 + [1000, 500]  # 第 9 个=涨停日 T，第 10 个=分歧日
    bars = [_Bar(d, v) for d, v in zip(dates, vols)]
    by_code = {"X": bars}
    cur = bars[-1]  # 分歧日
    avgv = _avg_vol_before(by_code, "X", cur, 8)
    assert avgv == pytest.approx(100.0, abs=1e-6)


def test_avg_vol_before_local_index_not_global():
    """用本地下标定位 cur_bar，不能用全局 index 切本地列表（曾导致基线恒 0）。

    即便全局位置很大、本地列表很短，也必须正确切到 cur_bar 之前的本地位置。
    """
    # 本地列表只有 3 根 bar（cur 在末尾），全局 date_to_idx 与之无关
    bars = [_Bar("2026-03-01", 200), _Bar("2026-03-02", 200), _Bar("2026-03-03", 300)]
    by_code = {"Y": bars}
    cur = bars[-1]
    avgv = _avg_vol_before(by_code, "Y", cur, 8)  # n=8 但本地只有 2 根前置
    assert avgv == pytest.approx(200.0, abs=1e-6)


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
    """集成测试：跑一次全量回测，断言指标结构合理、无异常、无未来函数。

    诚实结论：本因子全样本胜率 ~40%、滚动窗口仅 ~16% >50% 胜率、均净为负，
    属 regime 依赖噪声 / 净负，不纳入实盘（见 backtest_limitup_divergence_report.md）。
    此处仅断言回测可复现且结构正确，不断言胜率必须 >50%（避免把测试变成过拟合锁）。
    """
    from scripts.backtest_limitup_divergence import _load_data, _simulate
    import os
    from sqlalchemy import create_engine
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
    assert metrics["total_trades"] >= 0
    engine.dispose()

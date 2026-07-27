"""记录中心 records_service 收益率计算测试。

验证 1/3/5日 + 至今收益率计算逻辑，含除权安全（pct_chg 链式累乘）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.records_service import _compute_returns


class _Bar:
    """轻量 DailyKbar 替身，避免依赖 DB。"""
    def __init__(self, trade_date: str, close: float, pct_chg: float):
        self.trade_date = trade_date
        self.close = close
        self.pct_chg = pct_chg


def test_compute_returns_basic():
    """1/3/5日 + 至今收益率基本计算。"""
    # 推荐价 100，后续 5 天每天涨 1%
    bars = [_Bar("2026-06-01", 100.0, 0.0)]  # 推荐日 T+0
    pcts = [1.0, 1.0, 1.0, 1.0, 1.0]
    for i, p in enumerate(pcts, start=1):
        bars.append(_Bar(f"2026-06-0{i+1}", 100 * (1.01 ** i), p))

    r = _compute_returns(100.0, bars, "2026-06-06")
    # 1日：1.01 - 1 = 1%
    assert r["return_1d"] == pytest.approx(1.0, abs=0.01)
    # 3日：1.01^3 - 1 ≈ 3.03%
    assert r["return_3d"] == pytest.approx(3.03, abs=0.01)
    # 5日：1.01^5 - 1 ≈ 5.10%
    assert r["return_5d"] == pytest.approx(5.10, abs=0.02)
    # 至今 = 5日（只有5天未来数据）
    assert r["return_to_date"] == pytest.approx(5.10, abs=0.02)
    assert r["latest_date"] == "2026-06-06"
    assert r["available_bars"] == 6


def test_compute_returns_insufficient_bars():
    """不足 N 天时对应收益率为 None。"""
    bars = [_Bar("2026-06-01", 100.0, 0.0), _Bar("2026-06-02", 101.0, 1.0)]
    r = _compute_returns(100.0, bars, "2026-06-02")
    assert r["return_1d"] == pytest.approx(1.0, abs=0.01)
    assert r["return_3d"] is None
    assert r["return_5d"] is None
    # 至今仍可算（1天）
    assert r["return_to_date"] == pytest.approx(1.0, abs=0.01)


def test_compute_returns_ex_rights_safe():
    """除权日：close 跳变但 pct_chg 已调整，链式累乘不受虚假暴跌影响。"""
    # 推荐价 100，除权日 close 跳到 50（10送10），但 pct_chg=0（除权调整后）
    bars = [
        _Bar("2026-06-01", 100.0, 0.0),
        _Bar("2026-06-02", 50.0, 0.0),   # 除权日：close 腰斩但 pct_chg=0
        _Bar("2026-06-03", 51.0, 2.0),   # 次日涨 2%
    ]
    r = _compute_returns(100.0, bars, "2026-06-03")
    # 用 pct_chg 链式：1日=0%，2日=2%（而不是 close 序列的 -49%）
    assert r["return_1d"] == pytest.approx(0.0, abs=0.01)
    assert r["return_3d"] is None  # 只有2天未来数据
    assert r["return_to_date"] == pytest.approx(2.0, abs=0.01)


def test_compute_returns_negative():
    """下跌场景。"""
    bars = [
        _Bar("2026-06-01", 100.0, 0.0),
        _Bar("2026-06-02", 98.0, -2.0),
        _Bar("2026-06-03", 96.0, -2.04),
    ]
    r = _compute_returns(100.0, bars, "2026-06-03")
    assert r["return_1d"] == pytest.approx(-2.0, abs=0.01)
    assert r["return_3d"] is None
    assert r["return_to_date"] is not None
    assert r["return_to_date"] < 0


def test_compute_returns_empty():
    """无 K 线数据。"""
    r = _compute_returns(100.0, [], "2026-06-06")
    assert r["return_1d"] is None
    assert r["return_to_date"] is None
    assert r["available_bars"] == 0


def test_compute_returns_zero_price():
    """推荐价为 0（异常数据）不计算。"""
    bars = [_Bar("2026-06-01", 0.0, 0.0), _Bar("2026-06-02", 10.0, 0.0)]
    r = _compute_returns(0.0, bars, "2026-06-02")
    assert r["return_1d"] is None
    assert r["return_to_date"] is None


# ── #9: 矩阵记录列表排除 single_stock ──────────────────────────────────────

def test_list_matrix_records_excludes_single_stock():
    """记录中心矩阵 tab 须排除 single_stock，避免单股票评分历史稀释矩阵分析历史。"""
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.tables import StockMatrixHistory
    from app.services.records_service import list_matrix_records

    engine = create_engine("sqlite:///:memory:")
    StockMatrixHistory.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(StockMatrixHistory(code="000001", trade_date="2026-07-25", analysis_type="analysis",
                              final_score=80.0, skill_count=1, result_json="{}", elapsed_ms=10))
    db.add(StockMatrixHistory(code="000002", trade_date="2026-07-25", analysis_type="single_stock",
                              final_score=38.0, skill_count=1, result_json="{}", elapsed_ms=10))
    db.commit()

    out = list_matrix_records(db, page=1, page_size=20, exclude_analysis_type="single_stock")
    assert out["total"] == 1
    assert out["items"][0]["analysis_type"] == "analysis"

    # 不传 exclude 时仍返回全部（兼容其它调用方）
    out_all = list_matrix_records(db, page=1, page_size=20)
    assert out_all["total"] == 2

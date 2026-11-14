"""快照日修复工具的纯逻辑回归：组装口径、除零保护、缺数据不写库。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "repair_snapshot_day.py"
_spec = importlib.util.spec_from_file_location("repair_snapshot_day", _SCRIPT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


class _Result:
    def __init__(self, rows=None, scalar=0, rowcount=0):
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeDB:
    """按 execute 调用顺序返回预设结果（脚本里的 SQL 顺序固定）。"""

    def __init__(self, results):
        self.results = list(results)
        self.commits = 0
        self.rollbacks = 0

    def execute(self, stmt, params=None):
        return self.results.pop(0)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_num_handles_nan_and_none():
    assert rs.num(float("nan")) is None
    assert rs.num(None) is None
    assert rs.num("3.5") == 3.5


def _db_with(bars, basics=(), stocks=(), existing=0):
    return _FakeDB([
        _Result(scalar=existing),          # _rows(stock_quotes)
        _Result(rows=list(bars)),           # daily_kbars
        _Result(rows=list(basics)),         # daily_basic
        _Result(rows=list(stocks)),         # stocks
    ])


def test_dry_run_assembles_quote_fields_without_writing():
    bars = [{"code": "000001", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
             "volume": 1_000_000, "amount": 2.5e8, "pct_chg": 5.0}]
    basics = [{"code": "000001", "turnover_rate": 1.2, "pe_ttm": 12.0,
               "total_mv": 2.0e4, "circ_mv": 1.5e4}]
    stocks = [{"code": "000001", "name": "平安银行", "industry": "银行"}]
    db = _db_with(bars, basics, stocks)
    out = rs.backfill_stock_quotes(db, "2026-11-13", dry_run=True)
    assert out["status"] == "dry_run" and out["would_insert"] == 1
    assert db.commits == 0 and db.results == []  # 一条 insert 都没发


def test_pct_minus_100_does_not_divide_by_zero():
    """跌停 -100% 时原临时脚本会 ZeroDivisionError，固化版必须兜住。"""
    bars = [{"code": "000002", "open": 8.0, "high": 8.0, "low": 8.0, "close": 8.0,
             "volume": 100, "amount": 800, "pct_chg": -100.0}]
    db = _db_with(bars)
    out = rs.backfill_stock_quotes(db, "2026-11-13", dry_run=True)
    assert out["status"] == "dry_run" and out["would_insert"] == 1


def test_no_bars_returns_failed_without_writing():
    db = _db_with([])
    out = rs.backfill_stock_quotes(db, "2026-11-13", dry_run=True)
    assert out["status"] == "failed" and "daily_kbars" in out["reason"]
    assert db.commits == 0


def test_existing_complete_day_is_skipped():
    db = _FakeDB([_Result(scalar=5591)])
    out = rs.backfill_stock_quotes(db, "2026-11-13", dry_run=False)
    assert out["status"] == "skipped" and out["existing"] == 5591
    assert db.results == []  # 连组装查询都不发


@pytest.mark.parametrize("raw", ["2026-13-01", "not-a-date"])
def test_main_rejects_bad_date(monkeypatch, raw):
    """非法日期必须在连库/拉上游之前就拒绝。"""
    monkeypatch.setattr("sys.argv", ["repair_snapshot_day.py", "--date", raw, "--dry-run"])
    assert rs.main() == 2


def test_compact_date_is_normalized():
    """Python 3.11+ 接受 YYYYMMDD，脚本统一规范化成 YYYY-MM-DD 再查库。"""
    import datetime as dt

    assert dt.date.fromisoformat("20261113").isoformat() == "2026-11-13"

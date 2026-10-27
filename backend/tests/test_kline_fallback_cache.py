"""盘中逐股K线回退：当日跨进程缓存（含负结果）、日上限、窗口外不缓存。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import kline_fallback_cache as kfc
from app.services import market_data as md

TZ = ZoneInfo("Asia/Shanghai")
MON_1030 = datetime(2026, 10, 26, 10, 30, tzinfo=TZ)
MON_1800 = datetime(2026, 10, 26, 18, 0, tzinfo=TZ)
SAT_1030 = datetime(2026, 10, 24, 10, 30, tzinfo=TZ)


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(kfc, "_DB_PATH", tmp_path / "kfc.sqlite")
    monkeypatch.setenv("ASHARE_KLINE_FALLBACK_CACHE_FORCE", "1")
    monkeypatch.delenv("ASHARE_KLINE_FALLBACK_MAX_STOCKS_PER_DAY", raising=False)


def test_window():
    assert kfc.in_window(MON_1030)
    assert not kfc.in_window(MON_1800)
    assert not kfc.in_window(SAT_1030)


def test_negative_and_positive_results_cached_within_day():
    kfc.store("688001", [], "", ["tushare: 日K不足30条"], now=MON_1030)
    kfc.store("600000", [{"trade_date": "20261023", "close": 1.0}] * 31, "tushare", [], now=MON_1030)
    assert kfc.lookup("688001", now=MON_1030) == ([], "", ["tushare: 日K不足30条"])
    bars, src, _ = kfc.lookup("600000", now=MON_1030)
    assert len(bars) == 31 and src == "tushare"
    assert kfc.lookup("000001", now=MON_1030) is None


def test_outside_window_never_cached_or_read():
    kfc.store("688001", [], "", ["x"], now=MON_1800)
    assert kfc.lookup("688001", now=MON_1800) is None
    kfc.store("688002", [], "", ["x"], now=MON_1030)
    assert kfc.lookup("688002", now=MON_1800) is None  # 收盘后不用上午的缓存


def test_daily_stock_cap(monkeypatch):
    monkeypatch.setenv("ASHARE_KLINE_FALLBACK_MAX_STOCKS_PER_DAY", "2")
    assert kfc.budget_allows_fetch(now=MON_1030)
    kfc.store("000001", [], "", [], now=MON_1030)
    kfc.store("000002", [], "", [], now=MON_1030)
    assert not kfc.budget_allows_fetch(now=MON_1030)
    assert kfc.budget_allows_fetch(now=MON_1800)  # 窗口外不限
    monkeypatch.setenv("ASHARE_KLINE_FALLBACK_MAX_STOCKS_PER_DAY", "0")
    assert kfc.budget_allows_fetch(now=MON_1030)


def test_fetch_first_kline_hits_upstream_once_per_day(monkeypatch):
    calls: list[str] = []

    def fake_uncached(quote, sources, settings, disabled_sources=None):
        calls.append(str(quote["code"]))
        return [], "", ["tushare: 日K不足30条"]

    monkeypatch.setattr(md, "_fetch_first_kline_uncached", fake_uncached)
    monkeypatch.setattr(kfc, "_now", lambda: MON_1030)
    for _ in range(5):  # 模拟 5 次轮询（每次新进程，靠 sqlite 共享）
        assert md._fetch_first_kline({"code": "688999"}, ["tushare"], None) == ([], "", ["tushare: 日K不足30条"])
    assert calls == ["688999"]


def test_fetch_first_kline_unchanged_after_close(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(md, "_fetch_first_kline_uncached", lambda q, s, st, d=None: calls.append(q["code"]) or ([], "", []))
    monkeypatch.setattr(kfc, "_now", lambda: MON_1800)
    md._fetch_first_kline({"code": "688999"}, ["tushare"], None)
    md._fetch_first_kline({"code": "688999"}, ["tushare"], None)
    assert calls == ["688999", "688999"]


def test_fetch_first_kline_budget_exhausted_skips_upstream(monkeypatch):
    monkeypatch.setenv("ASHARE_KLINE_FALLBACK_MAX_STOCKS_PER_DAY", "1")
    monkeypatch.setattr(kfc, "_now", lambda: MON_1030)
    calls: list[str] = []
    monkeypatch.setattr(md, "_fetch_first_kline_uncached", lambda q, s, st, d=None: calls.append(q["code"]) or ([], "", []))
    md._fetch_first_kline({"code": "000001"}, ["tushare"], None)
    bars, src, errs = md._fetch_first_kline({"code": "000002"}, ["tushare"], None)
    assert calls == ["000001"] and bars == [] and "额度" in errs[0]

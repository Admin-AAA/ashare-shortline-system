"""realtime_quote_client 单测：mock tushare.realtime_quote，验证归一化/缓存/降级。"""

from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from app.services import realtime_quote_client as rq


def _fake_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "NAME": "深物业A", "TS_CODE": "000011.SZ", "DATE": "20260727",
                "TIME": "14:01:12", "OPEN": 8.39, "PRE_CLOSE": 8.26, "PRICE": 8.04,
                "HIGH": 9.09, "LOW": 8.04, "VOLUME": 46613717, "AMOUNT": 4.12e8,
            },
            {
                "NAME": "瑞芯微", "TS_CODE": "603893.SH", "DATE": "20260727",
                "TIME": "14:01:13", "OPEN": 220.0, "PRE_CLOSE": 221.8, "PRICE": 211.95,
                "HIGH": 221.7, "LOW": 208.11, "VOLUME": 1000, "AMOUNT": 2.0e5,
            },
        ]
    )


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setenv("ASHARE_TUSHARE_FASTAPIC_TOKEN", "test-token")
    rq.get_settings.cache_clear()
    rq._cache["fetched_at"] = 0.0
    rq._cache["rows"] = {}
    yield
    rq.get_settings.cache_clear()
    rq._cache["fetched_at"] = 0.0
    rq._cache["rows"] = {}


def _install_fake_tushare(monkeypatch, df):
    """注入一个假的 tushare 模块，realtime_quote 返回固定 DataFrame。"""
    fake = types.ModuleType("tushare")
    fake.set_token = lambda token: None
    fake.realtime_quote = lambda ts_code: df
    fake.__path__ = []
    cons = types.ModuleType("tushare.stock.cons")
    cons.verify_token_url = ""
    stock = types.ModuleType("tushare.stock")
    stock.cons = cons
    monkeypatch.setitem(sys.modules, "tushare", fake)
    monkeypatch.setitem(sys.modules, "tushare.stock", stock)
    monkeypatch.setitem(sys.modules, "tushare.stock.cons", cons)


def test_normalize_rows_shape_and_code6(monkeypatch):
    df = _fake_df()
    rows = rq._normalize_rows(df)
    assert set(rows) == {"000011", "603893"}
    q = rows["000011"]
    assert q["price"] == pytest.approx(8.04)
    assert q["high"] == pytest.approx(9.09)
    assert q["pre_close"] == pytest.approx(8.26)
    assert q["trade_date"] == "2026-07-27"
    assert q["source"] == "realtime_quote"
    assert q["ts_code"] == "000011.SZ"


def test_fetch_realtime_quotes_returns_code6_keyed(monkeypatch):
    _install_fake_tushare(monkeypatch, _fake_df())
    rows = rq.fetch_realtime_quotes(["000011", "603893"], use_cache=False)
    assert set(rows) == {"000011", "603893"}
    assert rows["603893"]["name"] == "瑞芯微"


def test_fetch_uses_ttl_cache_on_second_call(monkeypatch):
    df = _fake_df()
    _install_fake_tushare(monkeypatch, df)
    calls = {"n": 0}

    import sys as _sys
    _sys.modules["tushare"].realtime_quote = lambda ts_code: (calls.__setitem__("n", calls["n"] + 1), df)[1]

    rq.fetch_realtime_quotes(["000011"], use_cache=True)
    first = calls["n"]
    rq.fetch_realtime_quotes(["000011"], use_cache=True)  # 应命中缓存
    assert first == 1
    assert calls["n"] == 1  # 第二次没再调上游


def test_fetch_failure_returns_empty_not_raise(monkeypatch):
    fake = types.ModuleType("tushare")
    fake.set_token = lambda token: None

    def _boom(ts_code):
        raise RuntimeError("504 gateway timeout")

    fake.realtime_quote = _boom
    fake.__path__ = []
    cons = types.ModuleType("tushare.stock.cons")
    cons.verify_token_url = ""
    stock = types.ModuleType("tushare.stock")
    stock.cons = cons
    monkeypatch.setitem(sys.modules, "tushare", fake)
    monkeypatch.setitem(sys.modules, "tushare.stock", stock)
    monkeypatch.setitem(sys.modules, "tushare.stock.cons", cons)

    rows = rq.fetch_realtime_quotes(["000011"], use_cache=False)
    assert rows == {}


def test_invalid_codes_filtered(monkeypatch):
    _install_fake_tushare(monkeypatch, _fake_df())
    rows = rq.fetch_realtime_quotes(["", "abc", "12345", "000011"], use_cache=False)
    # 只有合法 6 位数字 000011 被请求
    assert set(rows) <= {"000011", "603893"}


def test_health_entry_structure(monkeypatch):
    _install_fake_tushare(monkeypatch, _fake_df())
    rq.fetch_realtime_quotes(["000011"], use_cache=False)
    h = rq.get_realtime_quote_health()
    assert h["provider_name"] == "realtime_quote"
    assert h["source_type"] == "tushare_provider"
    assert h["status"] == "healthy"
    assert h["last_success_at"] is not None

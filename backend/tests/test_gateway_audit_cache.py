"""Audit item 3: TTL cache, single-flight, stale fallback (fault injection, no upstream)."""
import threading
import time

import pytest

import app.services.datasource.cache as c
import app.services.datasource.gateway as g
from app.services.datasource.gateway import DatasetGateway

ROW = {k: 1 for k in g.SCHEMAS["quote.realtime"]}


@pytest.fixture(autouse=True)
def _disk(tmp_path, monkeypatch):
    import diskcache

    dc = diskcache.Cache(str(tmp_path / "stale"))
    monkeypatch.setattr(c, "_disk_cache", lambda: dc)
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: False)
    c.clear_fresh()
    yield
    dc.close()


class P:
    config_keys = ()

    def __init__(self, name, fn):
        self.name, self.fn, self.calls = name, fn, 0
        self.up = True

    def supports(self, d, q):
        return True

    def fetch(self, d, q):
        self.calls += 1
        if not self.up:
            raise ConnectionError("down")
        return self.fn(q)


def _gw(*ps):
    return DatasetGateway(list(ps), enabled=lambda p: True, failure_threshold=99)


def _quotes(q):
    return {k: {**ROW, "code": k, "price": 10.0} for k in q["keys"]}


def test_ttl_cache_hits_then_expires(monkeypatch):
    monkeypatch.setattr(c, "ttl_for", lambda d: 0.3)
    a = P("a", _quotes)
    gw = _gw(a)
    gw.fetch_fill("quote.realtime", {}, ["600000"])
    gw.fetch_fill("quote.realtime", {}, ["600000"])
    assert a.calls == 1
    c.clear_fresh()  # TTLCache built with ttl=0.3; force expiry deterministically
    gw.fetch_fill("quote.realtime", {}, ["600000"])
    assert a.calls == 2


def test_partial_cache_only_fetches_missing():
    seen = []
    a = P("a", lambda q: seen.append(list(q["keys"])) or _quotes(q))
    gw = _gw(a)
    gw.fetch_fill("quote.realtime", {}, ["600000"])
    gw.fetch_fill("quote.realtime", {}, ["600000", "000001"])
    assert seen == [["600000"], ["000001"]]


def test_no_cache_for_daily_datasets():
    a = P("a", lambda q: [{"x": 1}])
    gw = _gw(a)
    gw.fetch_first("tushare.daily", {"p": 1})
    gw.fetch_first("tushare.daily", {"p": 1})
    assert a.calls == 2 and c.ttl_for("tushare.daily") == 0


def test_single_flight_concurrent_identical(monkeypatch):
    def slow(q):
        time.sleep(0.4)
        return _quotes(q)

    a = P("a", slow)
    gw = _gw(a)
    out = []
    ts = [threading.Thread(target=lambda: out.append(gw.fetch_fill("quote.realtime", {}, ["600000"]))) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert a.calls == 1 and len(out) == 8 and all("600000" in r.rows for r in out)


def test_stale_fallback_flags_and_data_time():
    a = P("a", _quotes)
    gw = _gw(a)
    gw.fetch_fill("quote.realtime", {}, ["600000"])
    c.clear_fresh()
    a.up = False
    r = gw.fetch_fill("quote.realtime", {}, ["600000"])
    row = r.rows["600000"]
    assert r.stale is True and r.stale_keys == ("600000",) and r.data_time
    assert row["stale"] is True and row["data_time"] == r.data_time and row["price"] == 10.0


def test_formal_chain_refuses_stale():
    a = P("a", _quotes)
    gw = _gw(a)
    gw.fetch_fill("quote.realtime", {}, ["600000"])
    c.clear_fresh()
    a.up = False
    with c.formal_chain():
        r = gw.fetch_fill("quote.realtime", {}, ["600000"])
    assert r.rows == {} and r.stale is False
    with c.formal_chain():
        r2 = gw.fetch_fill("quote.realtime", {}, ["600000"], allow_stale=True)  # formal overrides opt-in
    assert r2.rows == {}


def test_non_stale_ok_dataset_default_refuses(monkeypatch):
    monkeypatch.setattr(c, "ttl_for", lambda d: 30.0)
    a = P("a", lambda q: [{"x": 1}])
    gw = _gw(a)
    gw.fetch_first("test.ds", {"code": "1"})
    c.clear_fresh()
    a.up = False
    assert gw.fetch_first("test.ds", {"code": "1"}).provider is None
    r = gw.fetch_first("test.ds", {"code": "1"}, allow_stale=True)
    assert r.stale is True and r.rows == [{"x": 1}]


def test_ttl_override_setting(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "datasource_cache_ttl", "quote.realtime=7, bar.minute=0")
    assert c.ttl_for("quote.realtime") == 7.0 and c.ttl_for("bar.minute") == 0.0

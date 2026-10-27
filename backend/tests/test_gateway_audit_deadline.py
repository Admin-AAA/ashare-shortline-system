"""Audit item 2: whole-request deadline, tenacity retry budget, Sina timeout (fault injection)."""
import time

import pytest

import app.services.datasource.gateway as g
from app.services.datasource.gateway import DatasetGateway, DataSourceError, ErrorKind

ROW = {k: 1 for k in g.SCHEMAS["quote.realtime"]}


class P:
    config_keys = ()

    def __init__(self, name, behave):
        self.name, self.behave, self.calls = name, behave, 0

    def supports(self, d, q):
        return True

    def fetch(self, d, q):
        self.calls += 1
        return self.behave(self.calls, q)


def _gw(*ps):
    return DatasetGateway(list(ps), enabled=lambda p: True, failure_threshold=99)


def test_default_deadlines():
    assert g.default_deadline("quote.realtime") == 3.0
    assert g.default_deadline("tushare.daily") == 120.0


def test_realtime_whole_request_under_3s(monkeypatch):
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: False)
    hang = lambda n, q: time.sleep(10)
    a, b, c = P("a", hang), P("b", hang), P("c", lambda n, q: {"600000": ROW})
    t0 = time.perf_counter()
    r = _gw(a, b, c).fetch_fill("quote.realtime", {}, ["600000"])
    dt = time.perf_counter() - t0
    assert dt < 3.3
    assert [x["outcome"] for x in r.attempts][:2] == ["timeout", "deadline"]


def test_explicit_deadline_override():
    t0 = time.perf_counter()
    r = _gw(P("a", lambda n, q: time.sleep(5))).fetch_first("ds", {}, deadline_s=0.5)
    assert time.perf_counter() - t0 < 1.0 and r.attempts[0]["outcome"] == "timeout"


def test_transient_error_retried_with_tenacity():
    def flaky(n, q):
        if n == 1:
            raise ConnectionError("reset")
        return [{"x": 1}]

    a = P("a", flaky)
    r = _gw(a).fetch_first("ds", {})
    assert r.provider == "a" and a.calls == 2


def test_retry_budget_shared_across_providers(monkeypatch):
    monkeypatch.setattr(g, "_setting", lambda attr, d: 1 if attr == "datasource_retry_budget" else d)

    def down(n, q):
        raise ConnectionError("down")

    a, b = P("a", down), P("b", down)
    _gw(a, b).fetch_first("ds", {})
    assert a.calls + b.calls == 3  # 2 first tries + 1 retry from the shared budget


def test_non_retryable_not_retried():
    def rl(n, q):
        raise DataSourceError(ErrorKind.RATE_LIMIT)

    a = P("a", rl)
    _gw(a).fetch_first("ds", {})
    assert a.calls == 1


def test_sina_chunk_timeout(monkeypatch):
    import app.services.datasource.sina_realtime as s

    monkeypatch.setattr(s, "SINA_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(s, "_raw_frame_unbounded", lambda codes: time.sleep(5))
    t0 = time.perf_counter()
    with pytest.raises(DataSourceError) as ei:
        s._raw_frame(["600000.SH"])
    assert ei.value.kind is ErrorKind.TIMEOUT and time.perf_counter() - t0 < 1.0

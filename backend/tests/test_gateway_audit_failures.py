"""Audit item 1: per-kind failure classification + thresholds (fault injection, no upstream)."""
import time

import pytest

import app.core.shared_breaker as sb
import app.services.datasource.gateway as g
from app.services.datasource.gateway import DatasetGateway, DataSourceError, ErrorKind


def _store(monkeypatch, tmp_path):
    import app.core.metrics as m

    monkeypatch.setattr(m, "_process_role", lambda: "backend")
    monkeypatch.setattr(m, "_store_path", lambda: tmp_path / "rt.sqlite")
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "live")
    sb._cache.clear()


class Faulty:
    config_keys = ()

    def __init__(self, name, exc=None, rows=None):
        self.name, self.exc, self.rows, self.calls = name, exc, rows, 0

    def supports(self, d, q):
        return True

    def fetch(self, d, q):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.rows


def _gw(*ps, threshold=10):
    return DatasetGateway(list(ps), enabled=lambda p: True, failure_threshold=threshold)


def _open_left(name, ds):
    sb._cache.clear()
    return sb.shared_open_until(f"live:gw:{name}:{ds}") - time.time()


def test_timeout_opens_after_two(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    p = Faulty("t", exc=TimeoutError("slow"))
    gw = _gw(p)
    gw.fetch_first("ds", {})
    assert _open_left("t", "ds") <= 0
    gw.fetch_first("ds", {})
    assert _open_left("t", "ds") > 290
    gw.fetch_first("ds", {})
    assert p.calls == 2  # third call skipped by breaker


def test_bad_data_opens_after_three(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    p = Faulty("b", exc=DataSourceError(ErrorKind.CONTRACT, "missing"))
    gw = _gw(p)
    for _ in range(2):
        gw.fetch_first("ds", {})
    assert _open_left("b", "ds") <= 0
    gw.fetch_first("ds", {})
    assert _open_left("b", "ds") > 290


def test_rate_limit_uses_upstream_retry_after(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    exc = DataSourceError(ErrorKind.RATE_LIMIT, "429")
    exc.retry_after_seconds = 20.0
    gw = _gw(Faulty("r", exc=exc))
    gw.fetch_first("ds", {})
    assert 15 < _open_left("r", "ds") <= 20.5


def test_rate_limit_without_hint_defaults_5min(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    _gw(Faulty("r2", exc=DataSourceError(ErrorKind.RATE_LIMIT))).fetch_first("ds", {})
    assert _open_left("r2", "ds") > 290


def test_thresholds_configurable(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "breaker_threshold_timeout", 1)
    _gw(Faulty("c", exc=TimeoutError())).fetch_first("ds", {})
    assert _open_left("c", "ds") > 290


def test_empty_in_session_is_bad_data_and_falls_back(monkeypatch):
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: True)
    a, b = Faulty("a", rows={}), Faulty("b", rows={"600000": {k: 1 for k in g.SCHEMAS["quote.realtime"]}})
    r = _gw(a, b).fetch_fill("quote.realtime", {}, ["600000"])
    assert [x["outcome"] for x in r.attempts] == ["bad_data", "success"] and r.provider == "b"


def test_empty_off_session_is_empty_not_success(monkeypatch):
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: False)
    a = Faulty("a", rows={})
    gw = _gw(a, threshold=1)
    br = gw._breaker("a", "quote.realtime")
    br.record_failure(kind="timeout")  # one timeout pending
    r = gw.fetch_fill("quote.realtime", {}, ["600000"])
    assert r.attempts[0]["outcome"] == "empty"
    assert br._kind_counts == {"timeout": 1}  # empty did NOT reset counters as a success would


def test_invalid_request_never_trips(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    gw = _gw(Faulty("i", exc=DataSourceError(ErrorKind.INVALID_REQUEST)), threshold=1)
    for _ in range(3):
        gw.fetch_first("ds", {})
    assert _open_left("i", "ds") <= 0


@pytest.mark.parametrize("cat,kind", [
    ("http_rate_limit", "rate_limit"), ("business_rate_limit", "rate_limit"),
    ("timeout", "timeout"), ("malformed_response", "bad_data"), ("network", None),
])
def test_transport_kind_mapping(cat, kind):
    from app.services.tushare_transport import FailureCategory, _breaker_kind

    assert _breaker_kind(FailureCategory(cat)) == kind


def test_transport_timeout_twice_opens_shared(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    import app.services.tushare_transport as tt

    p = tt.TushareHttpProvider(tt.ProviderConfig(
        name="fake", base_url="http://x", credential="k", auth_mode=tt.AuthMode.JSON_TOKEN,
        rate_limit_per_minute=0, max_attempts=1, failure_threshold=5))

    def boom(*a, **kw):
        raise tt.ProviderFailure(provider="fake", api_name="daily", category=tt.FailureCategory.TIMEOUT,
                                 retryable=True, fallback_allowed=True)

    monkeypatch.setattr(p, "_request_once", boom)
    for _ in range(2):
        with pytest.raises(tt.ProviderFailure):
            p.request("daily")
    sb._cache.clear()
    assert sb.shared_open_until("live:tushare:fake:daily") - time.time() > 290

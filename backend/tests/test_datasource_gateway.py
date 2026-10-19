import pytest

from app.core import metrics
from app.services.datasource import gateway as gw


class _P:
    def __init__(self, name, rows=None, exc=None, keys=()):
        self.name, self._rows, self._exc, self.config_keys = name, rows, exc, tuple(keys)
        self.calls = []

    def supports(self, dataset, query):
        return True

    def fetch(self, dataset, query):
        self.calls.append(dict(query))
        if self._exc:
            raise self._exc
        if isinstance(self._rows, dict):
            return {k: v for k, v in self._rows.items() if k in query.get("keys", self._rows)}
        return self._rows


def _bar(src):
    return {"code": "000001", "trade_date": "2026-10-16", "trade_time": "2026-10-16 09:31:00", "open": 1,
            "high": 1, "low": 1, "close": 1, "volume": 1, "amount": 1, "source": src}


def _all_on(p):
    return True


def test_first_mode_falls_back_on_error_and_empty():
    a, b, c = _P("a", exc=TimeoutError()), _P("b", rows=[]), _P("c", rows=[_bar("c")])
    r = gw.DatasetGateway([a, b, c], enabled=_all_on).fetch_first("bar.minute", {})
    assert r.provider == "c" and [x["outcome"] for x in r.attempts] == ["timeout", "empty", "success"]


def test_fill_mode_asks_only_missing_keys():
    a = _P("a", rows={"x": {"k": 1}})
    b = _P("b", rows={"x": {"k": 2}, "y": {"k": 3}})
    r = gw.DatasetGateway([a, b], enabled=_all_on).fetch_fill("other", {}, ["x", "y"])
    assert r.rows == {"x": {"k": 1}, "y": {"k": 3}} and b.calls[0]["keys"] == ["y"]


def test_disabled_provider_is_never_used_even_if_listed():
    a = _P("xiaoshi", rows=[_bar("x")], keys=[("xiaoshi", "quote")])
    b = _P("b", rows=[_bar("b")])
    disabled = {("xiaoshi", "quote")}
    g = gw.DatasetGateway([a, b], enabled=lambda p: gw.provider_enabled(p, disabled))
    assert g.fetch_first("bar.minute", {}).provider == "b" and not a.calls


def test_contract_violation_is_classified_and_skipped():
    bad = _P("bad", rows=[{"code": "1"}])
    good = _P("good", rows=[_bar("g")])
    r = gw.DatasetGateway([bad, good], enabled=_all_on).fetch_first("bar.minute", {})
    assert r.provider == "good" and r.attempts[0]["outcome"] == "contract"


def test_breaker_opens_after_threshold():
    a = _P("a", exc=RuntimeError("upstream"))  # non-retryable: one call per fetch
    g = gw.DatasetGateway([a], enabled=_all_on, failure_threshold=2)
    for _ in range(3):
        g.fetch_first("bar.minute", {})
    assert len(a.calls) == 2


def test_tushare_compat_delegates_to_router():
    class R:
        def request(self, api, params, fields):
            return {"api": api, "params": params, "fields": fields}

    p = gw.TushareApiProvider(lambda: R())
    out = gw.DatasetGateway([p], enabled=_all_on).fetch_first("tushare.daily", {"params": {"ts_code": "1"}, "fields": "close"})
    assert out.rows == {"api": "daily", "params": {"ts_code": "1"}, "fields": "close"}


def test_shadow_never_raises_and_counts():
    metrics.reset()
    gw.shadow_compare("d", [1], lambda: [1])
    gw.shadow_compare("d", [1], lambda: [2])
    gw.shadow_compare("d", [1], lambda: (_ for _ in ()).throw(RuntimeError()))
    outs = sorted(c["labels"]["outcome"] for c in metrics.snapshot()["counters"] if c["name"] == "dataset_shadow")
    assert outs == ["error", "match", "mismatch"]


@pytest.mark.parametrize("cat,kind", [("http_rate_limit", "rate_limit"), ("permission", "no_permission")])
def test_error_taxonomy_maps_tushare_categories(cat, kind):
    from app.services.tushare_transport import FailureCategory, ProviderFailure

    exc = ProviderFailure(provider="p", api_name="a", category=FailureCategory(cat), retryable=False, fallback_allowed=True)
    assert gw.classify_exception(exc).value == kind

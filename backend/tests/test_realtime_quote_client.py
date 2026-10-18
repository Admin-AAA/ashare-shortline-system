"""realtime_quote_client: thin cached facade over dataset quote.realtime."""
from app.services import realtime_quote_client as rq
from app.services.datasource import quote_realtime


def test_normalizes_codes_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(quote_realtime, "fetch", lambda codes: calls.append(list(codes)) or {c: {"price": 1.0} for c in codes})
    rq._cache.update(fetched_at=0.0, rows={})
    assert set(rq.fetch_realtime_quotes(["000011.SZ", "000011", "bad", "603893"])) == {"000011", "603893"}
    assert calls == [["000011", "603893"]]
    rq.fetch_realtime_quotes(["000011"])  # served from cache
    assert len(calls) == 1
    rq.fetch_realtime_quotes(["000011"], use_cache=False)
    assert len(calls) == 2


def test_empty_input_does_not_call_gateway(monkeypatch):
    monkeypatch.setattr(quote_realtime, "fetch", lambda codes: (_ for _ in ()).throw(AssertionError()))
    assert rq.fetch_realtime_quotes(["x"]) == {}

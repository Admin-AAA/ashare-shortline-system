"""Behavior-equivalence guards for retry loops migrated to tenacity."""
import time

import httpx
import pytest

from app.services import market_data, xiaoshi_client


def test_market_data_retry_only_on_rate_limit(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(market_data, "_throttle_tushare", lambda _s: None)
    calls = []

    class Pro:
        def daily(self, **_):
            calls.append(1)
            raise RuntimeError("抱歉，您每分钟最多访问该接口500次，请求速度过快")

    with pytest.raises(RuntimeError):
        market_data._call_tushare_with_retry(Pro(), None, "daily")
    assert len(calls) == 3 and sleeps == [1.5, 3.0]

    calls.clear(); sleeps.clear()

    class Pro2:
        def daily(self, **_):
            calls.append(1)
            raise ValueError("other")

    with pytest.raises(ValueError):
        market_data._call_tushare_with_retry(Pro2(), None, "daily")
    assert len(calls) == 1 and sleeps == []


def test_xiaoshi_client_retry_semantics(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    def run(handler):
        calls = []

        def h(_req):
            calls.append(1)
            return handler(len(calls))

        c = xiaoshi_client.XiaoshiClient(
            base_url="http://x", api_key="k", max_attempts=3,
            http_client=httpx.Client(transport=httpx.MockTransport(h)),
        )
        sleeps.clear()
        try:
            out = c._request_response("GET", "/a").status_code
        except xiaoshi_client.XiaoshiError as e:
            out = (type(e), e.status_code)
        return out, len(calls), list(sleeps)

    assert run(lambda i: httpx.Response(429, headers={"Retry-After": "9"})) == (
        (xiaoshi_client.XiaoshiError, 429), 3, [5.0, 5.0])
    assert run(lambda i: httpx.Response(503) if i < 3 else httpx.Response(200, json={})) == (200, 3, [0.2, 0.4])
    assert run(lambda i: httpx.Response(404)) == ((xiaoshi_client.XiaoshiError, 404), 1, [])


def test_tushare_transport_backoff_and_breaker(monkeypatch):
    from app.services import tushare_transport as tt

    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(tt.random, "uniform", lambda a, b: 0.0)
    cfg = tt.ProviderConfig(
        name="p", base_url="http://x", credential="c", auth_mode=list(tt.AuthMode)[0],
        rate_limit_per_minute=0, max_attempts=4,
    )
    prov = tt.TushareHttpProvider(cfg)
    monkeypatch.setattr(prov, "_wait_for_capacity", lambda _a: None)
    calls = []

    def fail(api_name, **_):
        calls.append(1)
        raise tt.ProviderFailure(provider="p", api_name=api_name,
                                 category=tt.FailureCategory.HTTP_SERVER,
                                 retryable=True, fallback_allowed=True)

    monkeypatch.setattr(prov, "_request_once", fail)
    events = []

    class B:
        state = "closed"
        def record_success(self): events.append("ok")
        def record_failure(self, **kw): events.append(("fail", kw["force_open"]))

    with pytest.raises(tt.ProviderFailure):
        prov._request_with_retries("daily", params={}, fields="", breaker=B())
    assert len(calls) == 4 and sleeps == [0.5, 1.0, 2.0]
    assert events == [("fail", False)]


def test_eastmoney_escalating_block_backoff(monkeypatch):
    from app.services import eastmoney_news as em

    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(em, "_wait_rate_limit", lambda: None)

    def run(statuses):
        it = iter(statuses)
        calls = []

        def h(_req):
            calls.append(1)
            st = next(it)
            return httpx.Response(st, json={"data": {"list": []}})

        sleeps.clear()
        try:
            out = em.fetch_announcements_for_code(
                "600000", max_pages=1, client=httpx.Client(transport=httpx.MockTransport(h)))
        except Exception as e:  # noqa: BLE001
            out = (type(e).__name__, str(e))
        return out, len(calls), list(sleeps)

    assert run([567, 567, 567]) == (("EastmoneyBlockedError", "eastmoney HTTP 567 x3"), 3, [5.0, 15.0, 30.0])
    out, n, sl = run([403, 403, 500])
    assert out[0] == "RuntimeError" and n == 3 and sl == [5.0, 15.0, 3.0]
    assert run([500, 200]) == ([], 2, [1.0])


def test_unified_sliding_window_limiter(monkeypatch):
    from app.services import tushare_transport as tt, xiaoshi_client as xc

    clock = [100.0]
    sleeps = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def fake_sleep(s):
        sleeps.append(round(s, 6)); clock[0] += s

    monkeypatch.setattr(time, "sleep", fake_sleep)
    q = xc._QPSLimiter(2)
    for _ in range(3):
        q.acquire()
    assert sleeps == [1.0]
    sleeps.clear()
    t = tt.SlidingWindowLimiter(1)
    t.acquire(); t.acquire()
    assert sleeps == [60.01]

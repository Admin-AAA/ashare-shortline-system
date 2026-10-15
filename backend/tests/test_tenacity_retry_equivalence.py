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

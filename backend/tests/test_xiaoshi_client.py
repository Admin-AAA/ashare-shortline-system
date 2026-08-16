from __future__ import annotations

import hashlib

import httpx
import pytest

from app.services.market_source.port import DailyBarRequest, QuoteRef, QuoteRequest
from app.services.xiaoshi_client import XiaoshiClient
from app.services.xiaoshi_client import XiaoshiError
from app.services.xiaoshi_market_source import XiaoshiMarketSourceAdapter


def test_xiaoshi_client_sends_bearer_key_and_retries_rate_limit(monkeypatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr("app.services.xiaoshi_client.time.sleep", lambda _seconds: None)
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    client = XiaoshiClient(
        base_url="https://api.shizixi.com",
        api_key="test-key",
        max_qps=30,
        max_attempts=2,
        http_client=http_client,
    )

    try:
        assert client.request("GET", "/api/v3/manifest") == {"ok": True}
        assert len(calls) == 2
        assert all(call.headers["Authorization"] == "Bearer test-key" for call in calls)
        assert client._limiter._limit == 30
    finally:
        http_client.close()


def test_xiaoshi_market_source_normalizes_snapshot_and_qfq_bars() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/data/stocks"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"code": "000001", "name": "平安银行", "exchange": "SZ", "industry": "银行"}
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/data/market-snapshot"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "code": "000001",
                            "name": "平安银行",
                            "price": 11.19,
                            "last_close": 11.27,
                            "change_pct": -0.71,
                            "volume": 100,
                            "amount": 200_000_000,
                            "turnover_pct": 0.46,
                            "quote_time": "2026-08-07T16:14:36",
                        }
                    ],
                    "updated_at": "2026-08-07T16:14:36",
                },
                request=request,
            )
        if request.url.path.endswith("/data/kline/000001"):
            return httpx.Response(
                200,
                json={
                    "bars": [
                        {"date": "2026-08-06", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1, "amount": 2},
                        {"date": "2026-08-07", "open": 11, "high": 12, "low": 10, "close": 11, "volume": 2, "amount": 3},
                    ]
                },
                request=request,
            )
        return httpx.Response(404, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = XiaoshiMarketSourceAdapter(
        client=XiaoshiClient(
            base_url="https://api.shizixi.com",
            api_key="test-key",
            http_client=http_client,
        )
    )
    try:
        quotes = adapter.fetch_quotes(QuoteRequest())
        assert quotes.audit.status.value == "success"
        assert quotes.as_legacy_rows()[0]["industry"] == "银行"
        assert quotes.as_legacy_rows()[0]["amount_yi"] == 2.0

        bars = adapter.fetch_daily_bars(
            DailyBarRequest((QuoteRef("000001", "SZ"),), start_date="2026-08-01", end_date="2026-08-07")
        )
        rows = bars.as_legacy_rows_by_code()["000001"]
        assert bars.audit.status.value == "success"
        assert rows[-1]["trade_date"] == "2026-08-07"
        assert rows[-1]["pct_chg"] == pytest.approx(10.0)
    finally:
        http_client.close()


def test_xiaoshi_client_uses_current_factor_and_history_contracts() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True}, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = XiaoshiClient(
        base_url="https://api.shizixi.com",
        api_key="test-key",
        http_client=http_client,
    )
    try:
        client.get_factor_library()
        client.get_history_download_session(dataset="min1", code="000001", year="2026")
    finally:
        http_client.close()

    assert [request.url.path for request in calls] == [
        "/api/v3/factors/library",
        "/api/v3/history/download-session",
    ]


def test_xiaoshi_client_verifies_pinned_quant_skill() -> None:
    content = "---\nname: xiaoshi-quant-expert\n---\n# Xiaoshi Quant Expert\n"
    expected_sha256 = hashlib.sha256(content.encode()).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=content, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = XiaoshiClient(
        base_url="https://api.shizixi.com",
        api_key="test-key",
        http_client=http_client,
    )
    try:
        artifact = client.get_quant_skill(
            version="2026-08-14.1",
            expected_sha256=expected_sha256,
        )
        assert artifact.version == "2026-08-14.1"
        assert artifact.sha256 == expected_sha256
        assert artifact.content == content

        with pytest.raises(XiaoshiError, match="校验失败"):
            client.get_quant_skill(
                version="2026-08-14.1",
                expected_sha256="0" * 64,
            )
    finally:
        http_client.close()


def test_xiaoshi_market_source_rejects_partial_full_market_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/data/stocks"):
            return httpx.Response(
                200,
                json={"items": [{"code": "000001", "name": "平安银行"}]},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "items": [{"code": "000001", "name": "平安银行", "price": 10}],
                "count": 1,
                "expected_total": 5533,
                "coverage": 0.0002,
                "partial": True,
            },
            request=request,
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = XiaoshiMarketSourceAdapter(
        client=XiaoshiClient(
            base_url="https://api.shizixi.com",
            api_key="test-key",
            http_client=http_client,
        )
    )
    try:
        batch = adapter.fetch_quotes(QuoteRequest())
        assert batch.audit.status.value == "failure"
        assert batch.quotes == ()
        assert batch.errors[0].message == "xiaoshi quote retrieval failed"
    finally:
        http_client.close()

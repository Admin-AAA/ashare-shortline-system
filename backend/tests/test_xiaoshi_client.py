from __future__ import annotations

import httpx
import pytest

from app.services.xiaoshi_client import XiaoshiClient
from app.services.xiaoshi_client import XiaoshiConfigurationError


def test_xiaoshi_client_sends_bearer_key_and_retries_rate_limit(monkeypatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr("app.services.xiaoshi_client.time.sleep", lambda _seconds: None)
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
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


def test_xiaoshi_client_requires_key_before_any_request() -> None:
    client = XiaoshiClient(base_url="https://api.shizixi.com", api_key="")
    try:
        with pytest.raises(XiaoshiConfigurationError):
            client.get_manifest()
    finally:
        client.close()


def test_xiaoshi_client_uses_published_query_and_history_paths() -> None:
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
        client.get_quote("000001")
        client.get_quant_catalog()
        client.get_quant_data("stock_fundflow", code="000001", limit=5)
        client.get_history_catalog(dataset="cn-daily", limit=1)
        client.get_history_manifest()
        client.get_history_download_session(dataset="cn-daily", market="CN", year="2025")
    finally:
        http_client.close()

    assert [request.url.path for request in calls] == [
        "/api/v3/market/quote/000001",
        "/api/v3/quant-data/catalog",
        "/api/v3/quant-data/stock_fundflow",
        "/api/v3/history/catalog",
        "/api/v3/history/manifest",
        "/api/v3/history/download-session",
    ]
    assert dict(calls[3].url.params)["dataset"] == "cn-daily"
    assert dict(calls[5].url.params) == {
        "dataset": "cn-daily",
        "market": "CN",
        "year": "2025",
    }


def test_xiaoshi_client_rejects_unsafe_quant_dataset_identifier() -> None:
    client = XiaoshiClient(base_url="https://api.shizixi.com", api_key="test-key")
    try:
        with pytest.raises(ValueError):
            client.get_quant_data("../market/quote/000001")
    finally:
        client.close()

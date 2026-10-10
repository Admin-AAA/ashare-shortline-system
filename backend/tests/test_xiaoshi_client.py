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


def test_xiaoshi_market_source_declines_bulk_quotes_and_per_instrument_bars() -> None:
    """报价与逐标的日线在 v3 公开操作清单里都没有可用端点，两者都必须如实声明能力缺口。

    QuoteRequest 要完整市场快照：`POST /api/v3/market/quotes` 只用于小批量，全市场逐代码
    枚举会被平台以 `bulk_download_required` 暂停该 key 的实时报价 24 小时；平台指引的
    `/api/v3/data/market-snapshot` 返回 `operation_not_in_public_contract`。逐标的
    `/api/v3/data/kline/{code}` 随 `/api/v3/data/*` 整族退役（L-149）。适配器不得为满足
    请求去尝试会触发封禁或恒 404 的地址。
    """
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
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
        assert quotes.audit.status.value == "failure"
        assert quotes.quotes == ()
        assert "market/quotes 仅限小批量" in quotes.errors[0].message
        assert "tushare" in quotes.errors[0].message

        bars = adapter.fetch_daily_bars(
            DailyBarRequest((QuoteRef("000001", "SZ"),), start_date="2026-08-01", end_date="2026-08-07")
        )
        assert bars.audit.status.value == "failure"
        assert bars.as_legacy_rows_by_code() == {"000001": []}
        assert "does not serve per-instrument daily bars" in bars.errors[0].message
        assert "download-session" in bars.errors[0].message

        # 两条能力缺口都不得再向已退役端点发请求，否则面板会一直显示连接失败。
        assert requested_paths == []
    finally:
        http_client.close()


def test_xiaoshi_client_uses_current_history_contract() -> None:
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
        client.get_history_download_session(dataset="min1", code="000001", year="2026")
    finally:
        http_client.close()

    assert [request.url.path for request in calls] == [
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

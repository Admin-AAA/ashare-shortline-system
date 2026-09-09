from __future__ import annotations

from types import SimpleNamespace

from app.services.tushare_transport import (
    AuthMode,
    FailureCategory,
    HttpMethod,
    ProviderConfig,
    ProviderFailure,
    TushareHttpProvider,
    TushareProviderRouter,
)


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def json(self):
        return {"code": 0, "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]}}

    def close(self) -> None:
        return None


def test_get_provider_sends_params_and_keeps_api_key_in_header(monkeypatch) -> None:
    provider = TushareHttpProvider(
        ProviderConfig(
            name="promax",
            base_url="https://promax.example/tushare/pro",
            credential="secret-key",
            auth_mode=AuthMode.API_KEY_HEADER,
            http_method=HttpMethod.GET,
            append_api_path=True,
            rate_limit_per_minute=180,
        )
    )
    captured = {}

    def fake_get(url, *, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        return _FakeResponse()

    monkeypatch.setattr(provider._session, "get", fake_get)
    try:
        result = provider.request(
            "daily",
            params={"ts_code": "000001.SZ"},
            fields="trade_date,close",
        )
        assert result["code"] == 0
        assert captured["url"] == "https://promax.example/tushare/pro/daily"
        assert captured["params"] == {
            "ts_code": "000001.SZ",
            "fields": "trade_date,close",
        }
        assert provider._session.headers["X-API-Key"] == "secret-key"
        assert "secret-key" not in str(captured)
    finally:
        provider.close()


def test_get_provider_treats_http_202_as_data_pending(monkeypatch) -> None:
    provider = TushareHttpProvider(
        ProviderConfig(
            name="promax",
            base_url="https://promax.example/tushare/pro",
            credential="secret-key",
            auth_mode=AuthMode.API_KEY_HEADER,
            http_method=HttpMethod.GET,
            append_api_path=True,
            rate_limit_per_minute=180,
        )
    )

    class _PendingResponse:
        status_code = 202
        headers: dict[str, str] = {}

        def close(self) -> None:
            return None

    monkeypatch.setattr(provider._session, "get", lambda *args, **kwargs: _PendingResponse())
    try:
        try:
            provider.request("stk_mins", params={"ts_code": "000001.SZ"})
        except ProviderFailure as exc:
            assert exc.category is FailureCategory.DATA_PENDING
            assert exc.retryable is False
            assert exc.fallback_allowed is True
        else:
            raise AssertionError("HTTP 202 must not be accepted as completed minute data")
        assert provider.health_snapshot()["breaker_state"] == "closed"
    finally:
        provider.close()


class _FakeProvider:
    def __init__(
        self,
        name: str,
        priority: int,
        result: str,
        *,
        excluded_apis: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.config = SimpleNamespace(priority=priority)
        self.result = result
        self.excluded_apis = excluded_apis

    def supports(self, api_name: str) -> bool:
        return api_name not in self.excluded_apis

    def request(self, api_name: str, *, params=None, fields=""):
        if self.result == "failure":
            raise ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.PERMISSION,
                retryable=False,
                fallback_allowed=True,
            )
        return {"code": 0, "data": {"fields": ["ts_code"], "items": [[self.name]]}}


def test_realtime_route_prefers_promax() -> None:
    router = TushareProviderRouter(
        [
            _FakeProvider("promax", -2, "success"),
            _FakeProvider("ai_tool", 2, "success"),
        ]
    )

    result = router.request("rt_min", params={"ts_code": "000001.SZ"})

    assert result["data"]["items"] == [["promax"]]


def test_promax_is_only_excluded_from_incompatible_announcement_contract() -> None:
    router = TushareProviderRouter(
        [
            _FakeProvider(
                "promax",
                -2,
                "success",
                excluded_apis=frozenset({"anns_d"}),
            ),
            _FakeProvider("ai_tool", 2, "success"),
        ]
    )

    assert router.request("anns_d")["data"]["items"] == [["ai_tool"]]
    assert router.request("stk_mins")["data"]["items"] == [["promax"]]


def test_default_realtime_routes_have_promax_first() -> None:
    assert TushareProviderRouter.DEFAULT_API_ROUTES["rt_k"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["rt_min"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["fina_indicator"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["ths_member"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["stk_mins"][0] == "promax"
    assert "promax" not in TushareProviderRouter.DEFAULT_API_ROUTES["anns_d"]


def test_promax_minute_contract_accepts_canonical_bars() -> None:
    from app.services.tushare_client import _promax_minute_contract_issue

    envelope = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"],
            "items": [
                ["000001.SZ", "2026-08-31 09:30:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
                ["000001.SZ", "2026-08-31 09:31:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
                ["000001.SZ", "2026-08-31 13:00:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
            ],
        },
    }

    assert _promax_minute_contract_issue(envelope) is None


def test_promax_minute_contract_rejects_snapshot_rows() -> None:
    from app.services.tushare_client import _promax_minute_contract_issue

    envelope = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"],
            "items": [
                ["000001.SZ", "2026-09-01 13:41:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
                ["000001.SZ", "2026-09-01 13:41:59", "1min", 10, 10.1, 9.9, 10, 90, 900],
                ["000001.SZ", "2026-09-01 15:01:58", "1min", 10, 10.1, 9.9, 10, 90, 900],
            ],
        },
    }

    assert _promax_minute_contract_issue(envelope) == "non_minute_timestamp"


def test_promax_minute_contract_rejection_falls_back_to_next_provider(monkeypatch) -> None:
    from app.services.tushare_client import _PromaxHttpProvider

    promax = _PromaxHttpProvider(
        ProviderConfig(
            name="promax",
            base_url="https://promax.example/tushare/pro",
            credential="secret-key",
            auth_mode=AuthMode.API_KEY_HEADER,
            http_method=HttpMethod.GET,
            append_api_path=True,
            rate_limit_per_minute=180,
        )
    )

    class _SnapshotResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self):
            return {
                "code": 0,
                "data": {
                    "fields": ["ts_code", "trade_time"],
                    "items": [["000001.SZ", "2026-09-01 14:59:31"]],
                },
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr(promax._session, "get", lambda *args, **kwargs: _SnapshotResponse())
    router = TushareProviderRouter([promax, _FakeProvider("ai_tool", 2, "success")])
    try:
        result = router.request("stk_mins", params={"ts_code": "000001.SZ"})
        assert result["data"]["items"] == [["ai_tool"]]
    finally:
        promax.close()

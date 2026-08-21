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


def test_promax_is_excluded_from_incompatible_api_contracts() -> None:
    router = TushareProviderRouter(
        [
            _FakeProvider(
                "promax",
                -2,
                "success",
                excluded_apis=frozenset({"anns_d", "stk_mins"}),
            ),
            _FakeProvider("ai_tool", 2, "success"),
        ]
    )

    assert router.request("anns_d")["data"]["items"] == [["ai_tool"]]
    assert router.request("stk_mins")["data"]["items"] == [["ai_tool"]]


def test_default_realtime_routes_have_promax_first() -> None:
    assert TushareProviderRouter.DEFAULT_API_ROUTES["rt_k"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["rt_min"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["fina_indicator"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["ths_member"][0] == "promax"
    assert "promax" not in TushareProviderRouter.DEFAULT_API_ROUTES["anns_d"]
    assert "promax" not in TushareProviderRouter.DEFAULT_API_ROUTES["stk_mins"]

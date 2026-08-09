from __future__ import annotations

from types import SimpleNamespace

from app.services.tushare_transport import (
    AuthMode,
    FailureCategory,
    ProviderConfig,
    ProviderFailure,
    TushareHttpProvider,
    TushareProviderRouter,
)


def test_provider_proxy_is_configured_without_exposing_credentials() -> None:
    provider = TushareHttpProvider(
        ProviderConfig(
            name="ai_tool_key1",
            base_url="https://ai-tool.example/pro",
            credential="secret-key",
            auth_mode=AuthMode.API_KEY_HEADER,
            rate_limit_per_minute=90,
            proxy="http://user:password@proxy.example:2018",
            source="tushare_source_1",
        )
    )
    try:
        assert provider._session.proxies == {
            "http": "http://user:password@proxy.example:2018",
            "https": "http://user:password@proxy.example:2018",
        }
        snapshot = provider.health_snapshot()
        assert snapshot["proxy_configured"] is True
        assert snapshot["source"] == "tushare_source_1"
        assert "secret-key" not in str(snapshot)
        assert "password" not in str(snapshot)
    finally:
        provider.close()


class _FakeProvider:
    def __init__(self, name: str, priority: int, result: str) -> None:
        self.name = name
        self.config = SimpleNamespace(priority=priority)
        self.result = result

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


def test_minute_route_prefers_key1_then_key2() -> None:
    router = TushareProviderRouter(
        [
            _FakeProvider("ai_tool_key1", -3, "failure"),
            _FakeProvider("ai_tool_key2", -2, "success"),
            _FakeProvider("ai_tool", -1, "success"),
        ]
    )

    result = router.request("stk_mins", params={"ts_code": "000001.SZ"})

    assert result["data"]["items"] == [["ai_tool_key2"]]


def test_default_minute_routes_have_independent_keys_first() -> None:
    assert TushareProviderRouter.DEFAULT_API_ROUTES["stk_mins"][:3] == (
        "ai_tool_key1",
        "ai_tool_key2",
        "ai_tool",
    )
    assert TushareProviderRouter.DEFAULT_API_ROUTES["rt_min"][:3] == (
        "ai_tool_key1",
        "ai_tool_key2",
        "ai_tool",
    )


def test_dual_keys_have_distinct_source_identities() -> None:
    key1 = TushareHttpProvider(
        ProviderConfig(
            name="ai_tool_key1",
            source="tushare_source_1",
            base_url="https://ai-tool.example/pro",
            credential="secret-key-1",
            auth_mode=AuthMode.API_KEY_HEADER,
            rate_limit_per_minute=90,
        )
    )
    key2 = TushareHttpProvider(
        ProviderConfig(
            name="ai_tool_key2",
            source="tushare_source_2",
            base_url="https://ai-tool.example/pro",
            credential="secret-key-2",
            auth_mode=AuthMode.API_KEY_HEADER,
            rate_limit_per_minute=90,
        )
    )
    try:
        assert key1.health_snapshot()["source"] == "tushare_source_1"
        assert key2.health_snapshot()["source"] == "tushare_source_2"
        assert key1.health_snapshot()["source"] != key2.health_snapshot()["source"]
    finally:
        key1.close()
        key2.close()

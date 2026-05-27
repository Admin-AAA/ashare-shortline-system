import asyncio
import types

from app.services import model_router_service


class DummySettings:
    agent_skill_api_key = "env-key"
    agent_skill_base_url = "https://api.deepseek.com"
    agent_skill_timeout_seconds = 12.0
    agent_model = "deepseek-v4-pro"


class FakeDB:
    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = routes or {}

    def get(self, model, route_id: str):
        return self.routes.get(route_id)


def _route(**kwargs):
    data = {
        "route_id": "route_saved",
        "name": "已保存路由",
        "provider": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "stored-key",
        "priority": 1,
    }
    data.update(kwargs)
    return types.SimpleNamespace(**data)


def test_model_route_uses_stored_key_when_editing_without_new_key(monkeypatch):
    db = FakeDB({"route_saved": _route()})
    monkeypatch.setattr(model_router_service, "get_settings", lambda: DummySettings())

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["model"] == "deepseek-chat"
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"ok": true, "message": "pong"}'))]
            )

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key, base_url, timeout):
            assert api_key == "stored-key"
            assert base_url == "https://api.deepseek.com"
            assert timeout == 12.0
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setitem(model_router_service.test_model_route.__globals__, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setitem(__import__("sys").modules, "openai", types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    result = asyncio.run(model_router_service.test_model_route(db, {"route_id": "route_saved", "model": "deepseek-chat"}))

    assert result["ok"] is True
    assert result["key_source"] == "custom"
    assert result["route_id"] == "route_saved"


def test_model_route_returns_error_when_no_api_key_available(monkeypatch):
    class NoKeySettings:
        agent_skill_api_key = ""
        agent_skill_base_url = "https://api.deepseek.com"
        agent_skill_timeout_seconds = 12.0
        agent_model = "deepseek-v4-pro"

    monkeypatch.setattr(model_router_service, "get_settings", lambda: NoKeySettings())

    result = asyncio.run(
        model_router_service.test_model_route(
            FakeDB(),
            {
                "name": "草稿路由",
                "provider": "openai_compatible",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
            },
        )
    )

    assert result["ok"] is False
    assert "未配置可用 API Key" in result["message"]
    assert result["key_source"] == "missing"

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


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeActiveRouteDB:
    def __init__(self, rows):
        self.rows = rows
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def scalars(self, stmt):
        return FakeScalarResult([row for row in self.rows if row.enabled])

    def commit(self):
        self.committed = True

    def flush(self):
        pass


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


def test_active_model_routes_skip_disabled_default_route(monkeypatch):
    rows = [
        _route(
            route_id="route_primary",
            name="jucode",
            base_url="https://cf.jucode.top/v1",
            model="gpt-5.5",
            api_key="custom-key",
            priority=1,
            enabled=True,
        ),
        _route(
            route_id="route_env_default",
            name="默认模型 deepseek-v4-pro",
            model="deepseek-v4-pro",
            api_key="",
            priority=2,
            enabled=False,
        ),
    ]
    fake_db = FakeActiveRouteDB(rows)

    monkeypatch.setattr(model_router_service, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(model_router_service, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(model_router_service, "_ensure_default_route", lambda db: None)

    active = model_router_service.get_active_model_routes()

    assert [route.route_id for route in active] == ["route_primary"]
    assert active[0].model == "gpt-5.5"
    assert active[0].key_source == "custom"

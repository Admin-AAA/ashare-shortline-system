from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.tables import ModelRouteConfig


DEFAULT_ROUTE_ID = "route_env_default"


def _extract_json_object(text: str) -> object | None:
    """容错解析 JSON：剥离 ```json 围栏，并尝试提取首个 JSON 对象。

    部分 OpenAI 兼容模型（如 deepseek）会在 JSON 外加 markdown 代码围栏或
    前后缀说明文字，直接 json.loads 会失败。此函数优先原始解析，失败则
    逐步剥离围栏 / 抽取首个 {...} 子串，最大化兼容不同模型的返回格式。
    """
    if not text:
        return None
    candidate = text.strip()
    # 1) 直接解析
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        pass
    # 2) 剥离 ```json ... ``` 或 ``` ... ``` 围栏
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", candidate, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            pass
    # 3) 用正则提取首个 {...}（贪婪，跨行）
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


@dataclass(frozen=True)
class ResolvedModelRoute:
    route_id: str
    name: str
    provider: str
    base_url: str
    model: str
    api_key: str
    priority: int
    key_source: str

    @property
    def circuit_key(self) -> str:
        return f"{self.provider}:{self.model}:{self.route_id}"


def list_model_routes(db: Session) -> list[dict]:
    _ensure_default_route(db)
    rows = db.scalars(
        select(ModelRouteConfig).order_by(ModelRouteConfig.priority.asc(), ModelRouteConfig.updated_at.asc())
    ).all()
    _normalize_priorities(db, rows)
    settings = get_settings()
    return [_serialize_route(row, settings) for row in rows]


def create_model_route(db: Session, payload: dict) -> dict:
    _ensure_default_route(db)
    existing_count = len(db.scalars(select(ModelRouteConfig.route_id)).all())
    route = ModelRouteConfig(
        route_id=str(payload.get("route_id") or f"route_{uuid.uuid4().hex[:10]}"),
        priority=max(1, existing_count + 1),
    )
    db.add(route)
    _apply_route_payload(route, payload, creating=True)
    db.flush()
    rows = db.scalars(
        select(ModelRouteConfig).order_by(ModelRouteConfig.priority.asc(), ModelRouteConfig.updated_at.asc())
    ).all()
    _normalize_priorities(db, rows)
    db.commit()
    return _serialize_route(route, get_settings())


def update_model_route(db: Session, route_id: str, payload: dict) -> dict | None:
    _ensure_default_route(db)
    route = db.get(ModelRouteConfig, route_id)
    if route is None:
        return None
    _apply_route_payload(route, payload, creating=False)
    route.updated_at = datetime.utcnow()
    db.flush()
    rows = db.scalars(
        select(ModelRouteConfig).order_by(ModelRouteConfig.priority.asc(), ModelRouteConfig.updated_at.asc())
    ).all()
    _normalize_priorities(db, rows)
    db.commit()
    return _serialize_route(route, get_settings())


def delete_model_route(db: Session, route_id: str) -> bool:
    _ensure_default_route(db)
    route = db.get(ModelRouteConfig, route_id)
    if route is None:
        return False
    db.delete(route)
    db.flush()
    rows = db.scalars(
        select(ModelRouteConfig).order_by(ModelRouteConfig.priority.asc(), ModelRouteConfig.updated_at.asc())
    ).all()
    _normalize_priorities(db, rows)
    if not rows:
        _ensure_default_route(db)
    db.commit()
    return True


def reorder_model_routes(db: Session, route_ids: list[str]) -> list[dict]:
    _ensure_default_route(db)
    rows = db.scalars(select(ModelRouteConfig).order_by(ModelRouteConfig.priority.asc(), ModelRouteConfig.updated_at.asc())).all()
    row_map = {row.route_id: row for row in rows}
    ordered = [row_map[route_id] for route_id in route_ids if route_id in row_map]
    tail = [row for row in rows if row.route_id not in {item.route_id for item in ordered}]
    merged = ordered + tail
    changed = False
    for idx, row in enumerate(merged, start=1):
        if row.priority != idx:
            row.priority = idx
            row.updated_at = datetime.utcnow()
            changed = True
    if changed:
        db.commit()
    return [_serialize_route(row, get_settings()) for row in merged]


async def test_model_route(db: Session, payload: dict) -> dict:
    settings = get_settings()
    try:
        route = _resolve_route_for_test(db, payload, settings)
    except LookupError as exc:
        return {"ok": False, "message": str(exc)}

    if not route.api_key:
        return {
            "ok": False,
            "route_id": route.route_id,
            "route_name": route.name,
            "model": route.model,
            "base_url": route.base_url,
            "key_source": "missing",
            "message": "未配置可用 API Key，请先填写自定义 Key 或配置系统默认 Key",
        }
    if route.provider != "openai_compatible":
        return {
            "ok": False,
            "route_id": route.route_id,
            "route_name": route.name,
            "model": route.model,
            "base_url": route.base_url,
            "key_source": route.key_source,
            "message": f"暂不支持测试 provider={route.provider}，目前仅支持 OpenAI 兼容路由",
        }

    from openai import AsyncOpenAI

    timeout_seconds = max(5.0, min(float(settings.agent_skill_timeout_seconds or 30.0), 30.0))
    started_at = time.perf_counter()
    try:
        async with AsyncOpenAI(
            api_key=route.api_key,
            base_url=route.base_url,
            timeout=timeout_seconds,
        ) as client:
            response = await client.chat.completions.create(
                model=route.model,
                messages=[
                    {"role": "system", "content": "你是模型连通性测试助手，只返回 JSON。"},
                    {"role": "user", "content": '请返回 {"ok": true, "message": "pong"}'},
                ],
                temperature=0,
                max_tokens=256,
                response_format={"type": "json_object"},
            )
        latency_ms = round((time.perf_counter() - started_at) * 1000)
        content = str(response.choices[0].message.content or "").strip()
        if not content:
            return {
                "ok": False,
                "route_id": route.route_id,
                "route_name": route.name,
                "model": route.model,
                "base_url": route.base_url,
                "key_source": route.key_source,
                "message": f"{route.name} 返回内容为空，请检查模型响应格式",
            }
        # 容错解析：部分 OpenAI 兼容模型会在 JSON 外加 ```json 围栏或前后缀，
        # 严格 json.loads 会失败。先剥离围栏，再用正则提取首个 JSON 对象。
        parsed = _extract_json_object(content)
        if parsed is None:
            return {
                "ok": False,
                "route_id": route.route_id,
                "route_name": route.name,
                "model": route.model,
                "base_url": route.base_url,
                "key_source": route.key_source,
                "message": f"{route.name} 返回内容非合法 JSON：{content[:120]}",
            }
        return {
            "ok": True,
            "route_id": route.route_id,
            "route_name": route.name,
            "model": route.model,
            "base_url": route.base_url,
            "key_source": route.key_source,
            "latency_ms": latency_ms,
            "message": f"{route.name} 测试成功，模型 {route.model} 可用",
        }
    except Exception as exc:  # noqa: BLE001 - surface upstream connectivity errors to UI
        return {
            "ok": False,
            "route_id": route.route_id,
            "route_name": route.name,
            "model": route.model,
            "base_url": route.base_url,
            "key_source": route.key_source,
            "message": _format_test_route_error(route.name, route.model, exc),
        }


def get_active_model_routes() -> list[ResolvedModelRoute]:
    settings = get_settings()
    with SessionLocal() as db:
        _ensure_default_route(db)
        rows = db.scalars(
            select(ModelRouteConfig)
            .where(ModelRouteConfig.enabled.is_(True))
            .order_by(ModelRouteConfig.priority.asc(), ModelRouteConfig.updated_at.asc())
        ).all()
        _normalize_priorities(db, rows)
        active: list[ResolvedModelRoute] = []
        for row in rows:
            api_key = str(row.api_key or settings.agent_skill_api_key or "").strip()
            if not api_key:
                continue
            active.append(
                ResolvedModelRoute(
                    route_id=str(row.route_id),
                    name=str(row.name),
                    provider=str(row.provider or "openai_compatible"),
                    base_url=str(row.base_url or settings.agent_skill_base_url or "").strip(),
                    model=str(row.model or settings.agent_model),
                    api_key=api_key,
                    priority=int(row.priority or 1),
                    key_source="env_default" if not str(row.api_key or "").strip() else "custom",
                )
            )
        if active:
            db.commit()
            return active
        db.commit()
    return [
        ResolvedModelRoute(
            route_id=DEFAULT_ROUTE_ID,
            name=f"默认模型 {settings.agent_model}",
            provider="openai_compatible",
            base_url=settings.agent_skill_base_url,
            model=settings.agent_model,
            api_key=settings.agent_skill_api_key,
            priority=1,
            key_source="env_default",
        )
    ] if settings.agent_skill_api_key else []


def _ensure_default_route(db: Session) -> None:
    existing = db.scalars(select(ModelRouteConfig).limit(1)).first()
    if existing is not None:
        return
    settings = get_settings()
    default_route = ModelRouteConfig(
        route_id=DEFAULT_ROUTE_ID,
        name=f"默认模型 {settings.agent_model}",
        provider="openai_compatible",
        base_url=settings.agent_skill_base_url,
        model=settings.agent_model,
        api_key="",
        priority=1,
        enabled=True,
    )
    db.add(default_route)
    db.commit()


def _normalize_priorities(db: Session, rows: list[ModelRouteConfig]) -> None:
    changed = False
    for idx, row in enumerate(rows, start=1):
        if row.priority != idx:
            row.priority = idx
            row.updated_at = datetime.utcnow()
            changed = True
    if changed:
        db.flush()


def _apply_route_payload(route: ModelRouteConfig, payload: dict, *, creating: bool) -> None:
    settings = get_settings()
    route.name = str(payload.get("name") or route.name or f"模型 {payload.get('model') or settings.agent_model}").strip()
    route.provider = str(payload.get("provider") or route.provider or "openai_compatible").strip() or "openai_compatible"
    route.base_url = str(payload.get("base_url") or route.base_url or settings.agent_skill_base_url).strip()
    route.model = str(payload.get("model") or route.model or settings.agent_model).strip() or settings.agent_model
    route.enabled = bool(payload.get("enabled", route.enabled if not creating else True))
    route.priority = max(1, int(payload.get("priority") or route.priority or 1))

    # 独立配置参数（可选，为空时使用系统默认值）
    if "max_tokens" in payload:
        val = payload.get("max_tokens")
        route.max_tokens = int(val) if val is not None and val != "" else None
    if "temperature" in payload:
        val = payload.get("temperature")
        route.temperature = float(val) if val is not None and val != "" else None
    if "timeout_seconds" in payload:
        val = payload.get("timeout_seconds")
        route.timeout_seconds = float(val) if val is not None and val != "" else None

    if creating:
        api_key = str(payload.get("api_key") or "").strip()
        route.api_key = api_key
    else:
        if payload.get("clear_api_key"):
            route.api_key = ""
        elif "api_key" in payload and payload.get("api_key") is not None:
            api_key = str(payload.get("api_key") or "").strip()
            if api_key:
                route.api_key = api_key


def _resolve_route_for_test(db: Session, payload: dict, settings) -> ResolvedModelRoute:
    route_id = str(payload.get("route_id") or "").strip()
    existing = db.get(ModelRouteConfig, route_id) if route_id else None
    if route_id and existing is None:
        raise LookupError("模型路由不存在，请先刷新列表后重试")

    provider = str(payload.get("provider") or (existing.provider if existing else "") or "openai_compatible").strip() or "openai_compatible"
    base_url = str(payload.get("base_url") or (existing.base_url if existing else "") or settings.agent_skill_base_url).strip()
    model = str(payload.get("model") or (existing.model if existing else "") or settings.agent_model).strip() or settings.agent_model
    name = str(payload.get("name") or (existing.name if existing else "") or f"模型 {model}").strip() or f"模型 {model}"
    api_key, key_source = _resolve_test_api_key(existing, payload, settings)

    return ResolvedModelRoute(
        route_id=route_id or "draft_route_test",
        name=name,
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        priority=int(existing.priority if existing and existing.priority else 1),
        key_source=key_source,
    )


def _resolve_test_api_key(route: ModelRouteConfig | None, payload: dict, settings) -> tuple[str, str]:
    env_api_key = str(settings.agent_skill_api_key or "").strip()
    draft_api_key = str(payload.get("api_key") or "").strip() if "api_key" in payload else ""
    stored_api_key = str(route.api_key or "").strip() if route else ""

    if payload.get("clear_api_key"):
        return env_api_key, "env_default" if env_api_key else "missing"
    if draft_api_key:
        return draft_api_key, "custom"
    if stored_api_key:
        return stored_api_key, "custom"
    if env_api_key:
        return env_api_key, "env_default"
    return "", "missing"


def _format_test_route_error(route_name: str, model: str, exc: Exception) -> str:
    text = str(exc).strip()
    lowered = text.lower()

    if "insufficient balance" in lowered or "error code: 402" in lowered:
        return f"{route_name} 测试失败：接口已连通，但账号余额不足或额度已用完"
    if any(marker in lowered for marker in ("invalid api key", "incorrect api key", "authentication", "unauthorized", "forbidden", "error code: 401", "error code: 403")):
        return f"{route_name} 测试失败：接口已连通，但 API Key 无效、已失效或无权限"
    if "model" in lowered and any(marker in lowered for marker in ("not found", "does not exist", "unsupported")):
        return f"{route_name} 测试失败：接口已连通，但模型 {model} 不可用"
    if text:
        return f"{route_name} 测试失败：{text}"
    return f"{route_name} 测试失败：未知错误"


def _serialize_route(route: ModelRouteConfig, settings) -> dict:
    custom_key = str(route.api_key or "").strip()
    uses_default_key = not custom_key
    default_key_available = bool(settings.agent_skill_api_key)
    return {
        "route_id": route.route_id,
        "name": route.name,
        "provider": route.provider,
        "base_url": route.base_url,
        "model": route.model,
        "priority": int(route.priority or 1),
        "enabled": bool(route.enabled),
        "uses_default_key": uses_default_key,
        "default_key_available": default_key_available,
        "has_api_key": bool(custom_key or settings.agent_skill_api_key),
        "key_source": "env_default" if uses_default_key else "custom",
        "api_key_masked": "使用系统默认 Key" if uses_default_key and default_key_available else _mask_key(custom_key),
        # 独立配置参数（为空时使用系统默认值）
        "max_tokens": route.max_tokens,
        "temperature": route.temperature,
        "timeout_seconds": route.timeout_seconds,
        "created_at": route.created_at.isoformat() if route.created_at else None,
        "updated_at": route.updated_at.isoformat() if route.updated_at else None,
    }


def _mask_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "未配置"
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}***{text[-4:]}"

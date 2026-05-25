from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.tables import ModelRouteConfig


DEFAULT_ROUTE_ID = "route_env_default"


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

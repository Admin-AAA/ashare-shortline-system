from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import httpx
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

# 部分中转网关（如 toolcode.top）的 WAF 会拦截 openai SDK 默认的
# "OpenAI/Python x.y.z" User-Agent（403 "Your request was blocked"），
# 统一覆盖为自定义 UA 规避误拦；对不拦截的网关无副作用。
OPENAI_CLIENT_HEADERS = {"User-Agent": "ashare-workbench/1.0"}


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
    valid: bool = True
    # 路由级推理参数覆盖（None 表示跟随全局默认）
    max_tokens: int | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None

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
            "message": "未配置可用 API Key，请为这条路由填写独立 Key",
        }
    if not route.base_url or not route.model:
        return {
            "ok": False,
            "route_id": route.route_id,
            "route_name": route.name,
            "model": route.model,
            "base_url": route.base_url,
            "key_source": route.key_source,
            "message": "未配置 Base URL 或模型，请先补全该路由的 Base URL 与模型",
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
            default_headers=OPENAI_CLIENT_HEADERS,
        ) as client:
            response = await client.chat.completions.create(
                model=route.model,
                messages=[
                    {"role": "system", "content": "你是模型连通性测试助手，只返回 JSON。"},
                    {"role": "user", "content": '请返回 {"ok": true, "message": "pong"}'},
                ],
                temperature=route.temperature if route.temperature is not None else 0,
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
    """返回当前可用的模型路由（按优先级升序）。

    路由表是唯一真相源：每条路由必须自带 api_key / base_url / model。
    全局 env key（ASHARE_AGENT_SKILL_API_KEY）与 config 默认 key/url 均不参与
    解析，也不作为回退。缺少 key 或 base_url/model 任意一项的路由视为无效，
    直接排除。无任何有效路由时返回空列表（调用方应据此明确报错，而非静默降级）。
    """
    with SessionLocal() as db:
        _ensure_default_route(db)
        rows = db.scalars(
            select(ModelRouteConfig)
            .where(ModelRouteConfig.enabled.is_(True))
            .order_by(ModelRouteConfig.priority.asc(), ModelRouteConfig.updated_at.asc())
        ).all()
        _normalize_priorities(db, rows)
        db.commit()
        active: list[ResolvedModelRoute] = []
        for row in rows:
            api_key = str(row.api_key or "").strip()
            base_url = str(row.base_url or "").strip()
            model = str(row.model or "").strip()
            has_key = bool(api_key)
            valid = bool(api_key and base_url and model)
            active.append(
                ResolvedModelRoute(
                    route_id=str(row.route_id),
                    name=str(row.name),
                    provider=str(row.provider or "openai_compatible"),
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    priority=int(row.priority or 1),
                    key_source="custom" if has_key else "missing",
                    valid=valid,
                    max_tokens=row.max_tokens,
                    temperature=row.temperature,
                    timeout_seconds=row.timeout_seconds,
                )
            )
        return [r for r in active if r.valid]


# route-health 缓存：GET /models 需 2 次外部 HTTP（~2.7s），MetricsPage 每次刷新都
# 重发太慢。30s TTL 与 metrics 缓存一致，route 配置变更后 30s 内自动刷新。
_route_health_cache: tuple[float, list[dict]] | None = None
_ROUTE_HEALTH_TTL = 30.0


async def check_route_availability() -> list[dict]:
    """并发检查每个 enabled 路由的 base_url 可达性 + key 有效性（GET /models）。

    OpenAI 兼容端点标准：GET {base_url}/models 返回模型列表，需 Authorization。
    轻量（不消耗 chat 额度），5s 超时。返回 [{route_id, name, base_url, model,
    reachable, status_code, model_count, error}]。

    价值（T8 可观测性）：07-22 LLM 65% 失败但用户不知是 grok-4.5 还是 kimi-k3 的问题。
    kimi-k3 base_url=api.kimi.com/coding/v1 可疑（标准是 api.moonshot.cn/v1）。
    本检查让路由可达性可见，运维可快速定位 base_url/key 配置错误。

    30s 缓存避免每次 MetricsPage 刷新都重发 2 次外部 HTTP（~2.7s → <0.01s 命中）。
    """
    global _route_health_cache
    now = time.time()
    if _route_health_cache is not None and (now - _route_health_cache[0]) < _ROUTE_HEALTH_TTL:
        return _route_health_cache[1]

    routes = get_active_model_routes()

    async def _check(route: ResolvedModelRoute) -> dict:
        base = str(route.base_url or "").rstrip("/")
        url = f"{base}/models"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {route.api_key}"})
            model_count = 0
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    models = data.get("data") or data.get("models") or []
                    model_count = len(models) if isinstance(models, list) else 0
                except Exception:
                    model_count = 0
            # 401/403 = 鉴权失败（key 无效/过期），base_url 可达但路由不可用；
            # 5xx = 上游不可用。两者都标 reachable=False，让用户知道路由实际不可用。
            ok = resp.status_code < 400
            err_msg = None
            if resp.status_code in (401, 403):
                err_msg = f"鉴权失败 HTTP {resp.status_code}（key 无效/过期，需更新路由配置）"
            elif resp.status_code >= 500:
                err_msg = f"HTTP {resp.status_code}"
            return {
                "route_id": route.route_id,
                "name": route.name,
                "base_url": base,
                "model": route.model,
                "reachable": ok,
                "status_code": resp.status_code,
                "model_count": model_count,
                "error": err_msg,
            }
        except httpx.TimeoutException:
            return {"route_id": route.route_id, "name": route.name, "base_url": base,
                    "model": route.model, "reachable": False, "status_code": None,
                    "model_count": 0, "error": "timeout (>5s)"}
        except Exception as exc:
            return {"route_id": route.route_id, "name": route.name, "base_url": base,
                    "model": route.model, "reachable": False, "status_code": None,
                    "model_count": 0, "error": str(exc)[:120]}

    results = await asyncio.gather(*[_check(r) for r in routes])
    # 附带 circuit_breaker 状态（进程内内存单例）：base_url 可达但熔断中时实际调用仍失败。
    # 让用户区分「配置错（base_url 不可达）」vs「上游故障熔断（可达但 503 累积触发」。
    try:
        from app.agents.circuit_breaker import get_circuit_breaker
        breaker = get_circuit_breaker()
        for r, route in zip(results, routes):
            r["circuit"] = breaker.status(route.circuit_key)
    except Exception:
        for r in results:
            r["circuit"] = None
    _route_health_cache = (now, results)
    return results


def has_active_model_routes() -> bool:
    """是否存在至少一条可用（自带 key/base_url/model）的模型路由。"""
    try:
        return bool(get_active_model_routes())
    except Exception:
        return False


def _ensure_default_route(db: Session) -> None:
    """库为空时栽植一条占位路由，提示用户去配置。

    该占位路由的 base_url / model / api_key 均为空，属于「无效路由」，
    不会被 get_active_model_routes() 选中——不存在「全局默认 key/url」回退。
    用户必须在模型路由表中为每条路由填写独立 Key 后才可使用。
    """
    existing = db.scalars(select(ModelRouteConfig).limit(1)).first()
    if existing is not None:
        return
    default_route = ModelRouteConfig(
        route_id=DEFAULT_ROUTE_ID,
        name="默认模型路由（请配置 Base URL / 模型 / API Key）",
        provider="openai_compatible",
        base_url="",
        model="",
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
    # 注意：不再回退到 config 默认 base_url/model（已弃用），缺失即为空，
    # 由调用方/UI 负责填写，避免出现「隐藏的默认 deepseek 路由」。
    fallback_name = route.name or f"模型 {payload.get('model') or '路由'}"
    route.name = str(payload.get("name") or fallback_name).strip()
    route.provider = str(payload.get("provider") or route.provider or "openai_compatible").strip() or "openai_compatible"
    route.base_url = str(payload.get("base_url") or route.base_url or "").strip()
    route.model = str(payload.get("model") or route.model or "").strip() or ""
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


def _coalesce_num(payload: dict, existing, key: str, caster):
    """表单本次填写的值优先；未填则用路由已存值；空串视为未填。"""
    if key in payload:
        val = payload.get(key)
        if val is not None and val != "":
            return caster(val)
    if existing is not None:
        return getattr(existing, key, None)
    return None


def _resolve_route_for_test(db: Session, payload: dict, settings) -> ResolvedModelRoute:
    route_id = str(payload.get("route_id") or "").strip()
    existing = db.get(ModelRouteConfig, route_id) if route_id else None
    if route_id and existing is None:
        raise LookupError("模型路由不存在，请先刷新列表后重试")

    provider = str(payload.get("provider") or (existing.provider if existing else "") or "openai_compatible").strip() or "openai_compatible"
    base_url = str(payload.get("base_url") or (existing.base_url if existing else "") or "").strip()
    model = str(payload.get("model") or (existing.model if existing else "") or "").strip() or ""
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
        max_tokens=_coalesce_num(payload, existing, "max_tokens", int),
        temperature=_coalesce_num(payload, existing, "temperature", float),
        timeout_seconds=_coalesce_num(payload, existing, "timeout_seconds", float),
    )


def _resolve_test_api_key(route: ModelRouteConfig | None, payload: dict, settings) -> tuple[str, str]:
    """解析测试连接所用的 key。

    仅从「本次表单输入的 key」或「路由自身已存储的 key」取，绝不回退到全局
    env key（ASHARE_AGENT_SKILL_API_KEY，已弃用）。清除操作时返回空 key。
    """
    draft_api_key = str(payload.get("api_key") or "").strip() if "api_key" in payload else ""
    stored_api_key = str(route.api_key or "").strip() if route else ""

    if payload.get("clear_api_key"):
        return "", "missing"
    if draft_api_key:
        return draft_api_key, "custom"
    if stored_api_key:
        return stored_api_key, "custom"
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
    has_api_key = bool(custom_key)
    return {
        "route_id": route.route_id,
        "name": route.name,
        "provider": route.provider,
        "base_url": route.base_url,
        "model": route.model,
        "priority": int(route.priority or 1),
        "enabled": bool(route.enabled),
        "uses_default_key": False,
        "default_key_available": False,
        "has_api_key": has_api_key,
        "key_source": "custom" if has_api_key else "missing",
        "api_key_masked": _mask_key(custom_key) if has_api_key else "未配置",
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

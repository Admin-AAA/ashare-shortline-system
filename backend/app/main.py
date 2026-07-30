import logging
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import ORJSONResponse
from starlette.concurrency import run_in_threadpool

from app.api import admin, backtests, dashboard, data_sources, frontend_logs, home, llm, metrics, outcomes, presets, records, recommendations, runs, skills, stats, strategies
from app.core.config import get_settings
from app.core.logger import setup_logging
from app.db.init_db import init_db
from app.db.alembic_runtime import get_alembic_health
from app.services.factors import bootstrap_default_factor_registry
from app.schemas.common import AuthStatusResponse, HealthResponse, OkResponse

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("app.access")
settings = get_settings()

_AUTH_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/status",
    "/api/health",
}
_AUTH_PROTECTED_PATHS = {
    "/docs",
    "/openapi.json",
    "/redoc",
}

# ORJSONResponse 作为默认 response_class：orjson 序列化比标准 json 快 2-3x，
# 全局加速所有 API 端点的 JSON 响应（/api/strategies 1.49s 中 ~0.55s 是序列化）。
# 现有显式 JSONResponse 返回（密码检查等）不受影响。
app = FastAPI(title="AShare Shortline System", default_response_class=ORJSONResponse)
_startup_db_error: str | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _access_token() -> str:
    return sha256(settings.access_password.encode("utf-8")).hexdigest()


def _auth_enabled() -> bool:
    return bool(settings.access_password)


def _request_is_authorized(request: Request) -> bool:
    if not _auth_enabled():
        return True
    token = request.cookies.get(settings.access_cookie_name, "")
    return secrets.compare_digest(token, _access_token())


def _normalize_api_path(path: str) -> str:
    """折叠路径参数，避免 run_id / 股票代码等造成高基数。

    例：/api/runs/run_20260729_095923_e475f7 → /api/runs/{id}
        /api/recommendations/600519 → /api/recommendations/{id}
    """
    parts = []
    for seg in path.split("/"):
        if not seg:
            continue
        if seg.isdigit() or seg.startswith("run_") or (len(seg) == 6 and seg[:6].isdigit()):
            parts.append("{id}")
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


def _record_api_metric(method: str, path: str, status_code: int, duration_ms: float) -> None:
    """持久化 /api/ GET 只读端点延迟，供 self_check 计算 api_p95。

    只采样 GET（驾驶舱只读路径，POST/写操作延迟语义不同）；失败静默，绝不阻断请求。
    """
    if method != "GET" or not path.startswith("/api/") or path == "/api/health":
        return
    try:
        from app.db.session import SessionLocal
        from app.models.tables import ApiRequestMetric

        db = SessionLocal()
        try:
            db.add(ApiRequestMetric(
                method=method,
                path=_normalize_api_path(path),
                status_code=status_code,
                duration_ms=round(duration_ms, 1),
            ))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - 监控写库失败不能影响业务请求
        pass


@app.middleware("http")
async def catch_unhandled_exceptions(request: Request, call_next):
    start = __import__("time").time()
    try:
        needs_auth = (
            request.url.path in _AUTH_PROTECTED_PATHS
            or (request.url.path.startswith("/api/") and request.url.path not in _AUTH_EXEMPT_PATHS)
        )
        if needs_auth:
            if not _request_is_authorized(request):
                return JSONResponse(status_code=401, content={"detail": "需要访问密码"})
        response = await call_next(request)
        elapsed = (__import__("time").time() - start) * 1000
        if elapsed > 3000:
            access_logger.warning("%s %s → %s (%.0fms) [SLOW]", request.method, request.url.path, response.status_code, elapsed)
        else:
            access_logger.info("%s %s → %s (%.0fms)", request.method, request.url.path, response.status_code, elapsed)
        await run_in_threadpool(_record_api_metric, request.method, request.url.path, response.status_code, elapsed)
        return response
    except Exception:
        elapsed = (__import__("time").time() - start) * 1000
        access_logger.error("%s %s → 500 (%.0fms) [ERROR]", request.method, request.url.path, elapsed)
        logger.exception("Unhandled request exception: %s %s", request.method, request.url.path)
        await run_in_threadpool(_record_api_metric, request.method, request.url.path, 500, elapsed)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


@app.on_event("startup")
def on_startup() -> None:
    global _startup_db_error
    setup_logging()
    bootstrap_default_factor_registry()
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001 - keep the frontend reachable during DB outages
        _startup_db_error = str(exc)
        logger.exception("数据库初始化失败，后端以降级模式启动")
        return
    _startup_db_error = None
    from app.db.session import SessionLocal
    from app.services.cost_service import recover_scheduled_task_runs
    from app.services.workbench_service import recover_interrupted_runs
    db = SessionLocal()
    try:
        recover_interrupted_runs(db)
        recover_scheduled_task_runs(db)
    finally:
        db.close()
    _warmup_initial_state()
    _warmup_data_health_cache()
    _warmup_pool_cache(delay_seconds=2.0)


def _warmup_initial_state() -> None:
    """启动阶段同步预热首屏所需缓存，确保首访不走冷链路。"""
    from app.db.session import SessionLocal
    from app.services.workbench_service import get_initial_workbench_state

    started_at = perf_counter()
    db = SessionLocal()
    try:
        get_initial_workbench_state(db)
        logger.info("首屏缓存预热完成 (%.0fms)", (perf_counter() - started_at) * 1000)
    except Exception:
        logger.exception("首屏缓存预热失败")
    finally:
        db.close()


def _warmup_data_health_cache() -> None:
    """后台生成数据健康诊断，避免服务重启后的首次页面访问走冷链路。"""
    import threading

    def _warm():
        from app.db.session import SessionLocal
        from app.services.data_health_service import diagnose_all

        started_at = perf_counter()
        db = SessionLocal()
        try:
            diagnose_all(db)
            logger.info("数据健康缓存预热完成 (%.0fms)", (perf_counter() - started_at) * 1000)
        except Exception:
            logger.exception("数据健康缓存预热失败")
        finally:
            db.close()

    threading.Thread(target=_warm, name="data-health-warmup", daemon=True).start()


def _warmup_pool_cache(delay_seconds: float = 0.0) -> None:
    """后台延后预热本地池状态，避免与首访关键链路抢占资源。"""
    import threading
    import time as _time

    def _warm():
        if delay_seconds > 0:
            _time.sleep(delay_seconds)
        from app.db.session import SessionLocal
        from app.services.data_sources import get_local_pool_status
        from app.api.data_sources import _pool_cache_key, _pool_status_cache, _pool_status_lock

        db = SessionLocal()
        try:
            data = get_local_pool_status(
                db,
                unified_limit=0,
                unified_offset=0,
                bars_limit=0,
                bars_offset=0,
                include_details=False,
            )
            with _pool_status_lock:
                _pool_status_cache[_pool_cache_key(include_details=False)] = {"ts": _time.monotonic(), "data": data}
            logger.info("数据池缓存预热完成")
        except Exception:
            logger.exception("数据池缓存预热失败")
        finally:
            db.close()
    threading.Thread(target=_warm, daemon=True).start()


@app.get("/api/health", response_model=HealthResponse)
def health():
    migration = get_alembic_health()
    if migration.status == "error":
        database = "migration_error"
    elif _startup_db_error:
        database = "unavailable"
    else:
        database = "ok"
    return {
        "ok": database == "ok",
        "database": database,
        "application": "ok" if database == "ok" else "degraded",
        "migration": migration.as_dict(),
    }


@app.get("/api/auth/status", response_model=AuthStatusResponse)
def auth_status(request: Request):
    return {"enabled": _auth_enabled(), "authenticated": _request_is_authorized(request)}


@app.post("/api/auth/login", response_model=OkResponse)
async def auth_login(request: Request):
    payload = await request.json()
    password = str(payload.get("password", ""))
    if not _auth_enabled() or secrets.compare_digest(password, settings.access_password):
        expires = datetime.now(timezone.utc) + timedelta(days=settings.access_session_days)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            settings.access_cookie_name,
            _access_token(),
            httponly=True,
            samesite="lax",
            secure=False,
            expires=expires,
        )
        return response
    return JSONResponse(status_code=401, content={"detail": "密码不正确"})


@app.post("/api/auth/logout", response_model=OkResponse)
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(settings.access_cookie_name)
    return response


app.include_router(recommendations.router)
app.include_router(records.router)
app.include_router(dashboard.router)
app.include_router(data_sources.router)
app.include_router(home.router)
app.include_router(presets.router)
app.include_router(runs.router)
app.include_router(backtests.router)
app.include_router(skills.router)
app.include_router(strategies.router)
app.include_router(stats.router)
app.include_router(metrics.router)
app.include_router(outcomes.router)
app.include_router(admin.router)
app.include_router(llm.router)
app.include_router(frontend_logs.router)
from app.api.stock_matrix import router as stock_matrix_router
app.include_router(stock_matrix_router)

import logging
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, dashboard, data_sources, frontend_logs, home, metrics, outcomes, presets, recommendations, runs, skills, stats
from app.core.config import get_settings
from app.core.logger import setup_logging
from app.db.init_db import init_db

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

app = FastAPI(title="AShare Shortline System")
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
        return response
    except Exception:
        elapsed = (__import__("time").time() - start) * 1000
        access_logger.error("%s %s → 500 (%.0fms) [ERROR]", request.method, request.url.path, elapsed)
        logger.exception("Unhandled request exception: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


@app.on_event("startup")
def on_startup() -> None:
    global _startup_db_error
    setup_logging()
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
    from app.services.scheduler_service import start_scheduler
    start_scheduler()
    _warmup_initial_state()
    _warmup_pool_cache(delay_seconds=15.0)


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


@app.on_event("shutdown")
def on_shutdown() -> None:
    from app.services.scheduler_service import stop_scheduler
    stop_scheduler()


@app.get("/api/health")
def health():
    return {"ok": True, "database": "degraded" if _startup_db_error else "ok"}


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {"enabled": _auth_enabled(), "authenticated": _request_is_authorized(request)}


@app.post("/api/auth/login")
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


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(settings.access_cookie_name)
    return response


app.include_router(recommendations.router)
app.include_router(dashboard.router)
app.include_router(data_sources.router)
app.include_router(home.router)
app.include_router(presets.router)
app.include_router(runs.router)
app.include_router(skills.router)
app.include_router(stats.router)
app.include_router(metrics.router)
app.include_router(outcomes.router)
app.include_router(admin.router)
app.include_router(frontend_logs.router)

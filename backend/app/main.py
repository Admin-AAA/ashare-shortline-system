import logging
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, data_sources, home, metrics, outcomes, presets, recommendations, runs, skills, stats
from app.core.config import get_settings
from app.core.logger import setup_logging
from app.db.init_db import init_db

logger = logging.getLogger(__name__)
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
    try:
        needs_auth = (
            request.url.path in _AUTH_PROTECTED_PATHS
            or (request.url.path.startswith("/api/") and request.url.path not in _AUTH_EXEMPT_PATHS)
        )
        if needs_auth:
            if not _request_is_authorized(request):
                return JSONResponse(status_code=401, content={"detail": "需要访问密码"})
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled request exception: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


@app.on_event("startup")
def on_startup() -> None:
    setup_logging()
    init_db()
    from app.db.session import SessionLocal
    from app.services.workbench_service import recover_interrupted_runs
    db = SessionLocal()
    try:
        recover_interrupted_runs(db)
    finally:
        db.close()
    from app.services.scheduler_service import start_scheduler
    start_scheduler()
    _warmup_pool_cache()


def _warmup_pool_cache() -> None:
    """后台预热数据池缓存，避免首个用户请求等待。"""
    import threading
    import time as _time
    def _warm():
        from app.db.session import SessionLocal
        from app.services.data_sources import get_local_pool_status
        from app.api.data_sources import _pool_status_cache, _pool_status_lock, _POOL_CACHE_KEY
        db = SessionLocal()
        try:
            data = get_local_pool_status(db, unified_limit=50, unified_offset=0, bars_limit=50, bars_offset=0)
            with _pool_status_lock:
                _pool_status_cache[_POOL_CACHE_KEY] = {"ts": _time.monotonic(), "data": data}
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
    return {"ok": True}


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
app.include_router(data_sources.router)
app.include_router(home.router)
app.include_router(presets.router)
app.include_router(runs.router)
app.include_router(skills.router)
app.include_router(stats.router)
app.include_router(metrics.router)
app.include_router(outcomes.router)
app.include_router(admin.router)

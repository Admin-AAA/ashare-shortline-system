import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, data_sources, home, metrics, outcomes, presets, recommendations, runs, skills, stats
from app.core.logger import setup_logging
from app.db.init_db import init_db

logger = logging.getLogger(__name__)

app = FastAPI(title="AShare Shortline System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def catch_unhandled_exceptions(request: Request, call_next):
    try:
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

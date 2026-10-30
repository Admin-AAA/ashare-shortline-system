import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api import midterm
from app.api import admin, backtests, benchmarks, dashboard, data_sources, frontend_logs, home, llm, metrics, outcomes, presets, records, recommendations, runs, skills, stats, strategies
from app.core.config import get_settings
from app.core.logger import setup_logging
from app.db.init_db import init_db
from app.db.alembic_runtime import get_alembic_health
from app.governance.scope_guard import ScopeViolation, assert_no_brokerage_access
from app.services.factors import bootstrap_default_factor_registry
from app.services.observability import (
    ApplicationLifespan,
    correlation_scope,
    log_observation,
)
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

@asynccontextmanager
async def application_lifespan(_app: FastAPI):
    """Own process resources without changing application read authority."""
    lifecycle = ApplicationLifespan()

    def close_database_pool() -> None:
        from app.db.session import engine

        engine.dispose()

    def close_provider_sessions() -> None:
        from app.services.tushare_client import clear_pro_cache

        clear_pro_cache()

    # Reverse shutdown order: providers first, then the database pool.
    lifecycle.add_cleanup("database_pool", close_database_pool)
    lifecycle.add_cleanup("provider_sessions", close_provider_sessions)
    lifecycle.startup(on_startup)
    try:
        yield
    finally:
        failures = lifecycle.shutdown()
        if failures:
            logger.error("application shutdown cleanup failures: %s", ",".join(failures))


from app.core.offset_json import OffsetJSONResponse  # noqa: E402

app = FastAPI(
    title="AShare Shortline System",
    lifespan=application_lifespan,
    default_response_class=OffsetJSONResponse,
)
_startup_db_error: str | None = None

# CORS 收紧（2026-08-02 修复）：原 allow_origins=["*"] + allow_credentials=True
# 违反浏览器同源安全最佳实践（且该组合实际不被浏览器支持）。本系统前端
# 经 vite proxy 同源访问（5174 → 8765），跨域仅影响本机直连场景，显式
# 列出本机 origin 即可，无需开放任意来源。
_LOCAL_ORIGINS = [
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:8765",
    "http://127.0.0.1:8765",
]

from starlette.middleware.gzip import GZipMiddleware  # noqa: E402

# 大 JSON（/api/runs、/api/records/runs 数百 KB~MB）压缩传输；内容不变。
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_LOCAL_ORIGINS,
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


# 性能测量脚本（backend/scripts/perf/）发的请求带这个头：不写 api_request_metrics，免得上千条测量样本
# 污染 self_check 的 api_p95。只认来自本机回环地址的请求，外部带这个头也照常记录。
PERF_PROBE_HEADER = "x-ashare-perf-probe"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_perf_probe(request: Request) -> bool:
    if request.headers.get(PERF_PROBE_HEADER) != "1":
        return False
    client = request.client
    return client is not None and client.host in _LOOPBACK_HOSTS


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
        if not _is_perf_probe(request):
            await run_in_threadpool(_record_api_metric, request.method, request.url.path, response.status_code, elapsed)
        return response
    except Exception:
        elapsed = (__import__("time").time() - start) * 1000
        access_logger.error("%s %s → 500 (%.0fms) [ERROR]", request.method, request.url.path, elapsed)
        logger.exception("Unhandled request exception: %s %s", request.method, request.url.path)
        if not _is_perf_probe(request):
            await run_in_threadpool(_record_api_metric, request.method, request.url.path, 500, elapsed)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )


_BROKERAGE_GUARD_METHODS = frozenset({"POST", "PUT", "PATCH"})


@app.middleware("http")
async def brokerage_scope_guard(request: Request, call_next):
    """Scope_Guard（S0 任务 0.10）：券商资源访问在任何 secret 解析语义之前 4xx 拒绝。

    最外层中间件（后注册先执行），只扫描 /api/ 写请求的 JSON payload；
    违规时返回 403 与安全拒绝事件（仅 identity/hash/reason），正常流量原样放行。
    """
    if (
        request.method in _BROKERAGE_GUARD_METHODS
        and request.url.path.startswith("/api/")
        and "application/json" in request.headers.get("content-type", "").lower()
    ):
        body = await request.body()
        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            payload = None
        try:
            assert_no_brokerage_access(
                payload, context=f"http:{request.method} {request.url.path}"
            )
        except ScopeViolation as exc:
            return JSONResponse(status_code=403, content={"detail": str(exc), "event": exc.event})
        # 重新装配 receive 通道，让下游端点仍能读取请求体。
        body_replayed = False

        async def _replay_receive() -> dict:
            nonlocal body_replayed
            if body_replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            body_replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, _replay_receive)
    return await call_next(request)


@app.middleware("http")
async def correlation_identity(request: Request, call_next):
    """Propagate one sanitized correlation identity across the request boundary."""
    with correlation_scope(request.headers.get("X-Correlation-ID")) as correlation_id:
        log_observation(
            logger,
            stage="api",
            reason_code="REQUEST_ACCEPTED",
            identity=f"{request.method}:{_normalize_api_path(request.url.path)}",
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


def on_startup() -> None:
    global _startup_db_error
    from app.core.shared_breaker import mark_live_process

    mark_live_process()  # live breaker namespace (research scripts stay isolated)
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
    from app.services.scheduler_service import ensure_scheduled_task_registrations
    from app.services.workbench_service import recover_interrupted_runs
    db = SessionLocal()
    try:
        recover_interrupted_runs(db)
        recover_scheduled_task_runs(db)
    finally:
        db.close()
    # 调度任务注册接线（2026-08-02 修复）：此前 4+1 个关键任务的注册函数零调用点，
    # 新部署依赖人工建行；此处统一幂等注册（盘后流水线/策略曝光/锁利/安全网）。
    ensure_scheduled_task_registrations()
    # 日志清理（2026-08-02 修复）：run_*.log 与脚本日志此前无轮转（数百个文件），
    # 启动时清理过期运行日志，避免磁盘无限增长。
    try:
        from app.core.config import get_settings

        _cleanup_logs(str(get_settings().log_dir))
    except Exception:  # noqa: BLE001 - 日志清理失败不阻塞启动
        logger.exception("日志清理失败")
    _warmup_initial_state()
    _warmup_data_health_cache()
    _warmup_pool_cache(delay_seconds=2.0)
    _warmup_horizon_realized(delay_seconds=5.0)


def _warmup_horizon_realized(delay_seconds: float = 0.0) -> None:
    """后台预热中长线已平仓结算缓存（midterm_service._REALIZED_FINAL），首开复盘/诊断页免等约 2 秒。"""
    import threading
    import time as _time

    def _warm():
        _time.sleep(delay_seconds)
        from app.services.midterm_service import warm_realized_cache
        started_at = perf_counter()
        try:
            warm_realized_cache()
            logger.info("中长线结算缓存预热完成 (%.0fms)", (perf_counter() - started_at) * 1000)
        except Exception:
            logger.exception("中长线结算缓存预热失败")

    threading.Thread(target=_warm, name="horizon-realized-warmup", daemon=True).start()


def _cleanup_logs(log_dir: str) -> None:
    """清理过期运行日志（run_*.log 14 天、脚本日志 30 天）。"""
    import os
    import re
    import time
    from pathlib import Path

    root = Path(log_dir)
    if not root.is_dir():
        return
    now = time.time()
    run_re = re.compile(r"^run_.*\.log$")
    script_re = re.compile(r"^[a-z_]+\.log$")
    kept = {"app.log", "access.log", "backend-local.log"}
    for path in root.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if run_re.match(name):
            keep_days = 14.0
        elif script_re.match(name) and name not in kept:
            keep_days = 30.0
        else:
            continue
        age_days = (now - path.stat().st_mtime) / 86400.0
        if age_days > keep_days:
            try:
                os.remove(path)
            except OSError:
                logger.debug("清理日志失败: %s", path)


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
            # secure=True（2026-08-02 修复）：localhost/127.0.0.1 属浏览器
            # trustworthy origin（secure context），HTTP 下 Secure cookie 仍可
            # 正常工作，同时杜绝经 HTTP 明文链路被窃取的风险。
            secure=True,
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
app.include_router(benchmarks.router)
app.include_router(metrics.router)
app.include_router(outcomes.router)
app.include_router(admin.router)
app.include_router(llm.router)
from app.api.text_events import router as text_events_router
app.include_router(text_events_router)
app.include_router(frontend_logs.router)
app.include_router(midterm.router)
from app.api.stock_matrix import router as stock_matrix_router
app.include_router(stock_matrix_router)

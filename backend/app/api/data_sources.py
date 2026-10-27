import logging
import time
import threading
from datetime import date

from fastapi import Depends, HTTPException
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.data_sources import get_local_pool_status, list_data_sources, set_data_source_enabled
from app.services.data_gap_remediation_service import (
    remediate_recommendation_run_auxiliary_data,
    sync_local_pool_kline_gaps,
)
from app.services.workbench_service import invalidate_bootstrap_cache
from app.services.auxiliary.application import run_auxiliary_application
from app.services.market_pool import refresh_quote_pool
from app.services import data_source_trace_service as trace_service


router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])
logger = logging.getLogger(__name__)

# ── 本地池状态缓存（TTL 30 秒，避免前端轮询重复计算）──
# 缓存 key 不含分页参数：heavy computation 共享，pagination 按需裁剪
_pool_status_cache: dict = {}
_pool_status_lock = threading.Lock()
# single-flight：同一 key 的缓存未命中只允许一个线程重算，其余等待后直接命中缓存。
_pool_compute_locks: dict[str, threading.Lock] = {}
_POOL_CACHE_KEY = "__pool__"  # 单一 key，不按分页拆分


# ── 统一数据源 fallback trace（R-SOURCE-3/4）。静态段须先于 /{source_type}/... 注册。──

@router.get("/traces")
def list_traces(
    operation_name: str | None = None,
    provider: str | None = None,
    final_outcome: str | None = None,
    failure_category: str | None = None,
    correlation_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """统一数据源请求 trace 查询（含 fallback attempt 链，安全摘要不含 credential）。"""
    return trace_service.list_traces(
        db,
        operation_name=operation_name,
        provider=provider,
        final_outcome=final_outcome,
        failure_category=failure_category,
        correlation_id=correlation_id,
        limit=limit,
    )


@router.get("/traces/fallback-chains")
def fallback_chains(limit: int = 20, db: Session = Depends(get_db)):
    """最近发生过 fallback 的请求链（promax→quantdata→... / tushare→baostock）。"""
    return trace_service.recent_fallback_chains(db, limit=limit)


@router.get("/traces/health")
def trace_health(hours: int = 24, db: Session = Depends(get_db)):
    """从统一 trace 聚合 success/fallback/valid-empty/breaker-skip/p95。"""
    return trace_service.health_aggregation(db, hours=hours)


@router.get("")
def list_sources(db: Session = Depends(get_db)):
    try:
        return list_data_sources(db)
    except Exception as exc:  # noqa: BLE001 - surface unready state without breaking the page
        logger.exception("list data sources failed")
        return [
            {
                "source": "system",
                "source_type": "quote",
                "enabled": False,
                "priority": 999,
                "note": "数据源配置未就绪",
                "success_count": 0,
                "failure_count": 1,
                "empty_count": 0,
                "success_rate": None,
                "latency_ms": None,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error": str(exc),
                "status": "degraded",
                "updated_at": None,
            }
        ]


@router.get("/pool")
def local_pool(
    db: Session = Depends(get_db),
    u_limit: int = 50,
    u_offset: int = 0,
    b_limit: int = 50,
    b_offset: int = 0,
    cache_ttl: int = 300,
    include_details: bool = True,
):
    include_paginated = include_details
    return get_cached_local_pool_status(
        db,
        u_limit=u_limit,
        u_offset=u_offset,
        b_limit=b_limit,
        b_offset=b_offset,
        cache_ttl=cache_ttl,
        include_paginated=include_paginated,
        include_details=include_details,
    )


def get_cached_local_pool_status(
    db: Session,
    *,
    u_limit: int = 50,
    u_offset: int = 0,
    b_limit: int = 50,
    b_offset: int = 0,
    cache_ttl: int = 300,
    include_paginated: bool = True,
    include_details: bool = True,
    cache_only: bool = False,
) -> dict:
    kwargs = dict(
        u_limit=u_limit, u_offset=u_offset, b_limit=b_limit, b_offset=b_offset,
        cache_ttl=cache_ttl, include_paginated=include_paginated,
        include_details=include_details, cache_only=cache_only,
    )
    cache_key = _pool_cache_key(include_details)
    with _pool_status_lock:
        cached = _pool_status_cache.get(cache_key)
        fresh = bool(cached and (time.monotonic() - cached["ts"]) < cache_ttl)
        compute_lock = _pool_compute_locks.setdefault(cache_key, threading.Lock())
    if fresh or cache_only:
        return _get_cached_local_pool_status_impl(db, **kwargs)
    # stale-while-revalidate：缓存过期但不超过 _POOL_STALE_MAX_SECONDS 时先返回旧值（cache_stale=True），
    # 后台单飞刷新。全量重算要 10 秒+（daily_kbars/capital_flow/stock_quotes 的全表聚合），不该压在请求线程上。
    if cached and (time.monotonic() - cached["ts"]) < _POOL_STALE_MAX_SECONDS:
        _pool_background_refresh(include_details, compute_lock, kwargs)
        return _get_cached_local_pool_status_impl(db, **{**kwargs, "cache_only": True})
    with compute_lock:
        return _get_cached_local_pool_status_impl(db, **kwargs)


_POOL_STALE_MAX_SECONDS = 3600.0


def _pool_background_refresh(include_details: bool, compute_lock, kwargs: dict) -> None:
    if not compute_lock.acquire(blocking=False):
        return  # 已有刷新在跑

    def _run() -> None:
        from app.db.session import SessionLocal

        session = SessionLocal()
        try:
            _get_cached_local_pool_status_impl(
                session, **{**kwargs, "cache_ttl": 0, "cache_only": False, "include_paginated": False}
            )
        except Exception:  # noqa: BLE001 - 后台刷新失败保留旧缓存，下次请求重试
            logger.warning("pool status background refresh failed", exc_info=True)
        finally:
            session.close()
            compute_lock.release()

    threading.Thread(target=_run, name="pool-status-refresh", daemon=True).start()


def _get_cached_local_pool_status_impl(
    db: Session,
    *,
    u_limit: int = 50,
    u_offset: int = 0,
    b_limit: int = 50,
    b_offset: int = 0,
    cache_ttl: int = 300,
    include_paginated: bool = True,
    include_details: bool = True,
    cache_only: bool = False,
) -> dict:
    now = time.monotonic()
    cache_key = _pool_cache_key(include_details)
    with _pool_status_lock:
        cached = _pool_status_cache.get(cache_key)
        if cached and ((now - cached["ts"]) < cache_ttl or cache_only):
            data = dict(cached["data"])
            if include_paginated:
                data["unified_quotes"], data["daily_bars"] = _load_paginated(
                    db, u_limit, u_offset, b_limit, b_offset, cached.get("_all_quote_rows")
                )
            else:
                data["unified_quotes"] = []
                data["daily_bars"] = []
                data["pagination_preview"] = {
                    "enabled": False,
                    "requested_unified_limit": int(u_limit or 0),
                    "requested_bars_limit": int(b_limit or 0),
                    "note": "summary-only cache hit; pagination limits are preview hints only and do not cap pool refresh or gap remediation.",
                }
            data["cache_stale"] = bool((now - cached["ts"]) >= cache_ttl)
            return data

    if cache_only:
        return _unready_pool_status("数据池缓存尚未预热")

    try:
        data = get_local_pool_status(
            db,
            unified_limit=u_limit,
            unified_offset=u_offset,
            bars_limit=b_limit,
            bars_offset=b_offset,
            include_details=include_details,
        )
        with _pool_status_lock:
            _pool_status_cache[cache_key] = {"ts": time.monotonic(), "data": data}
        if not include_paginated:
            data = dict(data)
            data["unified_quotes"] = []
            data["daily_bars"] = []
            data["pagination_preview"] = {
                "enabled": False,
                "requested_unified_limit": int(u_limit or 0),
                "requested_bars_limit": int(b_limit or 0),
                "note": "summary-only request; pagination limits are preview hints only and do not cap pool refresh or gap remediation.",
            }
        return data
    except Exception as exc:
        logger.exception("local pool status failed")
        return _unready_pool_status(str(exc))


def _load_paginated(db, u_limit, u_offset, b_limit, b_offset, _cached_rows=None):
    from app.services.data_sources import _daily_bar_rows, _unified_quote_rows, _unified_quote_rows_from_rows
    if _cached_rows is not None:
        unified = _unified_quote_rows_from_rows(_cached_rows, limit=u_limit, offset=u_offset, db=db)
    else:
        unified = _unified_quote_rows(db, limit=u_limit, offset=u_offset)
    bars = _daily_bar_rows(db, limit=b_limit, offset=b_offset)
    return unified, bars


def _pool_cache_key(include_details: bool) -> str:
    return f"{_POOL_CACHE_KEY}:{'full' if include_details else 'summary'}"


def invalidate_pool_status_cache() -> None:
    with _pool_status_lock:
        _pool_status_cache.clear()
    from app.services.data_sources import clear_daily_bar_rows_cache

    clear_daily_bar_rows_cache()
    invalidate_bootstrap_cache()


from app.services.cache_hooks import register as _register_cache_hook  # noqa: E402

_register_cache_hook("pool_status", invalidate_pool_status_cache)


@router.post("/pool/refresh")
def refresh_pool(db: Session = Depends(get_db)):
    quotes, meta = refresh_quote_pool(db)
    invalidate_pool_status_cache()
    return {"refreshed": len(quotes), "meta": meta}


@router.post("/pool/sync-gaps")
def sync_pool_gaps(payload: dict | None = None, db: Session = Depends(get_db)):
    payload = payload or {}
    result = sync_local_pool_kline_gaps(
        db,
        code_limit=int(payload.get("code_limit", 0)),
        days=int(payload.get("days", 90)),
        min_bars=int(payload.get("min_bars", 30)),
        dry_run=bool(payload.get("dry_run", False)),
    )
    if not bool(payload.get("dry_run", False)):
        invalidate_pool_status_cache()
    return result


@router.post("/recommendation-runs/{run_id}/sync-auxiliary")
def sync_recommendation_run_auxiliary(run_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    payload = payload or {}
    result = remediate_recommendation_run_auxiliary_data(
        db,
        run_id,
        target_tables=payload.get("target_tables"),
        code_limit=int(payload.get("code_limit", 0)),
        dry_run=bool(payload.get("dry_run", False)),
    )
    if not bool(payload.get("dry_run", False)):
        invalidate_bootstrap_cache()
    return result


def _unready_pool_status(message: str) -> dict:
    return {
        "summary": {
            "raw_quote_count": 0,
            "raw_bar_count": 0,
            "unified_quote_count": 0,
            "active_unified_quote_count": 0,
            "eligible_quote_count": 0,
            "candidate_filter": {
                "total_count": 0,
                "base_eligible_count": 0,
                "excluded_count": 0,
                "high_liquidity_count": 0,
                "low_liquidity_count": 0,
                "exclusion_counts": {},
                "high_liquidity_threshold": {"amount_yi": 0.5, "turnover_rate": 0.1},
            },
            "daily_bar_count": 0,
            "full_market_daily_bar_count": 0,
            "active_daily_bar_count": 0,
            "eligible_daily_bar_count": 0,
            "daily_bar_coverage_rate": 0.0,
            "eligible_daily_bar_coverage_rate": 0.0,
            "daily_bar_missing_count": 0,
            "daily_bar_stale_count": 0,
            "daily_bar_short_history_count": 0,
            "daily_bar_immature_history_count": 0,
            "daily_bar_total_short_history_count": 0,
            "actual_recommend_scan_count": 0,
            "skill_input_count": 0,
            "daily_bar_row_count": 0,
            "daily_indicator_count": 0,
            "indicator_coverage_pct": 0.0,
            "latest_quote_date": None,
            "latest_bar_date": None,
            "coverage_trade_date": None,
            "latest_unified_at": None,
            "limit_up_board_count": 0,
            "concept_sector_count": 0,
            "capital_flow_count": 0,
            "dragon_tiger_count": 0,
            "news_count": 0,
        },
        "quote_sources": [],
        "kline_sources": [],
        "auxiliary_sources": [],
        "recommendation_readiness": {
            "status": "blocked",
            "can_run_local_recommendation": False,
            "has_missing_data": True,
            "full_market_mode": True,
            "required_candidate_count": 0,
            "quote": {
                "available_count": 0,
                "candidate_count": 0,
                "base_filter_eligible_count": 0,
                "unified_quote_total": 0,
                "filter_summary": {
                    "total_count": 0,
                    "base_eligible_count": 0,
                    "excluded_count": 0,
                    "high_liquidity_count": 0,
                    "low_liquidity_count": 0,
                    "exclusion_counts": {},
                    "high_liquidity_threshold": {"amount_yi": 0.5, "turnover_rate": 0.1},
                },
                "latest_trade_date": None,
                "active_sources": [],
            },
            "kline": {
                "checked_count": 0,
                "ready_count": 0,
                "missing_count": 0,
                "short_history_count": 0,
                "immature_history_count": 0,
                "stale_count": 0,
                "min_bars": 30,
                "coverage_count": 0,
                "coverage_rate": 0.0,
                "latest_trade_date": None,
                "examples": [],
            },
            "scan": {
                "configured_candidate_count": 0,
                "lookahead_multiplier": 0,
                "lookahead_count": 0,
                "actual_scan_count": 0,
                "skill_input_count": 0,
                "demo_limited": False,
            },
            "messages": [f"本地数据池未就绪：{message}"],
        },
        "unified_quotes": [],
        "unified_quotes_total": 0,
        "daily_bars": [],
        "daily_bars_total": 0,
    }


@router.post("/{source_type}/{source}/enabled")
def set_enabled(source_type: str, source: str, payload: dict, db: Session = Depends(get_db)):
    valid_types = {"quote", "kline", "limit_up", "concept_sector", "capital_flow", "dragon_tiger", "news"}
    if source_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"source_type must be one of {valid_types}")
    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="enabled is required")
    result = set_data_source_enabled(
        db,
        source=source,
        source_type=source_type,
        enabled=bool(payload.get("enabled")),
        note=payload.get("note"),
    )
    if result is None:
        raise HTTPException(status_code=404, detail="data source not found")
    with _pool_status_lock:
        _pool_status_cache.clear()
    from app.services.data_sources import clear_daily_bar_rows_cache

    clear_daily_bar_rows_cache()
    invalidate_bootstrap_cache()
    return result


@router.post("/pool/refresh/limit-up")
def refresh_limit_up(db: Session = Depends(get_db)):
    result = run_auxiliary_application(db, instruments=(), trade_date=date.today(), refresh=True, datasets=("limit_up_board",))
    with _pool_status_lock:
        _pool_status_cache.clear()
    from app.services.data_sources import clear_daily_bar_rows_cache

    clear_daily_bar_rows_cache()
    invalidate_bootstrap_cache()
    item = result.orchestrated.result_for("limit_up_board")
    return {"refreshed": item.coverage.record_count, "audit": result.audit_payload["limit_up_board"]}


@router.post("/pool/refresh/concept-sectors")
def refresh_sectors(db: Session = Depends(get_db)):
    result = run_auxiliary_application(db, instruments=(), trade_date=date.today(), refresh=True, datasets=("concept_sector", "concept_sector_stock"))
    with _pool_status_lock:
        _pool_status_cache.clear()
    from app.services.data_sources import clear_daily_bar_rows_cache

    clear_daily_bar_rows_cache()
    invalidate_bootstrap_cache()
    item = result.orchestrated.result_for("concept_sector")
    return {"refreshed": item.coverage.record_count, "audit": result.audit_payload["concept_sector"]}


@router.post("/pool/refresh/dragon-tiger")
def refresh_dt(db: Session = Depends(get_db)):
    result = run_auxiliary_application(db, instruments=(), trade_date=date.today(), refresh=True, datasets=("dragon_tiger",))
    with _pool_status_lock:
        _pool_status_cache.clear()
    from app.services.data_sources import clear_daily_bar_rows_cache

    clear_daily_bar_rows_cache()
    invalidate_bootstrap_cache()
    item = result.orchestrated.result_for("dragon_tiger")
    return {"refreshed": item.coverage.record_count, "audit": result.audit_payload["dragon_tiger"]}

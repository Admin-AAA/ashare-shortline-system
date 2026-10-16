from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException
from app.api.contracts import ContractRouter as APIRouter
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.db.session import get_db
from app.engine.trading_calendar import normalize_trade_date as _normalize_trade_date
from app.models.tables import MarketSnapshot, RecommendationRun, StockQuote
from app.schemas.dashboard import DashboardOverview
from app.services.cost_service import list_scheduled_tasks
from app.services.dashboard_hardening import project_dashboard_hardening
from app.services.evidence.selectors import (
    historical_reconstruction_provenance_conditions,
    live_forward_run_conditions,
)
from app.services.dashboard_quality_service import build_dashboard_quality_scorecard
from app.services.evidence.validation import (
    canonical_historical_reconstruction_run_ids,
    canonical_live_forward_run_ids,
)
from app.services.observability import AlertRegistry
from app.services.data_pool.health_projection import summarize_alerts, summarize_tasks
from app.services.data_pool.readiness import summarize_pool_status
from app.services.recommendations.projection import (
    build_cockpit_recommendation,
    build_market_temperature,
    build_risk_radar,
    build_run_progress,
)
from app.services.runs.progress import progress_pct
from app.services.runs.recovery import evaluate_recovery_capability
from app.services.workbench_service import list_dashboard_recommendations, list_run_recommendations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# S8: overview owns no response cache.  It reuses the authoritative local-pool
# cache below; frontend request coalescing lives in the existing QueryClient.


def _canonical_completed_run_conditions(
    db: Session,
    canonical_run_ids: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
) -> tuple:
    """Completed dashboard evidence with a validated persisted identity hash."""
    live_run_ids, historical_run_ids = (
        canonical_run_ids or _canonical_dashboard_run_ids(db)
    )
    live_branch = and_(
        *live_forward_run_conditions(),
        RecommendationRun.run_id.in_(live_run_ids),
    )
    historical_branch = and_(
        *historical_reconstruction_provenance_conditions(),
        RecommendationRun.data_as_of <= RecommendationRun.trade_date,
        RecommendationRun.run_id.in_(historical_run_ids),
    )
    return (
        RecommendationRun.run_mode == "formal",
        RecommendationRun.status == "completed",
        or_(live_branch, historical_branch),
    )


def _canonical_dashboard_run_ids(
    db: Session,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        canonical_live_forward_run_ids(db),
        canonical_historical_reconstruction_run_ids(db),
    )


DASHBOARD_STALE_THRESHOLD_SECONDS = 300
DASHBOARD_EVIDENCE_LIMIT = 100
_DASHBOARD_EVIDENCE_COLUMNS = (
    RecommendationRun.run_id,
    RecommendationRun.evidence_class,
    RecommendationRun.actual_generated_at,
    RecommendationRun.trade_date,
    RecommendationRun.data_as_of,
    RecommendationRun.source_snapshot_id,
    RecommendationRun.evidence_identity_hash,
    RecommendationRun.strategy_policy_fingerprint,
    RecommendationRun.execution_policy_fingerprint,
    RecommendationRun.code_revision,
    RecommendationRun.reconstruction_batch_id,
    RecommendationRun.provenance_schema_version,
    RecommendationRun.result_summary_json,
    RecommendationRun.heartbeat_at,
    RecommendationRun.finished_at,
    RecommendationRun.started_at,
    RecommendationRun.status,
)
RUN_ACTIVE_STATUSES = {"queued", "running"}
RUN_HEARTBEAT_STALE_SECONDS = 1200
DASHBOARD_POOL_PREVIEW_LIMIT = 0
DASHBOARD_POOL_SCOPE_NOTE = (
    "驾驶舱概览使用汇总模式打开，不拉取分页明细；缺口样例只用于定位问题。"
    "刷新本地数据池会全量检查候选池，并补齐可处理的日K缺口。"
)


def compose_overview(
    *,
    market_snapshot: dict[str, Any],
    recent_run: dict[str, Any] | None,
    recommendations: list[Any],
    data_health: dict[str, Any],
    task_health: dict[str, Any],
    validation: dict[str, Any],
    quality_scorecard: dict[str, Any],
    hardening: dict[str, Any] | None = None,
) -> DashboardOverview:
    cards = [
        item if hasattr(item, "risk_level") else build_cockpit_recommendation(item)
        for item in recommendations
    ]
    return DashboardOverview(
        market=build_market_temperature(market_snapshot),
        recommendations=cards,
        risk_radar=build_risk_radar(cards),
        current_run=build_run_progress(recent_run),
        data_health=data_health,
        task_health=task_health,
        validation=validation,
        quality_scorecard=quality_scorecard,
        hardening=hardening or {},
    )


def serialize_market_snapshot_row(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "trade_date": str(getattr(row, "trade_date", "") or ""),
        "sample_count": int(getattr(row, "sample_count", 0) or 0),
        "up_count": int(getattr(row, "up_count", 0) or 0),
        "down_count": int(getattr(row, "down_count", 0) or 0),
        "limit_up_count": int(getattr(row, "limit_up_count", 0) or 0),
        "limit_down_count": int(getattr(row, "limit_down_count", 0) or 0),
        "main_industries": _loads(getattr(row, "main_industries_json", "[]"), []),
        "breadth": _loads(getattr(row, "breadth_json", "{}"), {}),
    }


def _select_dashboard_market_snapshot(db: Session) -> dict[str, Any]:
    row = db.scalar(select(MarketSnapshot).order_by(MarketSnapshot.created_at.desc()).limit(1))
    persisted_snapshot = serialize_market_snapshot_row(row)
    latest_quote_trade_date = _latest_stock_quote_trade_date(db)
    persisted_trade_date = _normalize_trade_date(persisted_snapshot.get("trade_date"))
    if latest_quote_trade_date and latest_quote_trade_date > persisted_trade_date:
        try:
            from app.services.market_pool import get_market_snapshot_from_pool

            return get_market_snapshot_from_pool(db, refresh=False)
        except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
            logger.debug("_select_dashboard_market_snapshot: best-effort fallback after %r", _ble_exc)
            return persisted_snapshot
    return persisted_snapshot


def serialize_run_progress_row(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    heartbeat_at = getattr(row, "heartbeat_at", None)
    heartbeat_age_seconds = None
    if heartbeat_at:
        heartbeat_age_seconds = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - heartbeat_at).total_seconds()))
    status = str(getattr(row, "status", "idle") or "idle")
    return {
        "run_id": getattr(row, "run_id", None),
        "status": status,
        "progress_phase": getattr(row, "progress_phase", None),
        "checkpoint_phase": getattr(row, "checkpoint_phase", None),
        "progress_pct": progress_pct(getattr(row, "progress_current", 0), getattr(row, "progress_total", 0)),
        "progress_message": getattr(row, "progress_message", None) or "",
        "error_message": getattr(row, "error_message", None),
        "is_stale": (
            status in RUN_ACTIVE_STATUSES
            and heartbeat_age_seconds is not None
            and heartbeat_age_seconds > RUN_HEARTBEAT_STALE_SECONDS
        ),
    }


def build_validation_summary(
    pool_status: dict[str, Any],
    *,
    quality_scorecard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness = pool_status.get("recommendation_readiness") if isinstance(pool_status.get("recommendation_readiness"), dict) else {}
    status = str(readiness.get("status") or "unknown")
    messages = [str(item) for item in readiness.get("messages") or []]
    alerts = summarize_alerts(
        [{"severity": "medium", "message": message} for message in messages]
    )
    validation_dimension = (
        ((quality_scorecard or {}).get("dimensions") or {}).get("validation")
        if isinstance((quality_scorecard or {}).get("dimensions"), dict)
        else {}
    )
    execution_dimension = (
        ((quality_scorecard or {}).get("dimensions") or {}).get("execution")
        if isinstance((quality_scorecard or {}).get("dimensions"), dict)
        else {}
    )
    scorecard_highlights = [str(item) for item in ((quality_scorecard or {}).get("highlights") or [])][:4]
    return {
        "last_status": status,
        "alerts": alerts,
        "quality_gate": {
            "overall_score": (quality_scorecard or {}).get("overall_score"),
            "overall_status": (quality_scorecard or {}).get("overall_status"),
            "validation_score": validation_dimension.get("score") if isinstance(validation_dimension, dict) else None,
            "validation_status": validation_dimension.get("status") if isinstance(validation_dimension, dict) else None,
            "execution_score": execution_dimension.get("score") if isinstance(execution_dimension, dict) else None,
            "execution_status": execution_dimension.get("status") if isinstance(execution_dimension, dict) else None,
            "highlights": scorecard_highlights,
        },
    }


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("_loads: best-effort fallback after %r", _ble_exc)
        return default


def _latest_stock_quote_trade_date(db: Session) -> str:
    latest = db.scalar(
        select(func.max(func.replace(StockQuote.trade_date, "-", ""))).where(
            StockQuote.trade_date.is_not(None),
            StockQuote.freshness_status != "inactive",
        )
    )
    return _normalize_trade_date(latest)


def _build_overview_payload(db: Session) -> DashboardOverview:
    """组合概览响应；不持有第二份响应缓存。"""
    market_snapshot = _select_dashboard_market_snapshot(db)
    recent_run_row = _select_dashboard_run(db)
    recent_run = serialize_run_progress_row(recent_run_row)
    if recent_run_row is not None and recent_run is not None:
        recovery = evaluate_recovery_capability(
            db,
            str(recent_run_row.run_id),
            now=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        recent_run["can_resume"] = recovery.can_resume
    recommendation_run_row = _select_dashboard_recommendation_run(db)
    run_recommendations = []
    if recommendation_run_row is not None:
        if recommendation_run_row.run_mode == "formal":
            run_recommendations = list_dashboard_recommendations(
                db,
                str(recommendation_run_row.trade_date),
            )
        else:
            # 保留没有正式运行时的历史兜底语义。
            run_recommendations = list_run_recommendations(db, str(recommendation_run_row.run_id))[:12]

    pool_status = load_dashboard_pool_status(db)
    data_health = summarize_pool_status(pool_status)
    task_health = summarize_tasks(list_scheduled_tasks(db))
    quality_scorecard = build_dashboard_quality_scorecard(
        db,
        data_health=data_health,
        task_health=task_health,
        current_run=recent_run,
    )
    hardening = _build_dashboard_hardening(
        db,
        market_snapshot=market_snapshot,
        data_health=data_health,
        task_health=task_health,
        recent_run=recent_run,
    )
    return compose_overview(
        market_snapshot=market_snapshot,
        recent_run=recent_run,
        recommendations=run_recommendations,
        data_health=data_health,
        task_health=task_health,
        validation=build_validation_summary(pool_status, quality_scorecard=quality_scorecard),
        quality_scorecard=quality_scorecard,
        hardening=hardening,
    )


def _build_dashboard_hardening(
    db: Session,
    *,
    market_snapshot: dict[str, Any],
    data_health: dict[str, Any],
    task_health: dict[str, Any],
    recent_run: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the canonical dashboard evidence projection."""
    invalid_live_claim = False
    try:
        canonical_run_ids = _canonical_dashboard_run_ids(db)
        canonical_live_run_ids, _ = canonical_run_ids
        rows = db.scalars(
            select(RecommendationRun)
            .where(
                *_canonical_completed_run_conditions(
                    db,
                    canonical_run_ids=canonical_run_ids,
                )
            )
            .options(load_only(*_DASHBOARD_EVIDENCE_COLUMNS))
            .order_by(
                case(
                    (RecommendationRun.evidence_class == "live_forward", 0),
                    (RecommendationRun.evidence_class == "historical_reconstruction", 1),
                    else_=2,
                ),
                RecommendationRun.trade_date.desc(),
                RecommendationRun.actual_generated_at.desc(),
                RecommendationRun.started_at.desc(),
            )
            .limit(DASHBOARD_EVIDENCE_LIMIT)
        ).all()
        projection = project_dashboard_hardening(
            rows,
            now=datetime.now(timezone.utc),
            current_business_date=str(market_snapshot.get("trade_date") or ""),
            stale_threshold=timedelta(seconds=DASHBOARD_STALE_THRESHOLD_SECONDS),
            fallback_data_quality=str(data_health.get("status") or "unknown"),
            fallback_risk_gate="unknown",
        )
        recent_live_claim_ids = tuple(
            db.scalars(
                select(RecommendationRun.run_id)
                .where(
                    RecommendationRun.run_mode == "formal",
                    RecommendationRun.status == "completed",
                    RecommendationRun.evidence_class == "live_forward",
                )
                .order_by(
                    RecommendationRun.trade_date.desc(),
                    RecommendationRun.actual_generated_at.desc(),
                    RecommendationRun.started_at.desc(),
                )
                .limit(DASHBOARD_EVIDENCE_LIMIT)
            ).all()
        )
        invalid_live_claim = any(
            run_id not in canonical_live_run_ids
            for run_id in recent_live_claim_ids
        )
    except Exception:
        logger.exception("S8 canonical dashboard projection failed")
        projection = project_dashboard_hardening(
            (),
            now=datetime.now(timezone.utc),
            current_business_date=str(market_snapshot.get("trade_date") or ""),
            stale_threshold=timedelta(seconds=DASHBOARD_STALE_THRESHOLD_SECONDS),
            fallback_data_quality=str(data_health.get("status") or "unknown"),
            fallback_risk_gate="unknown",
        )
    risk_halted = str(projection.get("risk_gate") or "").lower() in {"halt", "halted"}
    alert_registry = AlertRegistry.defaults()
    projection["observability"] = {
        **alert_registry.to_dict(),
        "active_alerts": alert_registry.evaluate(
            {
                "heartbeat_age_seconds": (
                    RUN_HEARTBEAT_STALE_SECONDS + 1
                    if (recent_run or {}).get("is_stale")
                    else 0
                ),
                "flow_failed": bool(task_health.get("failed_count"))
                or (recent_run or {}).get("status") == "failed",
                "quality_blocked": str(data_health.get("status") or "").lower()
                == "blocked",
                "identity_mismatch": invalid_live_claim,
                "risk_halted": risk_halted,
            }
        ),
    }
    return projection


@router.get("/overview", response_model=DashboardOverview)
def overview(db: Session = Depends(get_db)) -> DashboardOverview:
    try:
        return _build_overview_payload(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc



def _select_dashboard_run(db: Session) -> RecommendationRun | None:
    """Prefer active formal work, otherwise the newest canonical completed run.

    两阶段定位：先用「只选 run_id」走索引快速定位那一行（覆盖索引，避免物化
    大 JSON 列后文件排序），再按主键取整行。SELECT * 直接排序会因
    market_snapshot_json/result_summary_json 等大列触发全量物化 + filesort，
    在 316 行表上实测从 ~1135ms 降到 ~7ms。语义不变。
    """
    where = or_(
        and_(
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status.in_(RUN_ACTIVE_STATUSES),
            RecommendationRun.evaluation_scope == "live_forward",
        ),
        and_(*_canonical_completed_run_conditions(db)),
    )
    order = (
        case((RecommendationRun.status.in_(RUN_ACTIVE_STATUSES), 0), else_=1),
        case((RecommendationRun.evidence_class == "live_forward", 0), else_=1),
        RecommendationRun.trade_date.desc(),
        RecommendationRun.started_at.desc(),
    )
    run_id = db.scalar(select(RecommendationRun.run_id).where(where).order_by(*order).limit(1))
    if run_id is None:
        return None
    return db.get(RecommendationRun, run_id)


def _select_dashboard_recommendation_run(db: Session) -> RecommendationRun | None:
    latest_trade_date = _latest_dashboard_trade_date(db)
    if not latest_trade_date:
        return None
    base = (
        select(RecommendationRun)
        .where(
            *_canonical_completed_run_conditions(db),
            RecommendationRun.evidence_class == "live_forward",
            RecommendationRun.trade_date == latest_trade_date,
        )
        .order_by(
            RecommendationRun.finished_at.desc(),
            RecommendationRun.started_at.desc(),
        )
    )
    with_recommendations = db.scalar(
        base.where(RecommendationRun.recommended_count > 0).limit(1)
    )
    return with_recommendations or db.scalar(base.limit(1))


def _latest_dashboard_trade_date(db: Session) -> str | None:
    live_run_ids = canonical_live_forward_run_ids(db)
    if not live_run_ids:
        return None
    return db.scalar(
        select(RecommendationRun.trade_date)
        .where(
            RecommendationRun.run_id.in_(live_run_ids),
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status == "completed",
        )
        .order_by(RecommendationRun.trade_date.desc())
        .limit(1)
    )


def load_dashboard_pool_status(db: Session) -> dict[str, Any]:
    from app.api.data_sources import get_cached_local_pool_status

    # Dashboard overview: use cache only to avoid 800ms DB scan on every request.
    # Cache is refreshed by scheduler every 5 minutes.
    pool_status = get_cached_local_pool_status(
        db,
        u_limit=DASHBOARD_POOL_PREVIEW_LIMIT,
        u_offset=0,
        b_limit=DASHBOARD_POOL_PREVIEW_LIMIT,
        b_offset=0,
        include_paginated=False,
        include_details=False,
        cache_only=True,
    )
    if _is_unready_pool_cache(pool_status):
        # Cache miss: do full load once, then cache the result (even if empty).
        # Without this, every dashboard request re-scans the DB (775ms × N = 30s+).
        # get_cached_local_pool_status(cache_only=False) populates the cache internally,
        # so subsequent requests hit cache_only=True and return instantly.
        pool_status = get_cached_local_pool_status(
            db,
            u_limit=DASHBOARD_POOL_PREVIEW_LIMIT,
            u_offset=0,
            b_limit=DASHBOARD_POOL_PREVIEW_LIMIT,
            b_offset=0,
            include_paginated=False,
            include_details=False,
            cache_only=False,
        )
    pool_status["overview_scope_note"] = DASHBOARD_POOL_SCOPE_NOTE
    return pool_status


def _is_unready_pool_cache(pool_status: dict[str, Any]) -> bool:
    readiness = pool_status.get("recommendation_readiness")
    if not isinstance(readiness, dict):
        return True
    messages = " ".join(str(item) for item in readiness.get("messages") or [])
    summary = pool_status.get("summary") if isinstance(pool_status.get("summary"), dict) else {}
    return (
        "缓存尚未预热" in messages
        and int(summary.get("active_unified_quote_count") or 0) == 0
        and int(summary.get("eligible_quote_count") or 0) == 0
    )

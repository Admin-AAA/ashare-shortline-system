from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.tables import MarketSnapshot, RecommendationRun
from app.schemas.dashboard import DashboardOverview
from app.services.cost_service import list_scheduled_tasks
from app.services.data_pool.health_projection import summarize_alerts, summarize_tasks
from app.services.data_pool.readiness import summarize_pool_status
from app.services.recommendations.projection import (
    build_cockpit_recommendation,
    build_market_temperature,
    build_risk_radar,
    build_run_progress,
)
from app.services.runs.progress import progress_pct
from app.services.workbench_service import list_run_recommendations

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
RUN_ACTIVE_STATUSES = {"queued", "running"}
RUN_HEARTBEAT_STALE_SECONDS = 1200


def compose_overview(
    *,
    market_snapshot: dict[str, Any],
    recent_run: dict[str, Any] | None,
    recommendations: list[Any],
    data_health: dict[str, Any],
    task_health: dict[str, Any],
    validation: dict[str, Any],
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
        "is_stale": (
            status in RUN_ACTIVE_STATUSES
            and heartbeat_age_seconds is not None
            and heartbeat_age_seconds > RUN_HEARTBEAT_STALE_SECONDS
        ),
    }


def build_validation_summary(pool_status: dict[str, Any]) -> dict[str, Any]:
    readiness = pool_status.get("recommendation_readiness") if isinstance(pool_status.get("recommendation_readiness"), dict) else {}
    status = str(readiness.get("status") or "unknown")
    messages = [str(item) for item in readiness.get("messages") or []]
    alerts = summarize_alerts(
        [{"severity": "medium", "message": message} for message in messages]
    )
    return {"last_status": status, "alerts": alerts}


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


@router.get("/overview", response_model=DashboardOverview)
def overview(db: Session = Depends(get_db)) -> DashboardOverview:
    from app.api.data_sources import get_cached_local_pool_status

    try:
        market_row = db.scalar(select(MarketSnapshot).order_by(MarketSnapshot.created_at.desc()).limit(1))
        recent_run_row = db.scalar(select(RecommendationRun).order_by(RecommendationRun.started_at.desc()).limit(1))
        recent_run = serialize_run_progress_row(recent_run_row)
        run_recommendations = []
        if recent_run_row is not None:
            run_recommendations = list_run_recommendations(db, str(recent_run_row.run_id))[:12]

        pool_status = get_cached_local_pool_status(
            db,
            u_limit=20,
            u_offset=0,
            b_limit=20,
            b_offset=0,
            include_paginated=False,
            include_details=False,
            cache_only=True,
        )
        return compose_overview(
            market_snapshot=serialize_market_snapshot_row(market_row),
            recent_run=recent_run,
            recommendations=run_recommendations,
            data_health=summarize_pool_status(pool_status),
            task_health=summarize_tasks(list_scheduled_tasks(db)),
            validation=build_validation_summary(pool_status),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

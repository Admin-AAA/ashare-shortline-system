from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
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
from app.services.workbench_service import list_run_recommendations

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


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


@router.get("/overview", response_model=DashboardOverview)
def overview(db: Session = Depends(get_db)) -> DashboardOverview:
    from app.services.data_source_health import active_alerts
    from app.services.data_sources import get_local_pool_status
    from app.services.workbench_service import bootstrap_workbench_state

    try:
        home = bootstrap_workbench_state(db)
        recent_run = home.get("recent_runs", {}).get("formal") or home.get("recent_runs", {}).get("experiment")
        run_recommendations = []
        if recent_run and recent_run.get("run_id"):
            run_recommendations = list_run_recommendations(db, str(recent_run["run_id"]))[:12]

        pool_status = get_local_pool_status(
            db,
            unified_limit=20,
            unified_offset=0,
            bars_limit=20,
            bars_offset=0,
        )
        alerts = active_alerts(db)
        return compose_overview(
            market_snapshot=home.get("market_snapshot") or {},
            recent_run=recent_run,
            recommendations=run_recommendations,
            data_health=summarize_pool_status(pool_status),
            task_health=summarize_tasks(list_scheduled_tasks(db)),
            validation={"last_status": "not_run", "alerts": summarize_alerts(alerts)},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

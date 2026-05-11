from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Recommendation
from app.services.workbench_service import (
    get_recommendation_detail,
    _serialize_recommendation,
    run_workbench,
)


def run_recommendations(db: Session) -> dict[str, Any]:
    """Compatibility wrapper for older callers.

    The real implementation is the auditable workbench pipeline. It uses real
    market data only and will raise if configured data sources fail.
    """

    result = run_workbench(
        db,
        {
            "run_name": "兼容入口真实行情运行",
            "run_mode": "experiment",
            "market_scope": "all",
            "output_count": 6,
            "hide_recent_repeats": False,
            "skills": [
                {"skill_id": "daily-stock-analysis-skill", "weight": 1.0, "sort_order": 0},
            ],
        },
    )
    return {
        "run_id": result["run"]["run_id"],
        "recommended_count": result["run"]["recommended_count"],
        "cards": result["recommendations"],
    }


def list_recommendations(db: Session, limit: int = 100, trade_date: str | None = None) -> list[dict[str, Any]]:
    stmt = select(Recommendation).order_by(Recommendation.recommended_at.desc()).limit(limit)
    if trade_date:
        stmt = (
            select(Recommendation)
            .where(Recommendation.trade_date == trade_date)
            .order_by(Recommendation.final_score.desc())
            .limit(limit)
        )
    return [_serialize_recommendation(db, row) for row in db.scalars(stmt).all()]


__all__ = ["get_recommendation_detail", "list_recommendations", "run_recommendations"]

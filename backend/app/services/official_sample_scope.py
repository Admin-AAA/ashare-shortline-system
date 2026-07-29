"""Canonical eligibility rules for user-facing live performance samples.

Callers remain responsible for their projection and date/filter clauses.  This
module owns the facts that make a recommendation eligible to be described as a
completed, forward-generated formal sample.
"""
from __future__ import annotations

from sqlalchemy import func, text

from app.models.tables import Recommendation, RecommendationRun


OFFICIAL_RECOMMENDATION_PREFIX = "rec_"
LIVE_FORWARD_SCOPE = "live_forward"


def evaluation_scope_for_run(
    *,
    run_mode: str,
    trade_date_override: str | None,
    matrix_run: bool = False,
) -> str:
    """Classify a newly requested run without inferring from its display name."""
    normalized_mode = str(run_mode or "formal").lower()
    if normalized_mode == "experiment":
        return "experiment"
    if normalized_mode == "backtest":
        return "backtest"
    if trade_date_override or matrix_run:
        return "historical_replay"
    return LIVE_FORWARD_SCOPE


def official_sample_conditions() -> tuple:
    """Return the complete eligibility contract for official live metrics.

    ``recommended_at`` is stored as naive UTC.  A historical replay commonly
    reuses an earlier ``trade_date`` while being generated days later.  Such a
    row is useful for research, but it is not forward evidence and must not
    enter a user-facing live win rate.
    """
    beijing_generation_date = func.date(
        func.date_add(Recommendation.recommended_at, text("INTERVAL 8 HOUR"))
    )
    return (
        RecommendationRun.run_mode == "formal",
        RecommendationRun.evaluation_scope == LIVE_FORWARD_SCOPE,
        RecommendationRun.status == "completed",
        Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
        beijing_generation_date == Recommendation.trade_date,
    )


__all__ = [
    "LIVE_FORWARD_SCOPE",
    "OFFICIAL_RECOMMENDATION_PREFIX",
    "evaluation_scope_for_run",
    "official_sample_conditions",
]

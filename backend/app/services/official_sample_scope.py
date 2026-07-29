"""Canonical eligibility rules for user-facing live performance samples.

Callers remain responsible for their projection and date/filter clauses.  This
module owns the facts that make a recommendation eligible to be described as a
completed, forward-generated formal sample.
"""
from __future__ import annotations

from sqlalchemy import func, text

from app.models.tables import Recommendation, RecommendationRun


OFFICIAL_RECOMMENDATION_PREFIX = "rec_"


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
        RecommendationRun.status == "completed",
        Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
        beijing_generation_date == Recommendation.trade_date,
    )


__all__ = ["OFFICIAL_RECOMMENDATION_PREFIX", "official_sample_conditions"]

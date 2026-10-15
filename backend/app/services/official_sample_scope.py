"""Canonical eligibility rules for user-facing recommendation samples.

Callers remain responsible for their projection and date/filter clauses.  This
module owns the facts that make a recommendation eligible to be described as a
completed forward sample or an explicitly authorized historical reconstruction.

The historical-reconstruction branch is not defined here: its single source of
truth is ``app.services.evidence.selectors``, re-exported below so existing
callers keep working without a second copy of the expression.
"""
from __future__ import annotations

from sqlalchemy import and_, false, or_

from app.models.tables import Recommendation, RecommendationRun
from app.services.evidence.selectors import (
    OFFICIAL_RECOMMENDATION_PREFIX,
    historical_reconstruction_run_conditions,
    live_forward_run_conditions,
)


LIVE_FORWARD_SCOPE = "live_forward"
HISTORICAL_REPLAY_SCOPE = "historical_replay"
METRICS_SAMPLE_SCOPES = (LIVE_FORWARD_SCOPE, HISTORICAL_REPLAY_SCOPE)


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
    """Canonical LIVE_FORWARD eligibility for current demo metrics."""
    return (
        RecommendationRun.run_mode == "formal",
        RecommendationRun.status == "completed",
        *live_forward_run_conditions(),
        Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
    )


def historical_reconstruction_sample_conditions() -> tuple:
    """Backward-compatible alias; the canonical definition lives in selectors.

    Kept so existing imports of this name resolve to the same expression owned
    by ``app.services.evidence.selectors``.
    """
    return historical_reconstruction_run_conditions()


def recommendation_history_conditions(
    *,
    live_run_ids: tuple[str, ...] | list[str] = (),
    historical_run_ids: tuple[str, ...] | list[str] = (),
    catchup_run_ids: tuple[str, ...] | list[str] = (),
) -> tuple:
    """Return only explicitly validated LIVE or reconstruction run rows.

    ``catchup_run_ids`` (display-only) admits next-morning catch-up formal runs.
    """

    live_branch = false()
    if live_run_ids:
        live_branch = and_(
            *official_sample_conditions(),
            RecommendationRun.run_id.in_(tuple(live_run_ids)),
        )
    historical_branch = false()
    if historical_run_ids:
        historical_branch = and_(
            *historical_reconstruction_run_conditions(),
            RecommendationRun.run_id.in_(tuple(historical_run_ids)),
        )
    catchup_branch = false()
    if catchup_run_ids:
        catchup_branch = and_(
            RecommendationRun.run_mode == "formal",
            RecommendationRun.status == "completed",
            Recommendation.recommendation_id.like(f"{OFFICIAL_RECOMMENDATION_PREFIX}%"),
            RecommendationRun.run_id.in_(tuple(catchup_run_ids)),
        )
    return (or_(live_branch, historical_branch, catchup_branch),)


__all__ = [
    "HISTORICAL_REPLAY_SCOPE",
    "LIVE_FORWARD_SCOPE",
    "METRICS_SAMPLE_SCOPES",
    "OFFICIAL_RECOMMENDATION_PREFIX",
    "evaluation_scope_for_run",
    "historical_reconstruction_sample_conditions",
    "official_sample_conditions",
    "recommendation_history_conditions",
]

"""Historical_Query: causal, evidence-class-preserving read model.

The Historical_Query returns recommendation history rows (run + recommendation)
with their persisted ``evidence_class`` intact, selected by signal date
(``recommendation_runs.trade_date``) or a business-date range, and ordered by
business date then ``final_score`` (Requirement 2.7).

As-of causality is enforced at read time (Requirement 2.8): any row whose run
claims complete provenance — through the canonical LIVE predicate or the
reconstruction provenance core — must satisfy ``data_as_of <= trade_date``.
Violating rows are excluded by the query itself and are always counted in the
result so the exclusion is never silent.

This module only constructs and runs read-only SELECT statements; it never
writes to the database.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.models.tables import (
    Recommendation,
    RecommendationEvidenceClassificationAudit,
    RecommendationOutcomeVersion,
    RecommendationRun,
)
from app.services.evidence.selectors import (
    historical_reconstruction_provenance_conditions,
    live_forward_provenance_conditions,
)


HistoricalDateAxis = Literal["signal_date", "trade_date"]


@dataclass(frozen=True, slots=True)
class HistoricalRecord:
    """One run+recommendation history row with its preserved evidence class."""

    run: Any
    recommendation: Any
    evidence_class: str | None


@dataclass(frozen=True, slots=True)
class HistoricalQueryResult:
    """History rows plus the count of rows excluded by as-of enforcement."""

    records: tuple[HistoricalRecord, ...]
    excluded_as_of_violations: int


def as_of_violation_conditions() -> tuple:
    """Return conditions matching provenance-complete rows that break causality.

    The scope is every run claiming complete provenance (canonical LIVE
    predicate, or the reconstruction provenance core *without* its as-of
    clause so violations stay detectable).  A row violates the boundary when
    its ``data_as_of`` is missing or newer than the signal date; such rows can
    never prove they were generated without future data.
    """
    provenance_claim = or_(
        and_(*live_forward_provenance_conditions()),
        and_(*historical_reconstruction_provenance_conditions()),
    )
    causal_breach = or_(
        RecommendationRun.data_as_of.is_(None),
        RecommendationRun.data_as_of > RecommendationRun.trade_date,
    )
    return (and_(provenance_claim, causal_breach),)


def historical_records_statement(
    *,
    signal_date: str | None = None,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_axis: HistoricalDateAxis = "signal_date",
    evidence_classes: tuple[str, ...] | list[str] | None = None,
) -> Select:
    """Build a causal Historical_Query statement for the requested date axis.

    ``RecommendationRun.trade_date`` is the persisted signal date, while
    ``Recommendation.trade_date`` is the recommendation's business/trade date.
    Callers can filter either exact date independently and choose which axis
    receives range filters and primary ordering.
    """
    date_column = _date_column(date_axis)
    stmt = (
        select(RecommendationRun, Recommendation)
        .join(Recommendation, Recommendation.run_id == RecommendationRun.run_id)
        .where(not_(and_(*as_of_violation_conditions())))
        .order_by(
            date_column.desc(),
            Recommendation.final_score.desc(),
            Recommendation.recommendation_id.asc(),
        )
    )
    return _apply_date_filters(
        stmt,
        signal_date=signal_date,
        trade_date=trade_date,
        date_from=date_from,
        date_to=date_to,
        date_axis=date_axis,
        evidence_classes=evidence_classes,
    )


def as_of_violation_count_statement(
    *,
    signal_date: str | None = None,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_axis: HistoricalDateAxis = "signal_date",
    evidence_classes: tuple[str, ...] | list[str] | None = None,
) -> Select:
    """Build the statement counting rows excluded by as-of enforcement.

    Uses the same date/class filters as :func:`historical_records_statement`,
    so the count is exactly the population that query withheld.
    """
    stmt = (
        select(func.count(Recommendation.recommendation_id))
        .select_from(RecommendationRun)
        .join(Recommendation, Recommendation.run_id == RecommendationRun.run_id)
        .where(*as_of_violation_conditions())
    )
    return _apply_date_filters(
        stmt,
        signal_date=signal_date,
        trade_date=trade_date,
        date_from=date_from,
        date_to=date_to,
        date_axis=date_axis,
        evidence_classes=evidence_classes,
    )


def query_historical_records(
    db: Session,
    *,
    signal_date: str | None = None,
    trade_date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    date_axis: HistoricalDateAxis = "signal_date",
    evidence_classes: tuple[str, ...] | list[str] | None = None,
) -> HistoricalQueryResult:
    """Run the read-only Historical_Query and return rows plus exclusions."""
    rows = db.execute(
        historical_records_statement(
            signal_date=signal_date,
            trade_date=trade_date,
            date_from=date_from,
            date_to=date_to,
            date_axis=date_axis,
            evidence_classes=evidence_classes,
        )
    ).all()
    excluded = db.execute(
        as_of_violation_count_statement(
            signal_date=signal_date,
            trade_date=trade_date,
            date_from=date_from,
            date_to=date_to,
            date_axis=date_axis,
            evidence_classes=evidence_classes,
        )
    ).scalar_one()
    records = tuple(
        HistoricalRecord(
            run=run,
            recommendation=recommendation,
            evidence_class=getattr(run, "evidence_class", None),
        )
        for run, recommendation in rows
    )
    return HistoricalQueryResult(
        records=records,
        excluded_as_of_violations=int(excluded or 0),
    )


def classification_audits_for_run_statement(run_id: str) -> Select:
    """Build the query listing reviewed classification audits for one run."""
    return (
        select(RecommendationEvidenceClassificationAudit)
        .where(RecommendationEvidenceClassificationAudit.run_id == run_id)
        .order_by(
            RecommendationEvidenceClassificationAudit.planned_at.asc(),
            RecommendationEvidenceClassificationAudit.plan_hash.asc(),
        )
    )


def list_classification_audits_for_run(
    db: Session,
    run_id: str,
) -> list[RecommendationEvidenceClassificationAudit]:
    """Return classification audit rows for a run (read-only)."""
    return list(db.scalars(classification_audits_for_run_statement(run_id)).all())


def outcome_versions_for_recommendation_statement(recommendation_id: str) -> Select:
    """Build the query listing append-only outcome versions of a recommendation."""
    return (
        select(RecommendationOutcomeVersion)
        .where(RecommendationOutcomeVersion.recommendation_id == recommendation_id)
        .order_by(
            RecommendationOutcomeVersion.computed_at.asc(),
            RecommendationOutcomeVersion.version_id.asc(),
        )
    )


def list_outcome_versions_for_recommendation(
    db: Session,
    recommendation_id: str,
) -> list[RecommendationOutcomeVersion]:
    """Return outcome version rows for a recommendation (read-only)."""
    return list(
        db.scalars(outcome_versions_for_recommendation_statement(recommendation_id)).all()
    )


def _date_column(date_axis: HistoricalDateAxis):
    if date_axis == "signal_date":
        return RecommendationRun.trade_date
    if date_axis == "trade_date":
        return Recommendation.trade_date
    raise ValueError("date_axis must be 'signal_date' or 'trade_date'")


def _apply_date_filters(
    stmt: Select,
    *,
    signal_date: str | None,
    trade_date: str | None,
    date_from: str | None,
    date_to: str | None,
    date_axis: HistoricalDateAxis,
    evidence_classes: tuple[str, ...] | list[str] | None,
) -> Select:
    date_column = _date_column(date_axis)
    if signal_date is not None:
        stmt = stmt.where(RecommendationRun.trade_date == signal_date)
    if trade_date is not None:
        stmt = stmt.where(Recommendation.trade_date == trade_date)
    if date_from is not None:
        stmt = stmt.where(date_column >= date_from)
    if date_to is not None:
        stmt = stmt.where(date_column <= date_to)
    if evidence_classes:
        stmt = stmt.where(RecommendationRun.evidence_class.in_(list(evidence_classes)))
    return stmt


__all__ = [
    "HistoricalDateAxis",
    "HistoricalQueryResult",
    "HistoricalRecord",
    "as_of_violation_conditions",
    "as_of_violation_count_statement",
    "classification_audits_for_run_statement",
    "historical_records_statement",
    "list_classification_audits_for_run",
    "list_outcome_versions_for_recommendation",
    "outcome_versions_for_recommendation_statement",
    "query_historical_records",
]

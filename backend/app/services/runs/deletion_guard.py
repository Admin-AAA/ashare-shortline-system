"""Fail-closed physical-deletion checks for recommendation evidence."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    AgentDecisionLog,
    ApiCallLog,
    IntradayLockMonitor,
    IntradayLockSignal,
    Recommendation,
    RecommendationCollectionSeal,
    RecommendationEvidenceClassificationAudit,
    RecommendationExecutionState,
    RecommendationOutcome,
    RecommendationOutcomeVersion,
    RecommendationRun,
)


class DownstreamFactDeletionBlocked(RuntimeError):
    """Raised before a physical delete would discard or orphan recorded facts."""

    code = "downstream_facts_exist"

    def __init__(
        self,
        *,
        subject_kind: str,
        subject_id: str,
        blockers: Iterable[str],
    ) -> None:
        self.subject_kind = subject_kind
        self.subject_id = subject_id
        self.blockers = tuple(dict.fromkeys(blockers))
        if not self.blockers:
            raise ValueError("deletion guard requires at least one blocker")
        super().__init__(
            f"cannot delete {subject_kind} {subject_id}: "
            f"downstream facts exist ({', '.join(self.blockers)})"
        )

    def detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "subject": self.subject_kind,
            "id": self.subject_id,
            "blockers": list(self.blockers),
        }


def _has_rows(db: Session, statement: object) -> bool:
    return db.scalar(statement.limit(1)) is not None  # type: ignore[union-attr]


def _status_value(run: RecommendationRun) -> str:
    status = getattr(run.status, "value", run.status)
    return str(status)


def _run_blockers(db: Session, run: RecommendationRun) -> list[str]:
    blockers: list[str] = []
    if _status_value(run) in {"queued", "running", "interrupted"}:
        blockers.append("active_run")
    if str(run.reconstruction_batch_id or "").strip():
        blockers.append("reconstruction_batch_member")
    if _has_rows(
        db,
        select(RecommendationCollectionSeal.run_id).where(
            RecommendationCollectionSeal.run_id == run.run_id
        ),
    ):
        blockers.append("collection_seal")
    if _has_rows(
        db,
        select(RecommendationEvidenceClassificationAudit.plan_hash).where(
            RecommendationEvidenceClassificationAudit.run_id == run.run_id
        ),
    ):
        blockers.append("classification_audit")
    if _has_rows(
        db,
        select(ApiCallLog.id).where(ApiCallLog.run_id == run.run_id),
    ):
        blockers.append("api_call_log")
    if _has_rows(
        db,
        select(AgentDecisionLog.id).where(AgentDecisionLog.run_id == run.run_id),
    ):
        blockers.append("agent_decision_log")
    return blockers


def _recommendation_blockers(db: Session, recommendation: Recommendation) -> list[str]:
    recommendation_id = recommendation.recommendation_id
    blockers: list[str] = []
    if _has_rows(
        db,
        select(RecommendationOutcomeVersion.version_id).where(
            RecommendationOutcomeVersion.recommendation_id == recommendation_id
        ),
    ):
        blockers.append("outcome_version")
    if _has_rows(
        db,
        select(RecommendationOutcome.recommendation_id).where(
            RecommendationOutcome.recommendation_id == recommendation_id
        ),
    ):
        blockers.append("outcome")
    if _has_rows(
        db,
        select(RecommendationExecutionState.recommendation_id).where(
            RecommendationExecutionState.recommendation_id == recommendation_id
        ),
    ):
        blockers.append("execution_state")
    if _has_rows(
        db,
        select(AgentDecisionLog.id).where(
            AgentDecisionLog.recommendation_id == recommendation_id
        ),
    ):
        blockers.append("agent_decision_log")

    monitor_ids = select(IntradayLockMonitor.monitor_id).where(
        IntradayLockMonitor.recommendation_id == recommendation_id
    )
    if _has_rows(db, monitor_ids):
        blockers.append("intraday_lock_monitor")
    if _has_rows(
        db,
        select(IntradayLockSignal.signal_id).where(
            IntradayLockSignal.monitor_id.in_(monitor_ids)
        ),
    ):
        blockers.append("intraday_lock_signal")
    return blockers


def assert_recommendation_physically_deletable(
    db: Session,
    recommendation: Recommendation,
) -> None:
    """Require a complete no-fact preflight before deleting one recommendation."""
    run = db.get(RecommendationRun, recommendation.run_id)
    blockers = ["orphaned_run"] if run is None else _run_blockers(db, run)
    blockers.extend(_recommendation_blockers(db, recommendation))
    if blockers:
        raise DownstreamFactDeletionBlocked(
            subject_kind="recommendation",
            subject_id=recommendation.recommendation_id,
            blockers=blockers,
        )


def assert_run_physically_deletable(
    db: Session,
    run: RecommendationRun,
    *,
    recommendations: Iterable[Recommendation] | None = None,
) -> tuple[Recommendation, ...]:
    """Require all run and recommendation facts to be absent before any delete."""
    recs = tuple(
        recommendations
        if recommendations is not None
        else db.scalars(
            select(Recommendation).where(Recommendation.run_id == run.run_id)
        ).all()
    )
    blockers = _run_blockers(db, run)
    for recommendation in recs:
        blockers.extend(_recommendation_blockers(db, recommendation))
    if blockers:
        raise DownstreamFactDeletionBlocked(
            subject_kind="run",
            subject_id=run.run_id,
            blockers=blockers,
        )
    return recs


__all__ = [
    "DownstreamFactDeletionBlocked",
    "assert_recommendation_physically_deletable",
    "assert_run_physically_deletable",
]

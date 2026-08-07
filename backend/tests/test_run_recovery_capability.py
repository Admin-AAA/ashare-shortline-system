from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import (
    Recommendation,
    RecommendationCollectionSeal,
    RecommendationOutcome,
    RecommendationRun,
)
from app.services.runs.collection_seal import seal_recommendation_collection
from app.services.runs.contracts import RunErrorCode, RunPhase, RunStatus, parse_run_request
from app.services.runs.recovery import (
    RecoveryCapabilityError,
    evaluate_recovery_capability,
)
from app.services.runs.repository import CreateQueuedRun
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from app.services import workbench_service


NOW = datetime(2026, 8, 7, 10, 0, 0)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    Recommendation.__table__.create(engine)
    RecommendationOutcome.__table__.create(engine)
    RecommendationCollectionSeal.__table__.create(engine)
    return Session(engine)


def _interrupted_run(
    db: Session,
    *,
    run_id: str = "run-recovery",
    phase: RunPhase = RunPhase.PERSISTENCE,
    with_seal: bool = True,
) -> RecommendationRun:
    repository = SQLAlchemyRunRepository(db)
    created = repository.create_queued_run(
        CreateQueuedRun(
            run_id=run_id,
            request=parse_run_request(
                {"run_mode": "experiment", "run_name": run_id}
            ),
            execution_version="execution-v1:test",
            created_at=NOW,
        )
    )
    assert created.applied
    db.add(
        Recommendation(
            recommendation_id=f"rec-{run_id}",
            run_id=run_id,
            trade_date="2026-08-07",
            recommended_at=NOW,
            code="000001",
            name="平安银行",
            industry="银行",
            recommend_price=10.0,
            bucket="daily-stock-analysis-skill",
            final_score=80.0,
            consensus_score=78.0,
            status="pending",
        )
    )
    db.flush()
    seal = seal_recommendation_collection(db, run_id, sealed_at=NOW) if with_seal else None
    run = db.get(RecommendationRun, run_id)
    assert run is not None
    run.status = RunStatus.INTERRUPTED.value
    run.lifecycle_phase = phase.value
    run.checkpoint_side_effect_key = (
        seal.checkpoint_side_effect_key if seal is not None else f"{phase.value}:{run_id}"
    )
    run.lease_owner_id = None
    run.lease_token = None
    run.lease_acquired_at = None
    run.lease_expires_at = None
    db.commit()
    return run


def test_capability_accepts_only_sealed_persistence_checkpoint() -> None:
    db = _session()
    try:
        run = _interrupted_run(db)

        capability = evaluate_recovery_capability(db, run.run_id, now=NOW)

        assert capability.can_resume is True
        assert capability.block_code is None
        check = capability.require_resume_check()
        assert check.last_committed_phase is RunPhase.PERSISTENCE
        assert check.collection_seal_verified is True
    finally:
        db.close()


@pytest.mark.parametrize("phase", (RunPhase.PREPARE, RunPhase.REVIEW, RunPhase.AGGREGATION))
def test_capability_rejects_every_intermediate_checkpoint(phase: RunPhase) -> None:
    db = _session()
    try:
        run = _interrupted_run(db, phase=phase)

        capability = evaluate_recovery_capability(db, run.run_id, now=NOW)

        assert capability.can_resume is False
        assert capability.block_code == RunErrorCode.RESUME_REQUIRES_PERSISTENCE_CHECKPOINT.value
    finally:
        db.close()


def test_capability_rejects_legacy_persistence_checkpoint_without_seal() -> None:
    db = _session()
    try:
        run = _interrupted_run(db, with_seal=False)

        capability = evaluate_recovery_capability(db, run.run_id, now=NOW)

        assert capability.can_resume is False
        assert capability.block_code == RunErrorCode.RESUME_SEAL_NOT_FOUND.value
    finally:
        db.close()


def test_capability_rejects_failed_and_active_lease() -> None:
    db = _session()
    try:
        failed = _interrupted_run(db, run_id="run-failed")
        failed.status = RunStatus.FAILED.value
        db.commit()
        failed_capability = evaluate_recovery_capability(db, failed.run_id, now=NOW)
        assert failed_capability.can_resume is False
        assert failed_capability.block_code == RunErrorCode.RESUME_REQUIRES_INTERRUPTED_STATUS.value

        leased = _interrupted_run(db, run_id="run-leased")
        leased.lease_owner_id = "worker-other"
        leased.lease_token = "opaque-token"
        leased.lease_acquired_at = NOW
        leased.lease_expires_at = NOW + timedelta(minutes=5)
        db.commit()
        leased_capability = evaluate_recovery_capability(db, leased.run_id, now=NOW)
        assert leased_capability.can_resume is False
        assert leased_capability.block_code == RunErrorCode.RESUME_REQUIRES_NO_ACTIVE_LEASE.value
    finally:
        db.close()


def test_run_detail_projects_capability_and_dispatch_revalidates(
    monkeypatch,
) -> None:
    db = _session()
    try:
        recoverable = _interrupted_run(db, run_id="run-dispatch")
        detail = workbench_service.get_run_detail(db, recoverable.run_id)
        assert detail is not None
        assert detail["can_resume"] is True
        assert detail["resume_block_code"] is None
        assert detail["resume_block_reason"] is None

        triggered: list[str] = []
        monkeypatch.setattr(
            workbench_service,
            "_trigger_recommendation",
            lambda run_id: triggered.append(run_id),
        )
        workbench_service.dispatch_workbench_run(db, recoverable.run_id)
        assert triggered == [recoverable.run_id]

        blocked = _interrupted_run(
            db,
            run_id="run-dispatch-blocked",
            with_seal=False,
        )
        blocked_detail = workbench_service.get_run_detail(db, blocked.run_id)
        assert blocked_detail is not None
        assert blocked_detail["can_resume"] is False
        assert (
            blocked_detail["resume_block_code"]
            == RunErrorCode.RESUME_SEAL_NOT_FOUND.value
        )
        with pytest.raises(RecoveryCapabilityError) as exc_info:
            workbench_service.dispatch_workbench_run(db, blocked.run_id)
        assert exc_info.value.code == RunErrorCode.RESUME_SEAL_NOT_FOUND.value
        assert triggered == [recoverable.run_id]
    finally:
        db.close()


def test_capability_rejects_changed_collection_without_mutating_run() -> None:
    db = _session()
    try:
        run = _interrupted_run(db)
        recommendation = db.get(Recommendation, f"rec-{run.run_id}")
        assert recommendation is not None
        recommendation.final_score = 81.0
        db.commit()

        capability = evaluate_recovery_capability(db, run.run_id, now=NOW, lock=True)

        assert capability.can_resume is False
        assert capability.block_code == RunErrorCode.RESUME_SEAL_DIGEST_MISMATCH.value
        db.rollback()
        persisted = db.get(RecommendationRun, run.run_id)
        assert persisted is not None
        assert persisted.status == RunStatus.INTERRUPTED.value
        assert persisted.lifecycle_phase == RunPhase.PERSISTENCE.value
    finally:
        db.close()

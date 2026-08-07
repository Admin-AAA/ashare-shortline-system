from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.models.tables import (
    Recommendation,
    RecommendationCollectionSeal,
    RecommendationRun,
)
from app.services.runs import pipeline
from app.services.runs.collection_seal import seal_recommendation_collection
from app.services.runs.contracts import RunErrorCode, RunPhase, RunStatus, parse_run_request
from app.services.runs.recovery import RecoveryCapabilityError
from app.services.runs.repository import CreateQueuedRun
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository


NOW = datetime(2026, 8, 7, 10, 0, 0)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    Recommendation.__table__.create(engine)
    RecommendationCollectionSeal.__table__.create(engine)
    return Session(engine)


def _interrupted_sealed_run(
    db: Session,
    *,
    run_id: str = "run-completion-only",
    with_recommendation: bool = True,
):
    request = parse_run_request({"run_mode": "experiment", "run_name": run_id})
    created = SQLAlchemyRunRepository(db).create_queued_run(
        CreateQueuedRun(
            run_id=run_id,
            request=request,
            execution_version="execution-v1:test",
            created_at=NOW,
        )
    )
    assert created.applied
    if with_recommendation:
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
    seal = seal_recommendation_collection(db, run_id, sealed_at=NOW)
    run = db.get(RecommendationRun, run_id)
    assert run is not None
    run.status = RunStatus.INTERRUPTED.value
    run.lifecycle_phase = RunPhase.PERSISTENCE.value
    run.checkpoint_phase = RunPhase.PERSISTENCE.value
    run.checkpoint_side_effect_key = seal.checkpoint_side_effect_key
    run.progress_phase = RunStatus.INTERRUPTED.value
    run.run_revision = 7
    run.lease_owner_id = None
    run.lease_token = None
    run.lease_acquired_at = None
    run.lease_expires_at = None
    db.commit()
    return request


def _forbid_business_execution(monkeypatch) -> None:
    from app.agents.session_registry import AgentSessionRegistry
    from app.services.evidence import reconstruction_batch

    def forbidden(*_args, **_kwargs):
        pytest.fail("completion-only recovery must not execute business stages")

    monkeypatch.setattr(pipeline, "assemble_pipeline_stages", forbidden)
    monkeypatch.setattr(AgentSessionRegistry, "register", forbidden)
    monkeypatch.setattr(
        reconstruction_batch,
        "bind_reconstruction_worker_revision",
        forbidden,
    )


def _stub_serialization(monkeypatch) -> None:
    from app.services import workbench_service

    monkeypatch.setattr(
        workbench_service,
        "_serialize_run_summary",
        lambda _db, run: {"run_id": run.run_id, "status": run.status},
    )
    monkeypatch.setattr(
        workbench_service,
        "_serialize_recommendation",
        lambda _db, recommendation: {
            "recommendation_id": recommendation.recommendation_id,
            "final_score": recommendation.final_score,
        },
    )
    monkeypatch.setattr(workbench_service, "invalidate_bootstrap_cache", lambda: None)


@pytest.mark.parametrize("with_recommendation", (False, True))
def test_sealed_persistence_recovery_only_completes_lifecycle_and_provenance(
    monkeypatch,
    with_recommendation: bool,
) -> None:
    db = _session()
    try:
        request = _interrupted_sealed_run(
            db,
            with_recommendation=with_recommendation,
        )
        original_rows = list(
            db.scalars(
                select(Recommendation)
                .where(Recommendation.run_id == "run-completion-only")
                .order_by(Recommendation.recommendation_id)
            ).all()
        )
        original_values = [
            (row.recommendation_id, row.code, row.final_score)
            for row in original_rows
        ]
        original_seal = db.get(RecommendationCollectionSeal, "run-completion-only")
        assert original_seal is not None
        original_seal_values = (
            original_seal.recommendation_row_count,
            original_seal.collection_digest,
            original_seal.checkpoint_side_effect_key,
        )
        commits: list[bool] = []
        event.listen(db, "after_commit", lambda _session: commits.append(True))
        finalized: list[str] = []

        def finalize(session: Session, run_id: str) -> bool:
            assert commits == []
            run = session.get(RecommendationRun, run_id)
            assert run is not None
            assert run.status == RunStatus.COMPLETED.value
            finalized.append(run_id)
            return False

        monkeypatch.setattr(pipeline, "_finalize_completed_run_provenance", finalize)
        _forbid_business_execution(monkeypatch)
        _stub_serialization(monkeypatch)

        result = asyncio.run(
            pipeline.run_staged_pipeline(
                db,
                run_id="run-completion-only",
                request=request,
                existing_run_id="run-completion-only",
            )
        )

        assert commits == [True]
        assert finalized == ["run-completion-only"]
        assert result["run"]["status"] == RunStatus.COMPLETED.value
        db.expire_all()
        completed = db.get(RecommendationRun, "run-completion-only")
        assert completed is not None
        assert completed.status == RunStatus.COMPLETED.value
        assert completed.lifecycle_phase == RunPhase.COMPLETED.value
        assert completed.run_revision == 10
        assert completed.lease_token is None
        assert completed.recommended_count == len(original_values)
        persisted_rows = list(
            db.scalars(
                select(Recommendation)
                .where(Recommendation.run_id == "run-completion-only")
                .order_by(Recommendation.recommendation_id)
            ).all()
        )
        assert [
            (row.recommendation_id, row.code, row.final_score)
            for row in persisted_rows
        ] == original_values
        persisted_seal = db.get(RecommendationCollectionSeal, "run-completion-only")
        assert persisted_seal is not None
        assert (
            persisted_seal.recommendation_row_count,
            persisted_seal.collection_digest,
            persisted_seal.checkpoint_side_effect_key,
        ) == original_seal_values
    finally:
        db.close()


def test_recovery_provenance_failure_rolls_back_to_original_interrupted_state(
    monkeypatch,
) -> None:
    db = _session()
    try:
        request = _interrupted_sealed_run(db)
        commits: list[bool] = []
        event.listen(db, "after_commit", lambda _session: commits.append(True))
        _forbid_business_execution(monkeypatch)
        monkeypatch.setattr(
            pipeline,
            "_finalize_completed_run_provenance",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("provenance rejected")
            ),
        )

        with pytest.raises(RuntimeError, match="provenance rejected"):
            asyncio.run(
                pipeline.run_staged_pipeline(
                    db,
                    run_id="run-completion-only",
                    request=request,
                )
            )

        assert commits == []
        db.expire_all()
        interrupted = db.get(RecommendationRun, "run-completion-only")
        assert interrupted is not None
        assert interrupted.status == RunStatus.INTERRUPTED.value
        assert interrupted.lifecycle_phase == RunPhase.PERSISTENCE.value
        assert interrupted.run_revision == 7
        assert interrupted.lease_token is None
        assert db.get(RecommendationCollectionSeal, "run-completion-only") is not None
        recommendations = list(
            db.scalars(
                select(Recommendation).where(
                    Recommendation.run_id == "run-completion-only"
                )
            ).all()
        )
        assert [item.recommendation_id for item in recommendations] == [
            "rec-run-completion-only"
        ]
    finally:
        db.close()


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    (
        ("missing_seal", RunErrorCode.RESUME_SEAL_NOT_FOUND.value),
        ("key_mismatch", RunErrorCode.RESUME_SEAL_KEY_MISMATCH.value),
        ("row_count", RunErrorCode.RESUME_SEAL_ROW_COUNT_MISMATCH.value),
        ("digest", RunErrorCode.RESUME_SEAL_DIGEST_MISMATCH.value),
    ),
)
def test_worker_boundary_revalidates_seal_before_any_business_execution(
    monkeypatch,
    tamper: str,
    expected_code: str,
) -> None:
    db = _session()
    try:
        request = _interrupted_sealed_run(db)
        run = db.get(RecommendationRun, "run-completion-only")
        seal = db.get(RecommendationCollectionSeal, "run-completion-only")
        recommendation = db.get(Recommendation, "rec-run-completion-only")
        assert run is not None and seal is not None and recommendation is not None
        if tamper == "missing_seal":
            db.delete(seal)
        elif tamper == "key_mismatch":
            run.checkpoint_side_effect_key = "recommendation-seal:v1:wrong:key"
        elif tamper == "row_count":
            db.add(
                Recommendation(
                    recommendation_id="rec-run-completion-only-extra",
                    run_id=run.run_id,
                    trade_date="2026-08-07",
                    recommended_at=NOW,
                    code="000002",
                    name="万科A",
                    industry="房地产",
                    recommend_price=5.0,
                    bucket="daily-stock-analysis-skill",
                    final_score=79.0,
                    consensus_score=77.0,
                    status="pending",
                )
            )
        else:
            recommendation.final_score = 81.0
        db.commit()
        _forbid_business_execution(monkeypatch)

        with pytest.raises(RecoveryCapabilityError) as exc_info:
            asyncio.run(
                pipeline.run_staged_pipeline(
                    db,
                    run_id="run-completion-only",
                    request=request,
                )
            )

        assert exc_info.value.code == expected_code
        db.expire_all()
        persisted = db.get(RecommendationRun, "run-completion-only")
        assert persisted is not None
        assert persisted.status == RunStatus.INTERRUPTED.value
        assert persisted.run_revision == 7
        assert persisted.lease_token is None
    finally:
        db.close()

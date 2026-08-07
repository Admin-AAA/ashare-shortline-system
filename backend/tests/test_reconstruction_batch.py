from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tables import (
    RecommendationReconstructionBatch,
    RecommendationRun,
)
from app.services.evidence import identity_from_persisted_run
from app.services.evidence.reconstruction_batch import (
    RECONSTRUCTION_BATCH_STATUS_COMPLETED,
    ReconstructionBatchError,
    bind_reconstruction_worker_revision,
    build_reconstruction_batch_identity,
    create_or_resume_reconstruction_batch,
    finalize_reconstruction_run,
    latest_validated_reconstruction_cohort,
    mark_reconstruction_batch_failed,
    publish_reconstruction_batch,
)
from app.services.evidence.validation import (
    canonical_historical_reconstruction_run_ids,
)
from app.services.runs import pipeline


DATES = ("2099-01-04", "2099-01-05")
STRATEGIES = (
    {
        "strategy_id": "daily-stock-analysis-skill",
        "strategy_version": "v1",
        "fingerprint": "a" * 64,
    },
)
EXECUTION_POLICY = {
    "policy_id": "recommendation-run",
    "policy_version": "v1",
    "fingerprint": "b" * 64,
}
REQUEST = {
    "schema_version": 1,
    "run_mode": "formal",
    "run_name": "REPLAY-历史推荐重建",
    "trade_date_override": DATES[0],
    "score_line": 72.0,
    "output_count": 6,
    "skip_aux_refresh": True,
}
CODE_REVISION = "c" * 40


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationReconstructionBatch.__table__.create(engine)
    RecommendationRun.__table__.create(engine)
    return engine


def _identity(**overrides):
    values = {
        "expected_trade_dates": DATES,
        "request_template": REQUEST,
        "strategy_policy": STRATEGIES,
        "execution_policy": EXECUTION_POLICY,
        "code_revision": CODE_REVISION,
    }
    values.update(overrides)
    return build_reconstruction_batch_identity(**values)


def _execution_summary() -> str:
    return json.dumps(
        {
            "execution": {
                "strategy_fingerprints": list(STRATEGIES),
                "execution_policy": EXECUTION_POLICY,
            }
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sealed_run(
    identity,
    trade_date: str,
    *,
    run_id: str | None = None,
    strategy_policy_fingerprint: str | None = None,
    execution_policy_fingerprint: str | None = None,
    code_revision: str | None = None,
) -> RecommendationRun:
    run = RecommendationRun(
        run_id=run_id or f"reconstruction_{trade_date}",
        run_name="REPLAY fixture",
        run_mode="formal",
        evaluation_scope="historical_replay",
        trade_date=trade_date,
        status="completed",
        evidence_class="historical_reconstruction",
        actual_generated_at=datetime(2099, 1, 10, 2, 0),
        data_as_of=trade_date,
        source_snapshot_id=f"snapshot-{trade_date}",
        strategy_policy_fingerprint=(
            strategy_policy_fingerprint or identity.strategy_policy_fingerprint
        ),
        execution_policy_fingerprint=(
            execution_policy_fingerprint or identity.execution_policy_fingerprint
        ),
        code_revision=code_revision or identity.code_revision,
        reconstruction_batch_id=identity.batch_id,
        provenance_schema_version=1,
        request_schema_version=1,
        request_json=json.dumps(
            {**REQUEST, "trade_date_override": trade_date},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        result_summary_json=_execution_summary(),
    )
    run.evidence_identity_hash = identity_from_persisted_run(run).identity_hash
    return run


def test_finalize_completed_run_seals_worker_provenance() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        run = _sealed_run(identity, DATES[0])
        run.evidence_identity_hash = None
        db.add(run)
        db.flush()

        finalize_reconstruction_run(db, run.run_id)

        assert run.evidence_identity_hash == identity_from_persisted_run(run).identity_hash
        assert run.strategy_policy_fingerprint == identity.strategy_policy_fingerprint
        assert run.execution_policy_fingerprint == identity.execution_policy_fingerprint
        assert run.code_revision == identity.code_revision
        assert run.reconstruction_batch_id == identity.batch_id


def test_finalize_rejects_out_of_manifest_date_without_sealing() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        run = _sealed_run(identity, DATES[0])
        run.trade_date = "2099-01-06"
        run.request_json = json.dumps(
            {**REQUEST, "trade_date_override": run.trade_date},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        run.data_as_of = run.trade_date
        run.evidence_identity_hash = None
        db.add(run)
        db.flush()

        with pytest.raises(ReconstructionBatchError, match="outside its batch manifest"):
            finalize_reconstruction_run(db, run.run_id)

        assert run.evidence_identity_hash is None


def test_worker_revision_binding_rejects_expired_lease() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        run = _sealed_run(identity, DATES[0])
        run.status = "running"
        run.evidence_identity_hash = None
        run.lease_token = "worker-lease"
        run.lease_expires_at = datetime(2000, 1, 1)
        db.add(run)
        db.flush()

        with pytest.raises(ReconstructionBatchError, match="active worker lease"):
            bind_reconstruction_worker_revision(
                db,
                run.run_id,
                worker_lease=pipeline.WorkerLease(
                    run_id=run.run_id,
                    token="worker-lease",
                    revision=1,
                ),
                code_revision=identity.code_revision,
            )


def test_unmarked_historical_replay_does_not_require_worker_revision() -> None:
    engine = _engine()
    with Session(engine) as db:
        run = RecommendationRun(
            run_id="ordinary_historical_replay",
            run_name="ordinary historical replay",
            run_mode="formal",
            evaluation_scope="historical_replay",
            trade_date=DATES[0],
            status="running",
            request_schema_version=1,
            request_json=json.dumps(REQUEST, sort_keys=True, separators=(",", ":")),
        )
        db.add(run)
        db.flush()

        assert bind_reconstruction_worker_revision(
            db,
            run.run_id,
            worker_lease=pipeline.WorkerLease(
                run_id=run.run_id,
                token="worker-lease",
                revision=0,
            ),
        ) is None


    first = _identity()
    request_for_other_day = {**REQUEST, "trade_date_override": DATES[1]}
    second = _identity(request_template=request_for_other_day)

    assert first == second
    assert first.batch_id.startswith("recon-v1-")
    assert len(first.batch_id) <= 80

    assert _identity(
        request_template={**REQUEST, "score_line": 73.0}
    ).batch_id != first.batch_id
    assert _identity(code_revision="d" * 40).batch_id != first.batch_id
    assert _identity(
        execution_policy={**EXECUTION_POLICY, "fingerprint": "e" * 64}
    ).batch_id != first.batch_id
    assert _identity(
        strategy_policy=({**STRATEGIES[0], "fingerprint": "f" * 64},)
    ).batch_id != first.batch_id


def test_worker_revision_is_bound_only_with_active_lease_and_matching_manifest() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        run = _sealed_run(identity, DATES[0])
        run.status = "running"
        run.evidence_identity_hash = None
        run.run_revision = 3
        run.lease_token = "worker-lease"
        run.lease_expires_at = datetime(2099, 1, 10)
        db.add(run)
        db.flush()

        lease = pipeline.WorkerLease(
            run_id=run.run_id,
            token="worker-lease",
            revision=3,
        )
        assert bind_reconstruction_worker_revision(
            db,
            run.run_id,
            worker_lease=lease,
            code_revision=identity.code_revision,
        ) == identity.code_revision
        assert run.code_revision == identity.code_revision

        with pytest.raises(ReconstructionBatchError, match="worker revision"):
            bind_reconstruction_worker_revision(
                db,
                run.run_id,
                worker_lease=lease,
                code_revision="d" * 40,
            )

        run.lease_token = None
        with pytest.raises(ReconstructionBatchError, match="active worker lease"):
            bind_reconstruction_worker_revision(
                db,
                run.run_id,
                worker_lease=lease,
                code_revision=identity.code_revision,
            )


def test_scope_or_request_template_drift_cannot_be_published() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        runs = [_sealed_run(identity, trade_date) for trade_date in DATES]
        db.add_all(runs)
        db.flush()

        runs[1].evaluation_scope = "live_forward"
        with pytest.raises(ReconstructionBatchError, match="batch manifest"):
            publish_reconstruction_batch(db, identity.batch_id)

        runs[1].evaluation_scope = "historical_replay"
        runs[1].request_json = json.dumps(
            {**REQUEST, "trade_date_override": DATES[1], "score_line": 73.0},
            sort_keys=True,
            separators=(",", ":"),
        )
        with pytest.raises(ReconstructionBatchError, match="batch manifest"):
            publish_reconstruction_batch(db, identity.batch_id)


def test_incomplete_or_policy_mixed_batch_cannot_be_published() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        batch = create_or_resume_reconstruction_batch(db, identity)
        db.add(_sealed_run(identity, DATES[0]))
        db.flush()

        with pytest.raises(ReconstructionBatchError, match="incomplete"):
            publish_reconstruction_batch(db, identity.batch_id)

        batch.status = RECONSTRUCTION_BATCH_STATUS_COMPLETED
        batch.completed_trade_date_count = 1
        batch.cohort_identity_hash = "f" * 64
        batch.completed_at = datetime(2099, 1, 10, 3, 0)
        db.flush()
        assert latest_validated_reconstruction_cohort(db) is None

        batch.status = "building"
        batch.completed_at = None
        batch.cohort_identity_hash = None
        db.add(
            _sealed_run(
                identity,
                DATES[1],
                strategy_policy_fingerprint="1" * 64,
            )
        )
        db.flush()
        with pytest.raises(ReconstructionBatchError, match="batch manifest"):
            publish_reconstruction_batch(db, identity.batch_id)


def test_complete_batch_is_visible_and_sealed_against_run_replacement() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        runs = [_sealed_run(identity, trade_date) for trade_date in DATES]
        db.add_all(runs)
        db.flush()

        published = publish_reconstruction_batch(db, identity.batch_id)
        batch = db.get(RecommendationReconstructionBatch, identity.batch_id)
        assert batch is not None
        assert batch.status == RECONSTRUCTION_BATCH_STATUS_COMPLETED
        assert batch.completed_trade_date_count == len(DATES)
        assert batch.cohort_identity_hash == published.cohort_identity_hash

        completed_at = batch.completed_at
        republished = publish_reconstruction_batch(db, identity.batch_id)
        assert republished == published
        assert batch.completed_at == completed_at

        selected = latest_validated_reconstruction_cohort(db)
        assert selected is not None
        assert selected.batch_id == identity.batch_id
        assert selected.run_ids == tuple(run.run_id for run in runs)
        assert selected.expected_trade_dates == DATES

        runs[1].source_snapshot_id = "replacement-snapshot"
        runs[1].evidence_identity_hash = identity_from_persisted_run(
            runs[1]
        ).identity_hash
        db.flush()
        with pytest.raises(ReconstructionBatchError, match="sealed"):
            publish_reconstruction_batch(db, identity.batch_id)
        assert batch.completed_at == completed_at
        assert batch.cohort_identity_hash == published.cohort_identity_hash
        assert latest_validated_reconstruction_cohort(db) is None


def test_completed_batch_cannot_be_marked_failed() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        db.add_all(_sealed_run(identity, trade_date) for trade_date in DATES)
        db.flush()
        published = publish_reconstruction_batch(db, identity.batch_id)

        with pytest.raises(ReconstructionBatchError, match="sealed"):
            mark_reconstruction_batch_failed(db, identity.batch_id, "late failure")

        batch = db.get(RecommendationReconstructionBatch, identity.batch_id)
        assert batch is not None
        assert batch.status == RECONSTRUCTION_BATCH_STATUS_COMPLETED
        assert batch.cohort_identity_hash == published.cohort_identity_hash
        assert batch.completed_at == published.completed_at


def test_canonical_run_without_manifest_is_not_a_metrics_cohort() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        db.add_all(_sealed_run(identity, trade_date) for trade_date in DATES)
        db.flush()

        assert latest_validated_reconstruction_cohort(db) is None
        assert canonical_historical_reconstruction_run_ids(db) == ()


def test_canonical_run_ids_require_a_published_validated_cohort() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        runs = [_sealed_run(identity, trade_date) for trade_date in DATES]
        db.add_all(runs)
        db.flush()

        assert canonical_historical_reconstruction_run_ids(db) == ()
        assert canonical_historical_reconstruction_run_ids(
            db,
            trade_date=DATES[0],
        ) == ()

        publish_reconstruction_batch(db, identity.batch_id)

        assert tuple(
            canonical_historical_reconstruction_run_ids(db)
        ) == tuple(run.run_id for run in runs)
        assert tuple(
            canonical_historical_reconstruction_run_ids(
                db,
                trade_date=DATES[0],
            )
        ) == (runs[0].run_id,)
        assert canonical_historical_reconstruction_run_ids(
            db,
            trade_date="2099-01-08",
        ) == ()


def test_same_batch_and_trade_date_is_unique() -> None:
    engine = _engine()
    identity = _identity()
    with Session(engine) as db:
        create_or_resume_reconstruction_batch(db, identity)
        db.add_all(
            [
                _sealed_run(identity, DATES[0], run_id="duplicate_one"),
                _sealed_run(identity, DATES[0], run_id="duplicate_two"),
            ]
        )
        with pytest.raises(IntegrityError):
            db.flush()

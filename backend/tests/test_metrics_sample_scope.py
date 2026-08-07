from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.metrics import router as metrics_router
from app.db.session import get_db
from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun
from app.services.evidence import EvidenceClass, PROVENANCE_SCHEMA_VERSION
from app.services.official_sample_scope import (
    HISTORICAL_REPLAY_SCOPE,
    LIVE_FORWARD_SCOPE,
)
from app.services.outcome_service import (
    alpha_decay_detection,
    invalidate_outcome_metrics_cache,
    overview_metrics,
)


def _run(
    run_id: str,
    *,
    trade_date: str,
    evidence_class: EvidenceClass,
    evaluation_scope: str,
    complete: bool = True,
) -> RecommendationRun:
    is_replay = evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION
    return RecommendationRun(
        run_id=run_id,
        run_name="metrics scope fixture",
        run_mode="formal",
        evaluation_scope=evaluation_scope,
        evidence_class=evidence_class.value,
        actual_generated_at=datetime(2099, 2, 20, 2, 0),
        evidence_identity_hash="d" * 64,
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        reconstruction_batch_id=(
            "metrics-scope-replay-batch" if is_replay and complete else None
        ),
        provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        trade_date=trade_date,
        data_as_of=trade_date,
        source_snapshot_id=f"snapshot-{run_id}",
        started_at=datetime(2099, 2, 20, 2, 0),
        finished_at=datetime(2099, 2, 20, 2, 1),
        status="completed",
    )


def _recommendation(
    recommendation_id: str,
    run_id: str,
    *,
    trade_date: str,
    code: str,
    executed_return: float,
) -> tuple[Recommendation, RecommendationOutcome]:
    recommendation = Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date=trade_date,
        recommended_at=datetime(2099, 2, 20, 2, 0),
        code=code,
        name="metrics scope fixture",
        recommend_price=10.0,
        final_score=80.0,
        feature_snapshot_json="{}",
    )
    outcome = RecommendationOutcome(
        recommendation_id=recommendation_id,
        entry_triggered=True,
        entry_date=trade_date,
        exit_date=trade_date,
        executed_return=executed_return,
        outcome_label="success" if executed_return > 0 else "failed",
    )
    return recommendation, outcome


def test_metrics_scopes_are_provenance_selected_and_cache_isolated(db):
    trade_date = "2099-02-10"
    live = _run(
        "run_metrics_scope_live",
        trade_date=trade_date,
        evidence_class=EvidenceClass.LIVE_FORWARD,
        evaluation_scope="historical_replay",
    )
    replay = _run(
        "run_metrics_scope_replay",
        trade_date=trade_date,
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        evaluation_scope="live_forward",
    )
    incomplete_replay = _run(
        "run_metrics_scope_incomplete",
        trade_date=trade_date,
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        evaluation_scope="historical_replay",
        complete=False,
    )
    unverified = _run(
        "run_metrics_scope_unverified",
        trade_date=trade_date,
        evidence_class=EvidenceClass.UNVERIFIED,
        evaluation_scope="historical_replay",
    )
    live_rec, live_outcome = _recommendation(
        "rec_metrics_scope_live",
        live.run_id,
        trade_date=trade_date,
        code="989101",
        executed_return=2.0,
    )
    replay_rec, replay_outcome = _recommendation(
        "rec_metrics_scope_replay",
        replay.run_id,
        trade_date=trade_date,
        code="989102",
        executed_return=-2.0,
    )
    incomplete_rec, incomplete_outcome = _recommendation(
        "rec_metrics_scope_incomplete",
        incomplete_replay.run_id,
        trade_date=trade_date,
        code="989103",
        executed_return=3.0,
    )
    unverified_rec, unverified_outcome = _recommendation(
        "rec_metrics_scope_unverified",
        unverified.run_id,
        trade_date=trade_date,
        code="989104",
        executed_return=4.0,
    )
    db.add_all(
        [
            live,
            replay,
            incomplete_replay,
            unverified,
            live_rec,
            live_outcome,
            replay_rec,
            replay_outcome,
            incomplete_rec,
            incomplete_outcome,
            unverified_rec,
            unverified_outcome,
        ]
    )
    db.flush()
    invalidate_outcome_metrics_cache()

    live_metrics = overview_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=LIVE_FORWARD_SCOPE,
    )
    replay_metrics = overview_metrics(
        db,
        start_date=trade_date,
        end_date=trade_date,
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )

    assert live_metrics["total_recommendations"] == 1
    assert live_metrics["success_count"] == 1
    assert live_metrics["hit_rate"] == 1.0
    assert replay_metrics["total_recommendations"] == 1
    assert replay_metrics["failed_count"] == 1
    assert replay_metrics["hit_rate"] == 0.0


def test_metrics_scope_rejects_unknown_value_before_query(db):
    invalidate_outcome_metrics_cache()
    with pytest.raises(ValueError, match="unsupported metrics sample_scope"):
        overview_metrics(db, sample_scope="evaluation_scope_only")


def test_metrics_api_rejects_unknown_sample_scope():
    app = FastAPI()
    app.include_router(metrics_router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()

    with TestClient(app) as client:
        response = client.get("/api/metrics/overview?sample_scope=unknown")

    assert response.status_code == 422


def test_alpha_decay_cache_is_partitioned_by_sample_scope(monkeypatch):
    def scoped_rows(_db, *args, sample_scope=None, **kwargs):
        return [
            SimpleNamespace(
                entry_triggered=True,
                outcome_label="success",
                feature_snapshot_json="{}",
                executed_return=1.0,
                trade_date="2099-02-10",
            )
        ] if sample_scope == LIVE_FORWARD_SCOPE else []

    monkeypatch.setattr(
        "app.services.outcome_service._scoped_recommendation_rows",
        scoped_rows,
    )
    invalidate_outcome_metrics_cache()

    live_result = alpha_decay_detection(
        SimpleNamespace(),
        sample_scope=LIVE_FORWARD_SCOPE,
    )
    replay_result = alpha_decay_detection(
        SimpleNamespace(),
        sample_scope=HISTORICAL_REPLAY_SCOPE,
    )

    assert live_result["total"] == 1
    assert replay_result["total"] == 0

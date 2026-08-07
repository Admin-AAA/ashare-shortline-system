"""S1-a canonical evidence selectors and projection wiring.

**Validates: Requirements 2.5–2.9, 4.3, 4.8, 10.22–10.23**
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select

from app.models.tables import Recommendation, RecommendationRun
from app.services.evidence.identity import PROVENANCE_SCHEMA_VERSION, EvidenceClass
from app.services.evidence.selectors import (
    historical_reconstruction_run_conditions,
    is_live_forward_run,
    live_forward_run_conditions,
)
from app.services.recommendations.projection import (
    serialize_recommendation,
    serialize_run_summary,
)


def _complete_live_values(**overrides):
    values = {
        "evidence_class": EvidenceClass.LIVE_FORWARD.value,
        "actual_generated_at": datetime(2098, 1, 2, 2, 0),
        "data_as_of": "2098-01-02",
        "trade_date": "2098-01-02",
        "source_snapshot_id": "snapshot-selector-2098-01-02",
        "evidence_identity_hash": "d" * 64,
        "strategy_policy_fingerprint": "a" * 64,
        "execution_policy_fingerprint": "b" * 64,
        "code_revision": "c" * 40,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
    }
    values.update(overrides)
    return values


def _run(run_id: str, **overrides) -> RecommendationRun:
    values = {
        "run_id": run_id,
        "run_name": "selector fixture",
        "run_mode": "formal",
        "evaluation_scope": "live_forward",
        "status": "completed",
        **_complete_live_values(),
    }
    values.update(overrides)
    return RecommendationRun(**values)


def _recommendation(
    recommendation_id: str,
    run_id: str,
    *,
    trade_date: str = "2098-01-02",
    code: str = "989001",
    recommended_at: datetime | None = None,
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        run_id=run_id,
        trade_date=trade_date,
        recommended_at=recommended_at or datetime(2098, 1, 2, 2, 0),
        code=code,
        name="selector fixture",
        recommend_price=10.0,
        final_score=80.0,
    )


def test_row_selector_requires_complete_provenance_and_ignores_display_metadata():
    valid = _complete_live_values(
        run_name="REPLAY claims live",
        evaluation_scope="historical_replay",
        status="interrupted",
    )
    assert is_live_forward_run(valid)

    for missing in (
        "actual_generated_at",
        "data_as_of",
        "source_snapshot_id",
        "evidence_identity_hash",
        "strategy_policy_fingerprint",
        "execution_policy_fingerprint",
        "code_revision",
        "provenance_schema_version",
    ):
        candidate = dict(valid)
        candidate[missing] = None
        assert not is_live_forward_run(candidate), missing

    assert not is_live_forward_run({**valid, "data_as_of": "2098-01-03"})
    assert not is_live_forward_run(
        {**valid, "evidence_class": EvidenceClass.HISTORICAL_RECONSTRUCTION.value}
    )


def test_sql_live_selector_matches_exact_complete_provenance_population(db):
    valid = _run("run_s1_selector_valid")
    missing_snapshot = _run("run_s1_selector_missing_snapshot", source_snapshot_id=None)
    future_data = _run("run_s1_selector_future", data_as_of="2098-01-03")
    fake_live_metadata = _run(
        "run_s1_selector_fake_metadata",
        evidence_class=EvidenceClass.UNVERIFIED.value,
        run_name="LIVE LIVE LIVE",
        evaluation_scope="live_forward",
    )
    db.add_all([valid, missing_snapshot, future_data, fake_live_metadata])
    db.flush()

    selected = set(
        db.scalars(
            select(RecommendationRun.run_id).where(*live_forward_run_conditions())
        ).all()
    )

    assert "run_s1_selector_valid" in selected
    assert "run_s1_selector_missing_snapshot" not in selected
    assert "run_s1_selector_future" not in selected
    assert "run_s1_selector_fake_metadata" not in selected


def test_reconstruction_selector_requires_batch_complete_provenance_and_official_row(db):
    complete = _run(
        "run_s1_selector_reconstruction",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        evaluation_scope="historical_replay",
        actual_generated_at=datetime(2098, 1, 8, 2, 0),
        reconstruction_batch_id="batch-s1-selector",
    )
    missing_batch = _run(
        "run_s1_selector_reconstruction_missing_batch",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        reconstruction_batch_id=None,
    )
    complete_rec = _recommendation(
        "rec_s1_selector_reconstruction", complete.run_id
    )
    missing_rec = _recommendation(
        "rec_s1_selector_reconstruction_missing_batch", missing_batch.run_id
    )
    future_as_of = _run(
        "run_s1_selector_reconstruction_future_as_of",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        reconstruction_batch_id="batch-s1-selector-future",
        trade_date="2098-01-02",
        data_as_of="2098-01-03",
    )
    future_rec = _recommendation(
        "rec_s1_selector_reconstruction_future_as_of",
        future_as_of.run_id,
        trade_date="2098-01-03",
    )
    db.add_all([
        complete,
        missing_batch,
        future_as_of,
        complete_rec,
        missing_rec,
        future_rec,
    ])
    db.flush()

    selected = set(
        db.scalars(
            select(RecommendationRun.run_id)
            .join(Recommendation, Recommendation.run_id == RecommendationRun.run_id)
            .where(*historical_reconstruction_run_conditions())
        ).all()
    )

    assert complete.run_id in selected
    assert missing_batch.run_id not in selected
    assert future_as_of.run_id not in selected


def test_projection_serializes_evidence_identity_in_slim_full_and_run_summary():
    run = SimpleNamespace(
        run_id="run_s1_projection",
        run_name="projection fixture",
        preset_id=None,
        trade_date="2098-01-02",
        run_mode="formal",
        evaluation_scope="historical_replay",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        actual_generated_at=datetime(2098, 1, 8, 2, 3, 4, 567890),
        reconstruction_batch_id="batch-s1-projection",
        selected_skills_json="[]",
        result_summary_json="{}",
        status="completed",
    )
    rec = SimpleNamespace(
        recommendation_id="rec_s1_projection",
        run_id=run.run_id,
        trade_date="2098-01-02",
        recommended_at=datetime(2098, 1, 8, 2, 3, 4, 567890),
        code="989002",
        name="projection fixture",
        final_score=88.0,
        feature_snapshot_json="{}",
        market_context_json="{}",
        risk_tags_json="[]",
        info_tags_json="[]",
        agent_summary_json="{}",
        skill_breakdown_json="[]",
    )

    for slim in (False, True):
        payload = serialize_recommendation(rec, run=run, slim=slim)
        assert payload["evidence_class"] == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        assert payload["actual_generated_at"] == "2098-01-08T02:03:04.567890"
        assert payload["reconstruction_batch_id"] == "batch-s1-projection"

    summary = serialize_run_summary(
        run,
        preset=None,
        hit_rate=0.0,
        active_statuses=set(),
        heartbeat_stale_seconds=60,
    )
    assert summary["evidence_class"] == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    assert summary["actual_generated_at"] == "2098-01-08T02:03:04.567890"
    assert summary["reconstruction_batch_id"] == "batch-s1-projection"

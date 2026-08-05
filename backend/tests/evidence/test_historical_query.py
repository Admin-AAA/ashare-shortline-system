"""S1-a causal Historical_Query contract.

**Validates: Requirements 2.4, 2.7, 2.8, 2.10–2.11**
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.tables import (
    Recommendation,
    RecommendationEvidenceClassificationAudit,
    RecommendationOutcomeVersion,
    RecommendationRun,
)
from app.services.evidence.historical_query import (
    list_classification_audits_for_run,
    list_outcome_versions_for_recommendation,
    query_historical_records,
)
from app.services.evidence.identity import PROVENANCE_SCHEMA_VERSION, EvidenceClass


def _run(
    suffix: str,
    *,
    signal_date: str,
    evidence_class: EvidenceClass,
    data_as_of: str | None = None,
) -> RecommendationRun:
    reconstruction = evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION
    return RecommendationRun(
        run_id=f"run_s1_historical_{suffix}",
        run_name="historical query fixture",
        run_mode="formal",
        evaluation_scope="historical_replay" if reconstruction else "live_forward",
        evidence_class=evidence_class.value,
        actual_generated_at=datetime(2097, 2, 1, 2, 0),
        evidence_identity_hash=(suffix[0] * 64)[:64],
        strategy_policy_fingerprint="a" * 64,
        execution_policy_fingerprint="b" * 64,
        code_revision="c" * 40,
        reconstruction_batch_id=f"batch-{suffix}" if reconstruction else None,
        provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
        trade_date=signal_date,
        data_as_of=data_as_of or signal_date,
        source_snapshot_id=f"snapshot-{suffix}",
        status="completed",
    )


def _recommendation(
    suffix: str,
    run: RecommendationRun,
    *,
    trade_date: str,
    score: float,
) -> Recommendation:
    return Recommendation(
        recommendation_id=f"rec_s1_historical_{suffix}",
        run_id=run.run_id,
        trade_date=trade_date,
        recommended_at=datetime(2097, 2, 1, 2, 0),
        code=f"98{int(score):04d}"[-6:],
        name="historical query fixture",
        recommend_price=10.0,
        final_score=score,
    )


def _seed_mixed_history(db):
    unverified = _run(
        "unverified",
        signal_date="2097-01-03",
        evidence_class=EvidenceClass.UNVERIFIED,
    )
    live = _run(
        "live",
        signal_date="2097-01-02",
        evidence_class=EvidenceClass.LIVE_FORWARD,
    )
    reconstruction = _run(
        "reconstruction",
        signal_date="2097-01-01",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
    )
    violation = _run(
        "violation",
        signal_date="2097-01-04",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        data_as_of="2097-01-05",
    )
    recs = [
        _recommendation(
            "unverified", unverified, trade_date="2097-01-01", score=71.0
        ),
        _recommendation("live", live, trade_date="2097-01-03", score=82.0),
        _recommendation(
            "reconstruction", reconstruction, trade_date="2097-01-04", score=93.0
        ),
        _recommendation(
            "violation", violation, trade_date="2097-01-02", score=99.0
        ),
    ]
    db.add_all([unverified, live, reconstruction, violation, *recs])
    db.flush()
    return unverified, live, reconstruction, violation, recs


def test_historical_query_preserves_class_and_orders_by_requested_date_axis(db):
    unverified, live, reconstruction, _violation, _recs = _seed_mixed_history(db)

    by_signal = query_historical_records(
        db,
        date_from="2097-01-01",
        date_to="2097-01-04",
        date_axis="signal_date",
    )
    assert [record.run.run_id for record in by_signal.records] == [
        unverified.run_id,
        live.run_id,
        reconstruction.run_id,
    ]
    assert [record.evidence_class for record in by_signal.records] == [
        EvidenceClass.UNVERIFIED.value,
        EvidenceClass.LIVE_FORWARD.value,
        EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
    ]
    assert by_signal.excluded_as_of_violations == 1

    by_trade = query_historical_records(
        db,
        date_from="2097-01-01",
        date_to="2097-01-04",
        date_axis="trade_date",
    )
    assert [record.run.run_id for record in by_trade.records] == [
        reconstruction.run_id,
        live.run_id,
        unverified.run_id,
    ]
    assert by_trade.excluded_as_of_violations == 1


def test_historical_query_supports_independent_exact_signal_and_trade_dates(db):
    _unverified, live, _reconstruction, _violation, _recs = _seed_mixed_history(db)

    result = query_historical_records(
        db,
        signal_date="2097-01-02",
        trade_date="2097-01-03",
    )

    assert [record.run.run_id for record in result.records] == [live.run_id]
    assert result.excluded_as_of_violations == 0


@pytest.mark.parametrize("date_axis", ["generated_at", "unknown", ""])
def test_historical_query_rejects_unknown_date_axis_before_io(db, date_axis):
    with pytest.raises(ValueError, match="date_axis"):
        query_historical_records(db, date_axis=date_axis)


def test_as_of_violation_is_reported_even_when_live_claim_has_complete_provenance(db):
    violating_live = _run(
        "livefuture",
        signal_date="2097-01-06",
        evidence_class=EvidenceClass.LIVE_FORWARD,
        data_as_of="2097-01-07",
    )
    rec = _recommendation(
        "livefuture", violating_live, trade_date="2097-01-06", score=88.0
    )
    db.add_all([violating_live, rec])
    db.flush()

    result = query_historical_records(db, signal_date="2097-01-06")

    assert result.records == ()
    assert result.excluded_as_of_violations == 1


def test_evidence_class_filter_applies_equally_to_rows_and_exclusion_count(db):
    _unverified, _live, reconstruction, _violation, _recs = _seed_mixed_history(db)

    result = query_historical_records(
        db,
        date_from="2097-01-01",
        date_to="2097-01-04",
        evidence_classes=[EvidenceClass.HISTORICAL_RECONSTRUCTION.value],
    )

    assert [record.run.run_id for record in result.records] == [
        reconstruction.run_id
    ]
    assert result.excluded_as_of_violations == 1


def test_historical_query_is_read_only_and_preserves_original_timestamps(db):
    _unverified, _live, reconstruction, _violation, recs = _seed_mixed_history(db)
    target = next(rec for rec in recs if rec.run_id == reconstruction.run_id)
    before = (
        reconstruction.started_at,
        reconstruction.actual_generated_at,
        reconstruction.evaluation_scope,
        target.recommended_at,
        target.trade_date,
    )

    query_historical_records(db, signal_date=reconstruction.trade_date)

    after = (
        reconstruction.started_at,
        reconstruction.actual_generated_at,
        reconstruction.evaluation_scope,
        target.recommended_at,
        target.trade_date,
    )
    assert after == before


def test_related_classification_audits_and_outcome_versions_remain_ordered(db):
    run = _run(
        "related",
        signal_date="2097-01-08",
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
    )
    rec = _recommendation("related", run, trade_date="2097-01-08", score=85.0)
    base_time = datetime(2097, 2, 2, 1, 0)
    audits = [
        RecommendationEvidenceClassificationAudit(
            plan_hash="e" * 64,
            run_id=run.run_id,
            planned_evidence_class=EvidenceClass.UNVERIFIED.value,
            classification_reason="legacy_source_unproven",
            legacy_evaluation_scope="historical_replay",
            legacy_trade_date=run.trade_date,
            recommendation_count=1,
            outcome_count=2,
            planned_at=base_time + timedelta(minutes=2),
        ),
        RecommendationEvidenceClassificationAudit(
            plan_hash="d" * 64,
            run_id=run.run_id,
            planned_evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
            classification_reason="trusted_historical_replay_entrypoint",
            legacy_evaluation_scope="historical_replay",
            legacy_trade_date=run.trade_date,
            recommendation_count=1,
            outcome_count=2,
            planned_at=base_time + timedelta(minutes=1),
        ),
    ]
    versions = [
        RecommendationOutcomeVersion(
            version_id="outcome-s1-related-v2",
            recommendation_id=rec.recommendation_id,
            calculation_version="v2",
            entry_policy_id="entry-v1",
            horizon_days=5,
            label_source="auto",
            result_json="{}",
            computed_at=base_time + timedelta(minutes=2),
        ),
        RecommendationOutcomeVersion(
            version_id="outcome-s1-related-v1",
            recommendation_id=rec.recommendation_id,
            calculation_version="v1",
            entry_policy_id="entry-v1",
            horizon_days=5,
            label_source="auto",
            result_json="{}",
            computed_at=base_time + timedelta(minutes=1),
        ),
    ]
    db.add_all([run, rec, *audits, *versions])
    db.flush()

    assert [row.plan_hash for row in list_classification_audits_for_run(db, run.run_id)] == [
        "d" * 64,
        "e" * 64,
    ]
    assert [
        row.version_id
        for row in list_outcome_versions_for_recommendation(db, rec.recommendation_id)
    ] == ["outcome-s1-related-v1", "outcome-s1-related-v2"]

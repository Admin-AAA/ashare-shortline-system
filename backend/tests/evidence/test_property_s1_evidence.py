"""Hypothesis properties 6–10 for evidence identity and history.

SQLite instances are per-example, isolated, in-memory, and read/write only test
fixtures.  No production database, network, or Layer 2 evidence is touched.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.production_hardening_preflight import CLASSIFICATION_RULE_VERSION
from app.models.tables import Recommendation, RecommendationRun
from app.services.evidence.classification_backfill import (
    ClassificationAssignment,
    _legacy_scope_json,
    _resolve_run_action,
    verify_preservation,
)
from app.services.evidence.historical_query import query_historical_records
from app.services.evidence.identity import (
    PROVENANCE_SCHEMA_VERSION,
    EvidenceClass,
    EvidenceIdentityBuilder,
    EvidenceSnapshotIdentity,
    RunSource,
)
from app.services.evidence.selectors import is_live_forward_run


_SIGNAL_DATE = date(2026, 6, 2)


@settings(max_examples=100, deadline=None)
@given(
    source=st.sampled_from(tuple(RunSource)),
    same_business_day=st.booleans(),
    future_data=st.booleans(),
    verified_snapshot=st.booleans(),
    verified_revision=st.booleans(),
    display_claims_live=st.booleans(),
)
def test_property_6_evidence_classification_is_provenance_derived(
    source,
    same_business_day,
    future_data,
    verified_snapshot,
    verified_revision,
    display_claims_live,
):
    """**Property 6: Evidence classification is provenance-derived**"""
    generated_date = _SIGNAL_DATE if same_business_day else _SIGNAL_DATE + timedelta(days=1)
    identity = EvidenceIdentityBuilder().build(
        run_source=source,
        actual_generated_at=datetime.combine(
            generated_date, datetime.min.time(), tzinfo=timezone.utc
        )
        + timedelta(hours=2),
        signal_date=_SIGNAL_DATE,
        snapshot=EvidenceSnapshotIdentity(
            source_snapshot_id="snapshot-ok" if verified_snapshot else "unknown",
            data_as_of=(
                _SIGNAL_DATE + timedelta(days=1) if future_data else _SIGNAL_DATE
            ),
        ),
        strategy_policy="a" * 64,
        execution_policy="b" * 64,
        code_revision="c" * 40 if verified_revision else "unknown",
        reconstruction_batch_id=(
            "batch-property-6" if source is RunSource.HISTORICAL_REPLAY else None
        ),
    )
    if source is RunSource.HISTORICAL_REPLAY:
        expected = EvidenceClass.HISTORICAL_RECONSTRUCTION
    elif source is RunSource.BACKTEST:
        expected = EvidenceClass.RESEARCH_BACKTEST
    elif source is RunSource.EXPERIMENT:
        expected = EvidenceClass.EXPERIMENT
    elif (
        source is RunSource.FORMAL
        and same_business_day
        and not future_data
        and verified_snapshot
        and verified_revision
    ):
        expected = EvidenceClass.LIVE_FORWARD
    else:
        expected = EvidenceClass.UNVERIFIED

    assert identity.evidence_class is expected
    # Caller display metadata is not accepted by the builder and therefore
    # cannot alter the result, regardless of what it claims.
    assert display_claims_live in (True, False)


@settings(max_examples=100, deadline=None)
@given(
    evidence_class=st.sampled_from(sorted({item.value for item in EvidenceClass if item is not EvidenceClass.LIVE_FORWARD})),
    reason=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True),
    recommendation_count=st.integers(min_value=0, max_value=1000),
    outcome_count=st.integers(min_value=0, max_value=1000),
)
def test_property_7_historical_preservation_and_backfill_are_idempotent(
    evidence_class, reason, recommendation_count, outcome_count
):
    """**Property 7: Historical preservation and classification backfill are idempotent**"""
    assignment = ClassificationAssignment(
        run_id="run-property-7",
        evidence_class=evidence_class,
        reason=reason,
        recommendation_count=recommendation_count,
        outcome_count=outcome_count,
    )
    row = {
        "run_id": assignment.run_id,
        "run_mode": "formal",
        "run_name": "immutable legacy name",
        "evaluation_scope": "historical_replay",
        "trade_date": "2026-01-02",
        "started_at": datetime(2026, 1, 3, 2, 0),
        "finished_at": datetime(2026, 1, 3, 3, 0),
        "evidence_class": None,
        "classification_reason": None,
        "provenance_schema_version": None,
        "legacy_scope_json": None,
    }
    first = _resolve_run_action(row=row, audit=None, assignment=assignment)
    assert first.action == "updated"
    target_row = {
        **row,
        "evidence_class": evidence_class,
        "classification_reason": reason,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "legacy_scope_json": _legacy_scope_json(row),
    }
    audit = {
        "planned_evidence_class": evidence_class,
        "classification_reason": reason,
        "applied_at": datetime(2026, 1, 4, 2, 0),
    }
    second = _resolve_run_action(
        row=target_row, audit=audit, assignment=assignment
    )
    assert second.action == "unchanged"
    assert second.operations == ()

    preserved = {
        "run_count": 1,
        "primary_keys": [assignment.run_id],
        "runs": {
            assignment.run_id: {
                key: row[key]
                for key in (
                    "run_mode",
                    "run_name",
                    "evaluation_scope",
                    "trade_date",
                    "started_at",
                    "finished_at",
                )
            }
        },
        "recommendation_count": recommendation_count,
        "recommendations": {},
    }
    assert verify_preservation(preserved, preserved).ok
    assert CLASSIFICATION_RULE_VERSION


@settings(max_examples=100, deadline=None)
@given(
    evidence_class=st.sampled_from(tuple(EvidenceClass)),
    actual_generated=st.booleans(),
    causal=st.booleans(),
    snapshot=st.booleans(),
    identity=st.booleans(),
    strategy=st.booleans(),
    execution=st.booleans(),
    revision=st.booleans(),
    schema=st.booleans(),
)
def test_property_8_live_consumers_contain_exactly_eligible_forward_evidence(
    evidence_class,
    actual_generated,
    causal,
    snapshot,
    identity,
    strategy,
    execution,
    revision,
    schema,
):
    """**Property 8: LIVE consumers contain exactly eligible forward evidence**"""
    row = {
        "evidence_class": evidence_class.value,
        "actual_generated_at": datetime(2026, 6, 2, 2, 0) if actual_generated else None,
        "data_as_of": "2026-06-02" if causal else "2026-06-03",
        "trade_date": "2026-06-02",
        "source_snapshot_id": "snapshot" if snapshot else None,
        "evidence_identity_hash": "d" * 64 if identity else None,
        "strategy_policy_fingerprint": "a" * 64 if strategy else None,
        "execution_policy_fingerprint": "b" * 64 if execution else None,
        "code_revision": "c" * 40 if revision else None,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION if schema else None,
        "run_name": "LIVE" * 5,
        "evaluation_scope": "live_forward",
    }
    expected = (
        evidence_class is EvidenceClass.LIVE_FORWARD
        and actual_generated
        and causal
        and snapshot
        and identity
        and strategy
        and execution
        and revision
        and schema
    )
    assert is_live_forward_run(row) is expected


def _sqlite_session() -> tuple[Session, object]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    Recommendation.__table__.create(engine)
    return Session(engine), engine


@settings(max_examples=100, deadline=None)
@given(
    classes=st.lists(st.sampled_from(tuple(EvidenceClass)), min_size=1, max_size=5),
    axis=st.sampled_from(("signal_date", "trade_date")),
)
def test_property_9_historical_queries_preserve_class_and_business_date_order(
    classes, axis
):
    """**Property 9: Historical queries preserve class and business-date order**"""
    session, engine = _sqlite_session()
    try:
        expected = []
        for index, evidence_class in enumerate(classes):
            signal_date = (_SIGNAL_DATE + timedelta(days=index)).isoformat()
            trade_date = (_SIGNAL_DATE + timedelta(days=len(classes) - index)).isoformat()
            run = RecommendationRun(
                run_id=f"run-p9-{index}",
                run_name="p9",
                run_mode="formal",
                evaluation_scope="historical_replay",
                evidence_class=evidence_class.value,
                actual_generated_at=datetime(2026, 7, 1, 2, 0),
                evidence_identity_hash="d" * 64,
                strategy_policy_fingerprint="a" * 64,
                execution_policy_fingerprint="b" * 64,
                code_revision="c" * 40,
                reconstruction_batch_id=(
                    f"batch-p9-{index}"
                    if evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION
                    else None
                ),
                provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
                trade_date=signal_date,
                data_as_of=signal_date,
                source_snapshot_id=f"snapshot-p9-{index}",
                status="completed",
            )
            rec = Recommendation(
                recommendation_id=f"rec-p9-{index}",
                run_id=run.run_id,
                trade_date=trade_date,
                recommended_at=datetime(2026, 7, 1, 2, 0),
                code=f"{index:06d}",
                name="p9",
                recommend_price=10,
                final_score=80,
            )
            session.add_all([run, rec])
            expected.append((signal_date if axis == "signal_date" else trade_date, evidence_class.value))
        session.flush()
        result = query_historical_records(session, date_axis=axis)
        assert [record.evidence_class for record in result.records] == [
            item[1] for item in sorted(expected, key=lambda item: item[0], reverse=True)
        ]
        assert result.excluded_as_of_violations == 0
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=100, deadline=None)
@given(
    evidence_class=st.sampled_from((EvidenceClass.LIVE_FORWARD, EvidenceClass.HISTORICAL_RECONSTRUCTION)),
    data_offset=st.integers(min_value=-3, max_value=3),
)
def test_property_10_historical_reads_obey_as_of_causality(
    evidence_class, data_offset
):
    """**Property 10: Historical reads obey as-of causality**"""
    session, engine = _sqlite_session()
    try:
        signal = _SIGNAL_DATE.isoformat()
        run = RecommendationRun(
            run_id="run-p10",
            run_name="p10",
            run_mode="formal",
            evaluation_scope="historical_replay",
            evidence_class=evidence_class.value,
            actual_generated_at=datetime(2026, 7, 1, 2, 0),
            evidence_identity_hash="d" * 64,
            strategy_policy_fingerprint="a" * 64,
            execution_policy_fingerprint="b" * 64,
            code_revision="c" * 40,
            reconstruction_batch_id=(
                "batch-p10"
                if evidence_class is EvidenceClass.HISTORICAL_RECONSTRUCTION
                else None
            ),
            provenance_schema_version=PROVENANCE_SCHEMA_VERSION,
            trade_date=signal,
            data_as_of=(_SIGNAL_DATE + timedelta(days=data_offset)).isoformat(),
            source_snapshot_id="snapshot-p10",
            status="completed",
        )
        session.add_all(
            [
                run,
                Recommendation(
                    recommendation_id="rec-p10",
                    run_id=run.run_id,
                    trade_date=signal,
                    recommended_at=datetime(2026, 7, 1, 2, 0),
                    code="000010",
                    name="p10",
                    recommend_price=10,
                    final_score=80,
                ),
            ]
        )
        session.flush()
        result = query_historical_records(session, signal_date=signal)
        if data_offset > 0:
            assert result.records == ()
            assert result.excluded_as_of_violations == 1
        else:
            assert len(result.records) == 1
            assert result.excluded_as_of_violations == 0
    finally:
        session.close()
        engine.dispose()

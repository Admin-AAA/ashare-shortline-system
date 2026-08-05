"""S1-c reviewed classification backfill executor and verifier.

All write-path tests run inside the repository's rollback-only pytest
transaction.  They are Layer 1 contract tests, never task 9.4 authorization.

**Validates: Requirements 2.4, 2.9–2.11, 10.21**
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import select, text

from app.db.production_hardening_preflight import CLASSIFICATION_RULE_VERSION
from app.models.tables import (
    Recommendation,
    RecommendationEvidenceClassificationAudit,
    RecommendationOutcome,
    RecommendationRun,
)
from app.services.evidence.classification_backfill import (
    ALLOWED_BACKFILL_EVIDENCE_CLASSES,
    REVIEWED_CLASSIFICATION_PLAN_HASH,
    RUN_WRITE_ALLOWLIST,
    ClassificationBackfillError,
    canonical_plan_hash,
    capture_preservation_snapshot,
    execute_backfill,
    load_and_verify_plan,
    verify_preservation,
)
from app.services.evidence.identity import PROVENANCE_SCHEMA_VERSION, EvidenceClass
from scripts.verify_classification_backfill import verify_classification_backfill


def _seed_legacy_run(
    db,
    suffix: str,
    *,
    recommendation_count: int = 1,
    outcome_count: int = 1,
    provenance: dict | None = None,
):
    run_id = f"run_s1_backfill_{suffix}"
    run = RecommendationRun(
        run_id=run_id,
        run_name=f"legacy payload {suffix}",
        run_mode="formal",
        evaluation_scope="historical_replay",
        trade_date="2096-01-02",
        started_at=datetime(2096, 1, 3, 2, 3, 4, 567890),
        finished_at=datetime(2096, 1, 3, 2, 4, 5, 678901),
        params_json=json.dumps({"immutable": suffix}, sort_keys=True),
        market_snapshot_json=json.dumps({"snapshot": suffix}, sort_keys=True),
        result_summary_json=json.dumps({"result": suffix}, sort_keys=True),
        filter_audit_json=json.dumps({"audit": suffix}, sort_keys=True),
        status="completed",
        **(provenance or {}),
    )
    recs = []
    outcomes = []
    for index in range(recommendation_count):
        rec = Recommendation(
            recommendation_id=f"rec_s1_backfill_{suffix}_{index}",
            run_id=run_id,
            trade_date=run.trade_date,
            recommended_at=datetime(2096, 1, 3, 2, 5, index),
            code=f"97{index:04d}",
            name="backfill fixture",
            recommend_price=10.0,
            final_score=80.0 + index,
            feature_snapshot_json=json.dumps({"feature": suffix, "index": index}),
        )
        recs.append(rec)
        if index < outcome_count:
            outcomes.append(
                RecommendationOutcome(
                    recommendation_id=rec.recommendation_id,
                    entry_triggered=True,
                    executed_return=1.0,
                    outcome_label="success",
                )
            )
    db.add_all([run, *recs, *outcomes])
    db.flush()
    return run, recs, outcomes


def _assignment(
    run_id: str,
    *,
    evidence_class: str = EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
    reason: str = "known_replay_source",
    recommendation_count: int = 1,
    outcome_count: int = 1,
):
    return {
        "run_id": run_id,
        "evidence_class": evidence_class,
        "reason": reason,
        "recommendation_count": recommendation_count,
        "outcome_count": outcome_count,
    }


def _payload(assignments):
    assignments = list(assignments)
    return {
        "rule_version": CLASSIFICATION_RULE_VERSION,
        "assignments": assignments,
        "live_forward_assignments": 0,
        "plan_hash": canonical_plan_hash(CLASSIFICATION_RULE_VERSION, assignments),
        "write_authorized": False,
        "write_operations_executed": 0,
    }


def _verified(assignments):
    payload = _payload(assignments)
    return load_and_verify_plan(
        payload,
        expected_plan_hash=payload["plan_hash"],
    )


def test_reviewed_plan_hash_is_fixed_and_allowlist_excludes_original_fields():
    assert REVIEWED_CLASSIFICATION_PLAN_HASH == (
        "1e2255b4daf2ed18559f7911ff2aabeb849471ed6ea0b46153c723cc111e38e8"
    )
    assert RUN_WRITE_ALLOWLIST == (
        "evidence_class",
        "classification_reason",
        "provenance_schema_version",
        "legacy_scope_json",
    )
    assert "live_forward" not in ALLOWED_BACKFILL_EVIDENCE_CLASSES
    assert not {
        "run_mode",
        "run_name",
        "evaluation_scope",
        "trade_date",
        "started_at",
        "finished_at",
        "params_json",
        "result_summary_json",
    }.intersection(RUN_WRITE_ALLOWLIST)


def test_plan_loader_rehashes_and_rejects_tampering_duplicates_and_live_promotion():
    assignment = _assignment("run_plan_contract")
    payload = _payload([assignment])
    plan = load_and_verify_plan(payload, expected_plan_hash=payload["plan_hash"])
    assert plan.assignments[0].run_id == "run_plan_contract"

    tampered = {**payload, "assignments": [{**assignment, "reason": "tampered"}]}
    with pytest.raises(ClassificationBackfillError) as mismatch:
        load_and_verify_plan(tampered, expected_plan_hash=payload["plan_hash"])
    assert mismatch.value.code == "classification_plan_hash_mismatch"

    duplicate_payload = _payload([assignment, assignment])
    with pytest.raises(ClassificationBackfillError) as duplicate:
        load_and_verify_plan(
            duplicate_payload,
            expected_plan_hash=duplicate_payload["plan_hash"],
        )
    assert duplicate.value.code == "classification_plan_invalid"

    live_payload = _payload(
        [_assignment("run_live_forbidden", evidence_class="live_forward")]
    )
    with pytest.raises(ClassificationBackfillError) as live:
        load_and_verify_plan(
            live_payload,
            expected_plan_hash=live_payload["plan_hash"],
        )
    assert live.value.code == "classification_plan_invariant_violation"


def test_default_dry_run_reports_exact_decision_and_performs_zero_writes(db):
    run, _recs, _outcomes = _seed_legacy_run(db, "dry_run")
    plan = _verified([_assignment(run.run_id)])
    before = capture_preservation_snapshot(db)

    report = execute_backfill(db, plan)

    assert report.dry_run is True
    assert report.write_authorized is False
    assert report.write_operations_executed == 0
    assert report.updated == 1
    assert report.invariants["write_set_allowlist_only"] is True
    assert capture_preservation_snapshot(db) == before
    db.expire_all()
    persisted = db.get(RecommendationRun, run.run_id)
    assert persisted.evidence_class is None
    assert db.get(
        RecommendationEvidenceClassificationAudit,
        (plan.plan_hash, run.run_id),
    ) is None


def test_rollback_transaction_write_path_changes_only_allowlist_and_is_second_run_noop(db):
    run, recs, _outcomes = _seed_legacy_run(db, "write_noop")
    plan = _verified([_assignment(run.run_id)])
    original_run = db.execute(
        text(
            "SELECT run_mode, run_name, evaluation_scope, trade_date, started_at, "
            "finished_at, params_json, market_snapshot_json, result_summary_json, "
            "filter_audit_json FROM recommendation_runs WHERE run_id=:run_id"
        ),
        {"run_id": run.run_id},
    ).mappings().one()
    original_rec = db.execute(
        text(
            "SELECT trade_date, recommended_at, feature_snapshot_json "
            "FROM recommendations WHERE recommendation_id=:recommendation_id"
        ),
        {"recommendation_id": recs[0].recommendation_id},
    ).mappings().one()

    first = execute_backfill(
        db,
        plan,
        dry_run=False,
        now=datetime(2096, 2, 1, 1, 2, 3, 456789),
    )
    db.expire_all()
    persisted = db.get(RecommendationRun, run.run_id)
    audit = db.get(
        RecommendationEvidenceClassificationAudit,
        (plan.plan_hash, run.run_id),
    )
    assert first.updated == 1
    assert first.write_operations_executed == 2
    assert persisted.evidence_class == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    assert persisted.classification_reason == "known_replay_source"
    assert persisted.provenance_schema_version == PROVENANCE_SCHEMA_VERSION
    assert json.loads(persisted.legacy_scope_json) == {
        "evaluation_scope": "historical_replay",
        "run_mode": "formal",
        "run_name": "legacy payload write_noop",
        "started_at": original_run["started_at"].isoformat(
            sep=" ", timespec="microseconds"
        ),
        "trade_date": "2096-01-02",
    }
    assert audit is not None
    assert audit.reviewed_at is None
    assert audit.applied_at == datetime(2096, 2, 1, 1, 2, 3, 456789)

    preserved_run = db.execute(
        text(
            "SELECT run_mode, run_name, evaluation_scope, trade_date, started_at, "
            "finished_at, params_json, market_snapshot_json, result_summary_json, "
            "filter_audit_json FROM recommendation_runs WHERE run_id=:run_id"
        ),
        {"run_id": run.run_id},
    ).mappings().one()
    preserved_rec = db.execute(
        text(
            "SELECT trade_date, recommended_at, feature_snapshot_json "
            "FROM recommendations WHERE recommendation_id=:recommendation_id"
        ),
        {"recommendation_id": recs[0].recommendation_id},
    ).mappings().one()
    assert dict(preserved_run) == dict(original_run)
    assert dict(preserved_rec) == dict(original_rec)

    second = execute_backfill(
        db,
        plan,
        dry_run=False,
        now=datetime(2096, 2, 2, 1, 2, 3),
    )
    db.expire_all()
    audit_after = db.get(
        RecommendationEvidenceClassificationAudit,
        (plan.plan_hash, run.run_id),
    )
    assert second.updated == 0
    assert second.unchanged == 1
    assert second.write_operations_executed == 0
    assert audit_after.applied_at == datetime(2096, 2, 1, 1, 2, 3, 456789)


def test_complete_provenance_is_never_overwritten_without_matching_audit(db):
    existing = {
        "evidence_class": EvidenceClass.RESEARCH_BACKTEST.value,
        "classification_reason": "owned_by_other_writer",
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "legacy_scope_json": "{}",
    }
    run, _recs, _outcomes = _seed_legacy_run(
        db, "already_complete", provenance=existing
    )
    plan = _verified([_assignment(run.run_id)])

    report = execute_backfill(db, plan, dry_run=False)

    assert report.skipped_provenance_complete == 1
    assert report.write_operations_executed == 0
    db.expire_all()
    assert db.get(RecommendationRun, run.run_id).classification_reason == (
        "owned_by_other_writer"
    )


def test_partial_provenance_and_plan_count_drift_fail_closed(db):
    partial, _recs, _outcomes = _seed_legacy_run(
        db,
        "partial",
        provenance={"evidence_class": EvidenceClass.UNVERIFIED.value},
    )
    with pytest.raises(ClassificationBackfillError) as conflict:
        execute_backfill(db, _verified([_assignment(partial.run_id)]))
    assert conflict.value.code == "classification_provenance_conflict"

    drifted, _recs, _outcomes = _seed_legacy_run(db, "drift")
    with pytest.raises(ClassificationBackfillError) as drift:
        execute_backfill(
            db,
            _verified(
                [
                    _assignment(
                        drifted.run_id,
                        recommendation_count=2,
                        outcome_count=1,
                    )
                ]
            ),
        )
    assert drift.value.code == "classification_plan_drift"


def test_preservation_checker_detects_primary_key_time_scope_and_recommendation_drift():
    before = {
        "run_count": 1,
        "primary_keys": ["run-a"],
        "runs": {
            "run-a": {
                "run_mode": "formal",
                "run_name": "original",
                "evaluation_scope": "historical_replay",
                "trade_date": "2096-01-01",
                "started_at": "start",
                "finished_at": "finish",
            }
        },
        "recommendation_count": 1,
        "recommendations": {
            "rec-a": {"run_id": "run-a", "recommended_at": "original"}
        },
    }
    after = json.loads(json.dumps(before))
    after["runs"]["run-a"]["evaluation_scope"] = "live_forward"
    after["runs"]["run-a"]["started_at"] = "rewritten"
    after["recommendations"]["rec-a"]["recommended_at"] = "rewritten"

    report = verify_preservation(before, after)

    assert not report.ok
    assert not report.checks["run_identity_fields_unchanged"]
    assert not report.checks["run_original_time_fields_unchanged"]
    assert not report.checks["recommendations_unchanged"]


def test_verify_script_report_is_layer1_read_only_and_lists_unverified(db):
    run, _recs, _outcomes = _seed_legacy_run(db, "verify_script")
    assignment = _assignment(
        run.run_id,
        evidence_class=EvidenceClass.UNVERIFIED.value,
        reason="insufficient_immutable_provenance",
    )
    payload = _payload([assignment])

    result = verify_classification_backfill(
        db,
        expected_plan_hash=payload["plan_hash"],
        plan_payload=payload,
    )

    assert result["status"] == "verified_read_only_dry_run"
    assert result["layer"] == "LAYER_1_ONLY"
    assert result["environment_acceptance_state"] == "BLOCKED"
    assert result["write_authorized"] is False
    assert result["write_operations_executed"] == 0
    assert result["unverified_run_ids"] == [run.run_id]
    assert run.run_id in result["run_primary_keys"]
    expected = result["before_after_expected"]
    assert expected["run_count_before"] == expected["run_count_after"]
    assert expected["recommendation_count_before"] == expected[
        "recommendation_count_after"
    ]
    assert expected["run_primary_key_digest_before"] == expected[
        "run_primary_key_digest_after"
    ]
    db.expire_all()
    assert db.get(RecommendationRun, run.run_id).evidence_class is None


def test_applied_audit_conflict_is_rejected_instead_of_overwritten(db):
    run, _recs, _outcomes = _seed_legacy_run(db, "audit_conflict")
    plan = _verified([_assignment(run.run_id)])
    db.add(
        RecommendationEvidenceClassificationAudit(
            plan_hash=plan.plan_hash,
            run_id=run.run_id,
            planned_evidence_class=EvidenceClass.UNVERIFIED.value,
            classification_reason="different_reviewed_fact",
            legacy_evaluation_scope=run.evaluation_scope,
            legacy_started_at=run.started_at,
            legacy_trade_date=run.trade_date,
            recommendation_count=1,
            outcome_count=1,
            planned_at=datetime(2096, 1, 10),
            applied_at=datetime(2096, 1, 11),
        )
    )
    db.flush()

    with pytest.raises(ClassificationBackfillError) as conflict:
        execute_backfill(db, plan, dry_run=False)
    assert conflict.value.code == "classification_provenance_conflict"

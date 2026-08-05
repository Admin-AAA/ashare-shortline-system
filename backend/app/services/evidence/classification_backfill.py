"""Offline executor for the reviewed legacy classification plan (task 1.4).

This module is a pure domain boundary: SQLAlchemy is used for typed table
access, but there are no network, CLI, scheduler, or vendor imports, and
nothing here is wired into a production entry point.  The executor consumes
only plans that :func:`load_and_verify_plan` has re-hashed with the exact
canonical algorithm of ``app.db.production_hardening_preflight`` and matched
against the reviewed plan hash; every mismatch fails closed.

Real backfill against production is decided by task 9.4 under the frozen
release scope and a dedicated ``Separate_Write_Approval`` (Requirements 2.10,
10.21).  This module never grants that approval and is never invoked
automatically.

Write contract (Requirements 2.4, 2.10, 2.11):

* ``recommendation_runs`` rows may only receive the four allowlisted
  provenance columns ``evidence_class``, ``classification_reason``,
  ``provenance_schema_version`` and ``legacy_scope_json``.
* ``recommendation_evidence_classification_audits`` rows are append-only
  facts keyed by ``(plan_hash, run_id)``; the executor fills ``planned_at`` /
  ``applied_at`` and never ``reviewed_at`` — an independent authorization
  flow owns the review timestamp.
* Original identity/time fields (``run_mode``, ``run_name``,
  ``evaluation_scope``, ``trade_date``, ``started_at``, ``finished_at`` and
  recommendation ``recommended_at``) are never modified, no payload column is
  touched, and nothing is ever deleted.
* A second execution over the same ``(plan_hash, run_id)`` is a strict no-op.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.db.production_hardening_preflight import (
    CLASSIFICATION_RULE_VERSION,
    LegacyRunFacts,
    assert_read_only_statement,
    classify_legacy_run,
)
from app.services.evidence.identity import PROVENANCE_SCHEMA_VERSION


REVIEWED_CLASSIFICATION_PLAN_HASH = (
    "1e2255b4daf2ed18559f7911ff2aabeb849471ed6ea0b46153c723cc111e38e8"
)
"""Task 0.3 read-only classification plan approved for task 1.4."""

ALLOWED_BACKFILL_EVIDENCE_CLASSES = frozenset(
    {
        "research_backtest",
        "experiment",
        "historical_reconstruction",
        "unverified",
    }
)
"""Legacy classes the reviewed plan may assign; ``live_forward`` is impossible."""

RUN_WRITE_ALLOWLIST = (
    "evidence_class",
    "classification_reason",
    "provenance_schema_version",
    "legacy_scope_json",
)
"""The only ``recommendation_runs`` columns the executor may write."""

PRESERVED_RUN_IDENTITY_FIELDS = ("run_mode", "run_name", "evaluation_scope")
PRESERVED_RUN_TIME_FIELDS = ("trade_date", "started_at", "finished_at")

_PLAN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Read-only statements mirroring collect_classification_plan.  Task 9.4 uses
# them to re-derive the reviewed plan from live data instead of trusting a
# payload file; the preflight module itself stays untouched.
_RUN_FACTS_STATEMENT = """
    SELECT run_id, run_mode, evaluation_scope, run_name, trade_date, started_at
    FROM recommendation_runs
    ORDER BY run_id
"""
_RECOMMENDATION_COUNT_STATEMENT = """
    SELECT run_id AS group_key, COUNT(*) AS row_count
    FROM recommendations
    GROUP BY run_id
"""
_OUTCOME_COUNT_STATEMENT = """
    SELECT recommendations.run_id AS group_key, COUNT(*) AS row_count
    FROM recommendations
    INNER JOIN recommendation_outcomes
        ON recommendation_outcomes.recommendation_id = recommendations.recommendation_id
    GROUP BY recommendations.run_id
"""
_RUN_ROW_STATEMENT = """
    SELECT run_id, run_mode, run_name, evaluation_scope, trade_date, started_at,
           finished_at, evidence_class, classification_reason,
           provenance_schema_version, legacy_scope_json,
           actual_generated_at, data_as_of, source_snapshot_id,
           evidence_identity_hash, strategy_policy_fingerprint,
           execution_policy_fingerprint, code_revision, reconstruction_batch_id
    FROM recommendation_runs
    WHERE run_id = :run_id
"""
_AUDIT_ROW_STATEMENT = """
    SELECT plan_hash, run_id, planned_evidence_class, classification_reason,
           reviewed_at, applied_at
    FROM recommendation_evidence_classification_audits
    WHERE plan_hash = :plan_hash AND run_id = :run_id
"""
_RECOMMENDATION_STATS_STATEMENT = """
    SELECT COUNT(*) AS recommendation_count,
           MIN(recommended_at) AS recommended_at_min,
           MAX(recommended_at) AS recommended_at_max
    FROM recommendations
    WHERE run_id = :run_id
"""
_OUTCOME_COUNT_FOR_RUN_STATEMENT = """
    SELECT COUNT(*) AS outcome_count
    FROM recommendations
    INNER JOIN recommendation_outcomes
        ON recommendation_outcomes.recommendation_id = recommendations.recommendation_id
    WHERE recommendations.run_id = :run_id
"""
_PRESERVATION_RUNS_STATEMENT = """
    SELECT run_id, run_mode, run_name, evaluation_scope, trade_date, started_at,
           finished_at
    FROM recommendation_runs
    ORDER BY run_id
"""
_PRESERVATION_RECOMMENDATIONS_STATEMENT = """
    SELECT recommendation_id, run_id, recommended_at
    FROM recommendations
    ORDER BY recommendation_id
"""

_UPDATE_RUN_STATEMENT = """
    UPDATE recommendation_runs
    SET evidence_class = :evidence_class,
        classification_reason = :classification_reason,
        provenance_schema_version = :provenance_schema_version,
        legacy_scope_json = :legacy_scope_json
    WHERE run_id = :run_id
"""
_INSERT_AUDIT_STATEMENT = """
    INSERT INTO recommendation_evidence_classification_audits (
        plan_hash,
        run_id,
        planned_evidence_class,
        classification_reason,
        legacy_evaluation_scope,
        legacy_started_at,
        legacy_trade_date,
        legacy_recommended_at_min,
        legacy_recommended_at_max,
        recommendation_count,
        outcome_count,
        planned_at,
        applied_at
    ) VALUES (
        :plan_hash,
        :run_id,
        :planned_evidence_class,
        :classification_reason,
        :legacy_evaluation_scope,
        :legacy_started_at,
        :legacy_trade_date,
        :legacy_recommended_at_min,
        :legacy_recommended_at_max,
        :recommendation_count,
        :outcome_count,
        :planned_at,
        :applied_at
    )
"""
_APPLY_AUDIT_STATEMENT = """
    UPDATE recommendation_evidence_classification_audits
    SET applied_at = :applied_at
    WHERE plan_hash = :plan_hash AND run_id = :run_id
"""


class ClassificationBackfillError(ValueError):
    """Stable, caller-safe error for classification backfill violations."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ClassificationAssignment:
    """One reviewed plan assignment; payload key ``reason`` maps to the column."""

    run_id: str
    evidence_class: str
    reason: str
    recommendation_count: int
    outcome_count: int


@dataclass(frozen=True, slots=True)
class VerifiedClassificationPlan:
    """A plan whose hash and invariants survived :func:`load_and_verify_plan`."""

    plan_hash: str
    rule_version: str
    assignments: tuple[ClassificationAssignment, ...]


@dataclass(frozen=True, slots=True)
class PreservationReport:
    """Pure comparison result of two preservation snapshots."""

    ok: bool
    checks: Mapping[str, bool]
    violations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": dict(self.checks),
            "violations": list(self.violations),
        }


@dataclass(frozen=True, slots=True)
class ClassificationBackfillReport:
    """Structured, JSON-serializable outcome of one executor run."""

    plan_hash: str
    rule_version: str
    dry_run: bool
    write_authorized: bool
    write_operations_executed: int
    total: int
    updated: int
    unchanged: int
    skipped_provenance_complete: int
    runs_not_in_plan: int
    evidence_class_counts: Mapping[str, Mapping[str, int]]
    invariants: Mapping[str, bool]
    preservation_violations: tuple[str, ...]
    run_details: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_hash": self.plan_hash,
            "rule_version": self.rule_version,
            "dry_run": self.dry_run,
            "write_authorized": self.write_authorized,
            "write_operations_executed": self.write_operations_executed,
            "total": self.total,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped_provenance_complete": self.skipped_provenance_complete,
            "runs_not_in_plan": self.runs_not_in_plan,
            "evidence_class_counts": {
                evidence_class: dict(counts)
                for evidence_class, counts in self.evidence_class_counts.items()
            },
            "invariants": dict(self.invariants),
            "preservation_violations": list(self.preservation_violations),
            "run_details": [dict(detail) for detail in self.run_details],
        }


@dataclass(frozen=True, slots=True)
class _RunDecision:
    action: str
    operations: tuple[str, ...]
    detail: Mapping[str, Any]


def canonical_plan_hash(
    rule_version: str,
    assignments: Sequence[Mapping[str, Any]],
) -> str:
    """Recompute the preflight plan hash byte-for-byte.

    Mirrors ``collect_classification_plan``: canonical JSON with sorted keys,
    compact separators and UTF-8 SHA-256 over ``{"rule_version", "assignments"}``.
    """

    canonical = json.dumps(
        {"rule_version": rule_version, "assignments": assignments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_and_verify_plan(
    plan_payload: Mapping[str, Any],
    *,
    expected_plan_hash: str,
) -> VerifiedClassificationPlan:
    """Validate a plan payload and pin it to the reviewed hash, failing closed.

    The payload must carry ``rule_version`` (equal to the reviewed
    classification rule), the ordered ``assignments`` list and
    ``live_forward_assignments == 0``; every assignment's ``evidence_class``
    must stay inside :data:`ALLOWED_BACKFILL_EVIDENCE_CLASSES`.  The canonical
    hash is recomputed over the raw payload exactly as the preflight computed
    it — any tampering, reordering or rule drift raises
    ``classification_plan_hash_mismatch`` before anything executes.
    """

    if not isinstance(plan_payload, Mapping):
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            "plan payload must be a mapping",
        )
    rule_version = plan_payload.get("rule_version")
    if rule_version != CLASSIFICATION_RULE_VERSION:
        raise ClassificationBackfillError(
            "classification_rule_version_mismatch",
            f"plan rule_version {rule_version!r} does not match the reviewed "
            f"{CLASSIFICATION_RULE_VERSION!r}",
        )
    raw_assignments = plan_payload.get("assignments")
    if not isinstance(raw_assignments, list):
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            "plan assignments must be a list",
        )
    assignments = tuple(
        _validated_assignment(item, index)
        for index, item in enumerate(raw_assignments)
    )
    run_ids = [assignment.run_id for assignment in assignments]
    if len(set(run_ids)) != len(run_ids):
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            "plan assignments contain duplicate run_id values",
        )
    live_forward_assignments = plan_payload.get("live_forward_assignments")
    if (
        isinstance(live_forward_assignments, bool)
        or not isinstance(live_forward_assignments, int)
        or live_forward_assignments != 0
    ):
        raise ClassificationBackfillError(
            "classification_plan_invariant_violation",
            "plan must declare live_forward_assignments == 0",
        )
    if not isinstance(expected_plan_hash, str) or not _PLAN_HASH_PATTERN.fullmatch(
        expected_plan_hash
    ):
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            "expected_plan_hash must be a 64-character lowercase SHA-256 hex digest",
        )
    try:
        actual_plan_hash = canonical_plan_hash(rule_version, raw_assignments)
    except (TypeError, ValueError) as exc:
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            "plan assignments must be JSON-serializable",
        ) from exc
    if actual_plan_hash != expected_plan_hash:
        raise ClassificationBackfillError(
            "classification_plan_hash_mismatch",
            f"recomputed plan hash {actual_plan_hash} does not match expected "
            f"{expected_plan_hash}",
        )
    return VerifiedClassificationPlan(
        plan_hash=actual_plan_hash,
        rule_version=rule_version,
        assignments=tuple(
            sorted(assignments, key=lambda assignment: assignment.run_id)
        ),
    )


def derive_plan_payload(connection: Session | Connection) -> dict[str, Any]:
    """Re-derive the read-only classification plan, exposing the assignments.

    Uses the same statements, classification rule and canonical hash as the
    production preflight, so a payload derived from the real database must
    reproduce the reviewed plan hash before any executor step.  This function
    performs zero writes.
    """

    for statement in (
        _RUN_FACTS_STATEMENT,
        _RECOMMENDATION_COUNT_STATEMENT,
        _OUTCOME_COUNT_STATEMENT,
    ):
        assert_read_only_statement(statement)
    rows = connection.execute(text(_RUN_FACTS_STATEMENT)).mappings().all()
    recommendation_counts = _group_counts(connection, _RECOMMENDATION_COUNT_STATEMENT)
    outcome_counts = _group_counts(connection, _OUTCOME_COUNT_STATEMENT)
    assignments: list[dict[str, Any]] = []
    for row in rows:
        run_id = str(row["run_id"])
        evidence_class, reason = classify_legacy_run(
            LegacyRunFacts(
                run_id=run_id,
                run_mode=row["run_mode"],
                evaluation_scope=row["evaluation_scope"],
                run_name=row["run_name"],
                trade_date=row["trade_date"],
                started_at=row["started_at"],
            )
        )
        assignments.append(
            {
                "run_id": run_id,
                "evidence_class": evidence_class,
                "reason": reason,
                "recommendation_count": recommendation_counts.get(run_id, 0),
                "outcome_count": outcome_counts.get(run_id, 0),
            }
        )
    return {
        "status": "read_only_plan_derived",
        "rule_version": CLASSIFICATION_RULE_VERSION,
        "assignments": assignments,
        "live_forward_assignments": 0,
        "plan_hash": canonical_plan_hash(CLASSIFICATION_RULE_VERSION, assignments),
        "write_authorized": False,
        "write_operations_executed": 0,
    }


def capture_preservation_snapshot(session: Session | Connection) -> dict[str, Any]:
    """Capture the facts Requirement 2.4 says must survive the backfill.

    Covers the total run count, the run primary-key set, per-run identity and
    original time fields, and every recommendation's ``recommended_at``.  The
    snapshot is a plain dict so :func:`verify_preservation` stays pure.
    """

    assert_read_only_statement(_PRESERVATION_RUNS_STATEMENT)
    assert_read_only_statement(_PRESERVATION_RECOMMENDATIONS_STATEMENT)
    run_rows = session.execute(text(_PRESERVATION_RUNS_STATEMENT)).mappings().all()
    recommendation_rows = session.execute(
        text(_PRESERVATION_RECOMMENDATIONS_STATEMENT)
    ).mappings().all()
    return {
        "run_count": len(run_rows),
        "primary_keys": [str(row["run_id"]) for row in run_rows],
        "runs": {
            str(row["run_id"]): {
                field: _stable_value(row[field])
                for field in PRESERVED_RUN_IDENTITY_FIELDS + PRESERVED_RUN_TIME_FIELDS
            }
            for row in run_rows
        },
        "recommendation_count": len(recommendation_rows),
        "recommendations": {
            str(row["recommendation_id"]): {
                "run_id": str(row["run_id"]),
                "recommended_at": _stable_value(row["recommended_at"]),
            }
            for row in recommendation_rows
        },
    }


def verify_preservation(
    before_snapshot: Mapping[str, Any],
    after_snapshot: Mapping[str, Any],
) -> PreservationReport:
    """Pure check that execution preserved every historical fact (Req 2.4/2.11).

    Compares total run count, run primary-key sets, per-run identity and
    original time fields, and the recommendation set with its
    ``recommended_at`` values.  Any difference — including a deleted row or a
    single moved timestamp — flips the matching check and is reported as a
    violation.
    """

    checks = {
        "total_runs_unchanged": True,
        "run_primary_keys_unchanged": True,
        "run_identity_fields_unchanged": True,
        "run_original_time_fields_unchanged": True,
        "recommendations_unchanged": True,
    }
    violations: list[str] = []

    if before_snapshot.get("run_count") != after_snapshot.get("run_count"):
        checks["total_runs_unchanged"] = False
        violations.append(
            f"run_count changed: {before_snapshot.get('run_count')} -> "
            f"{after_snapshot.get('run_count')}"
        )
    before_keys = sorted(before_snapshot.get("primary_keys") or [])
    after_keys = sorted(after_snapshot.get("primary_keys") or [])
    if before_keys != after_keys:
        checks["run_primary_keys_unchanged"] = False
        violations.append("run primary key set changed")

    before_runs = before_snapshot.get("runs") or {}
    after_runs = after_snapshot.get("runs") or {}
    for run_id in sorted(before_runs):
        before_fields = before_runs[run_id]
        after_fields = after_runs.get(run_id)
        if after_fields is None:
            checks["run_identity_fields_unchanged"] = False
            checks["run_original_time_fields_unchanged"] = False
            violations.append(f"run {run_id} is missing after execution")
            continue
        for field_name in PRESERVED_RUN_IDENTITY_FIELDS:
            if before_fields.get(field_name) != after_fields.get(field_name):
                checks["run_identity_fields_unchanged"] = False
                violations.append(
                    f"run {run_id} {field_name} changed: "
                    f"{before_fields.get(field_name)!r} -> {after_fields.get(field_name)!r}"
                )
        for field_name in PRESERVED_RUN_TIME_FIELDS:
            if before_fields.get(field_name) != after_fields.get(field_name):
                checks["run_original_time_fields_unchanged"] = False
                violations.append(
                    f"run {run_id} {field_name} changed: "
                    f"{before_fields.get(field_name)!r} -> {after_fields.get(field_name)!r}"
                )

    before_recommendations = before_snapshot.get("recommendations") or {}
    after_recommendations = after_snapshot.get("recommendations") or {}
    if before_snapshot.get("recommendation_count") != after_snapshot.get(
        "recommendation_count"
    ) or set(before_recommendations) != set(after_recommendations):
        checks["recommendations_unchanged"] = False
        violations.append("recommendation set changed")
    else:
        for recommendation_id in sorted(before_recommendations):
            before_rec = before_recommendations[recommendation_id]
            after_rec = after_recommendations[recommendation_id]
            if (
                before_rec.get("run_id") != after_rec.get("run_id")
                or before_rec.get("recommended_at") != after_rec.get("recommended_at")
            ):
                checks["recommendations_unchanged"] = False
                violations.append(
                    f"recommendation {recommendation_id} changed: "
                    f"{before_rec!r} -> {after_rec!r}"
                )

    return PreservationReport(
        ok=all(checks.values()),
        checks=checks,
        violations=tuple(violations),
    )


def execute_backfill(
    session: Session | Connection,
    plan: VerifiedClassificationPlan,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> ClassificationBackfillReport:
    """Execute (or simulate) the reviewed classification plan idempotently.

    ``dry_run=True`` — the default — computes the exact same decisions and
    report but performs zero writes, matching the plan's
    ``write_authorized=false`` semantics.  Writes happen only with an explicit
    ``dry_run=False`` call, and only inside the caller's transaction: this
    function never commits.  ``now`` injects the apply clock so tests and
    gated replays are deterministic.

    Per assignment the executor is fail-closed: a missing run, drifted
    recommendation/outcome counts, partial pre-existing provenance, or any
    conflict between a pre-registered audit row and the plan raises
    :class:`ClassificationBackfillError`.  Runs already carrying complete
    provenance from another authorized writer are never overwritten; they are
    reported as ``skipped_provenance_complete``.
    """

    if not isinstance(plan, VerifiedClassificationPlan):
        raise ClassificationBackfillError(
            "classification_plan_unverified",
            "execute_backfill requires a plan returned by load_and_verify_plan",
        )
    applied_at = now if now is not None else _utcnow_naive()
    before_snapshot = capture_preservation_snapshot(session)
    planned_run_ids = {assignment.run_id for assignment in plan.assignments}

    details: list[Mapping[str, Any]] = []
    per_class: dict[str, dict[str, int]] = {}
    write_operations_executed = 0
    for assignment in plan.assignments:
        row = _fetch_run(session, assignment.run_id)
        if row is None:
            raise ClassificationBackfillError(
                "classification_run_missing",
                f"planned run {assignment.run_id} is absent from recommendation_runs",
            )
        _assert_plan_counts_match(session, assignment)
        audit = _fetch_audit(session, plan.plan_hash, assignment.run_id)
        decision = _resolve_run_action(row=row, audit=audit, assignment=assignment)
        if not dry_run:
            _apply_decision(
                session,
                plan=plan,
                assignment=assignment,
                row=row,
                decision=decision,
                applied_at=applied_at,
            )
            write_operations_executed += len(decision.operations)
        details.append(decision.detail)
        class_counts = per_class.setdefault(
            assignment.evidence_class,
            {"total": 0, "updated": 0, "unchanged": 0, "skipped_provenance_complete": 0},
        )
        class_counts["total"] += 1
        class_counts[decision.action] += 1

    after_snapshot = capture_preservation_snapshot(session)
    preservation = verify_preservation(before_snapshot, after_snapshot)
    if not preservation.ok:
        raise ClassificationBackfillError(
            "classification_preservation_violation",
            "post-execution preservation check failed: "
            + "; ".join(preservation.violations),
        )

    total = len(details)
    updated = sum(1 for detail in details if detail["action"] == "updated")
    unchanged = sum(1 for detail in details if detail["action"] == "unchanged")
    skipped = sum(
        1 for detail in details if detail["action"] == "skipped_provenance_complete"
    )
    invariants = {
        **preservation.checks,
        "no_live_forward_assigned": True,
        "write_set_allowlist_only": True,
        "counters_sum_to_total": updated + unchanged + skipped == total,
    }
    return ClassificationBackfillReport(
        plan_hash=plan.plan_hash,
        rule_version=plan.rule_version,
        dry_run=dry_run,
        write_authorized=not dry_run,
        write_operations_executed=write_operations_executed,
        total=total,
        updated=updated,
        unchanged=unchanged,
        skipped_provenance_complete=skipped,
        runs_not_in_plan=len(set(before_snapshot["primary_keys"]) - planned_run_ids),
        evidence_class_counts=per_class,
        invariants=invariants,
        preservation_violations=preservation.violations,
        run_details=tuple(details),
    )


def _validated_assignment(item: Any, index: int) -> ClassificationAssignment:
    where = f"assignments[{index}]"
    if not isinstance(item, Mapping):
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            f"{where} must be a mapping",
        )
    run_id = item.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            f"{where}.run_id must be a non-empty string",
        )
    evidence_class = item.get("evidence_class")
    if (
        not isinstance(evidence_class, str)
        or evidence_class not in ALLOWED_BACKFILL_EVIDENCE_CLASSES
    ):
        raise ClassificationBackfillError(
            "classification_plan_invariant_violation",
            f"{where}.evidence_class {evidence_class!r} is not an allowlisted "
            "legacy evidence class",
        )
    reason = item.get("reason")
    if not isinstance(reason, str) or not reason:
        raise ClassificationBackfillError(
            "classification_plan_invalid",
            f"{where}.reason must be a non-empty string",
        )
    counts: dict[str, int] = {}
    for field_name in ("recommendation_count", "outcome_count"):
        value = item.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ClassificationBackfillError(
                "classification_plan_invalid",
                f"{where}.{field_name} must be a non-negative integer",
            )
        counts[field_name] = value
    return ClassificationAssignment(
        run_id=run_id,
        evidence_class=evidence_class,
        reason=reason,
        recommendation_count=counts["recommendation_count"],
        outcome_count=counts["outcome_count"],
    )


def _resolve_run_action(
    *, row: Mapping[str, Any], audit: Mapping[str, Any] | None,
    assignment: ClassificationAssignment,
) -> _RunDecision:
    """Pure per-run decision; canonical new writes are never legacy-backfilled."""
    target = {
        "evidence_class": assignment.evidence_class,
        "classification_reason": assignment.reason,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "legacy_scope_json": _legacy_scope_json(row),
    }
    current = {name: row[name] for name in RUN_WRITE_ALLOWLIST}
    matches_target = all(current[name] == target[name] for name in RUN_WRITE_ALLOWLIST)
    populated = [current[name] is not None for name in RUN_WRITE_ALLOWLIST]
    if audit is not None and audit["applied_at"] is not None:
        if matches_target and _audit_matches_plan(audit, assignment):
            return _RunDecision("unchanged", (), _detail(assignment, "unchanged", 0))
        raise ClassificationBackfillError("classification_provenance_conflict", f"run {assignment.run_id} drifted from its applied audit facts")
    canonical_complete = all(row.get(name) is not None for name in (
        "evidence_class", "actual_generated_at", "evidence_identity_hash",
        "strategy_policy_fingerprint", "execution_policy_fingerprint",
        "code_revision", "provenance_schema_version",
    ))
    if canonical_complete and audit is None:
        return _RunDecision("skipped_provenance_complete", (), _detail(assignment, "skipped_provenance_complete", 0, matches_plan=matches_target))
    if any(populated):
        if not all(populated):
            raise ClassificationBackfillError("classification_provenance_conflict", f"run {assignment.run_id} carries partial provenance; manual review required")
        if audit is None:
            return _RunDecision("skipped_provenance_complete", (), _detail(assignment, "skipped_provenance_complete", 0, matches_plan=matches_target))
        _assert_audit_matches_plan(audit, assignment)
        if matches_target:
            return _RunDecision("updated", ("apply_audit",), _detail(assignment, "updated", 1))
        raise ClassificationBackfillError("classification_provenance_conflict", f"run {assignment.run_id} provenance disagrees with the reviewed plan")
    operations = ["update_run"]
    if audit is None: operations.append("insert_audit")
    else:
        _assert_audit_matches_plan(audit, assignment); operations.append("apply_audit")
    return _RunDecision("updated", tuple(operations), _detail(assignment, "updated", len(operations)))

def _apply_decision(
    session: Session | Connection,
    *,
    plan: VerifiedClassificationPlan,
    assignment: ClassificationAssignment,
    row: Mapping[str, Any],
    decision: _RunDecision,
    applied_at: datetime,
) -> None:
    for operation in decision.operations:
        if operation == "update_run":
            session.execute(
                text(_UPDATE_RUN_STATEMENT),
                {
                    "evidence_class": assignment.evidence_class,
                    "classification_reason": assignment.reason,
                    "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
                    "legacy_scope_json": _legacy_scope_json(row),
                    "run_id": assignment.run_id,
                },
            )
        elif operation == "insert_audit":
            stats = session.execute(
                text(_RECOMMENDATION_STATS_STATEMENT),
                {"run_id": assignment.run_id},
            ).mappings().one()
            session.execute(
                text(_INSERT_AUDIT_STATEMENT),
                {
                    "plan_hash": plan.plan_hash,
                    "run_id": assignment.run_id,
                    "planned_evidence_class": assignment.evidence_class,
                    "classification_reason": assignment.reason,
                    "legacy_evaluation_scope": row["evaluation_scope"],
                    "legacy_started_at": row["started_at"],
                    "legacy_trade_date": row["trade_date"],
                    "legacy_recommended_at_min": stats["recommended_at_min"],
                    "legacy_recommended_at_max": stats["recommended_at_max"],
                    "recommendation_count": assignment.recommendation_count,
                    "outcome_count": assignment.outcome_count,
                    "planned_at": applied_at,
                    "applied_at": applied_at,
                },
            )
        elif operation == "apply_audit":
            session.execute(
                text(_APPLY_AUDIT_STATEMENT),
                {
                    "plan_hash": plan.plan_hash,
                    "run_id": assignment.run_id,
                    "applied_at": applied_at,
                },
            )
        else:  # pragma: no cover - defensive; _resolve_run_action is exhaustive
            raise ClassificationBackfillError(
                "classification_plan_invalid",
                f"unknown write operation {operation!r}",
            )


def _fetch_run(session: Session | Connection, run_id: str) -> dict[str, Any] | None:
    row = session.execute(
        text(_RUN_ROW_STATEMENT),
        {"run_id": run_id},
    ).mappings().first()
    return dict(row) if row is not None else None


def _fetch_audit(
    session: Session | Connection,
    plan_hash: str,
    run_id: str,
) -> dict[str, Any] | None:
    row = session.execute(
        text(_AUDIT_ROW_STATEMENT),
        {"plan_hash": plan_hash, "run_id": run_id},
    ).mappings().first()
    return dict(row) if row is not None else None


def _assert_plan_counts_match(
    session: Session | Connection,
    assignment: ClassificationAssignment,
) -> None:
    recommendation_count = int(
        session.execute(
            text(
                "SELECT COUNT(*) FROM recommendations WHERE run_id = :run_id"
            ),
            {"run_id": assignment.run_id},
        ).scalar_one()
    )
    outcome_count = int(
        session.execute(
            text(_OUTCOME_COUNT_FOR_RUN_STATEMENT),
            {"run_id": assignment.run_id},
        ).scalar_one()
    )
    if (
        recommendation_count != assignment.recommendation_count
        or outcome_count != assignment.outcome_count
    ):
        raise ClassificationBackfillError(
            "classification_plan_drift",
            f"run {assignment.run_id} counts drifted from the reviewed plan: "
            f"recommendations {assignment.recommendation_count} -> "
            f"{recommendation_count}, outcomes {assignment.outcome_count} -> "
            f"{outcome_count}",
        )


def _audit_matches_plan(
    audit: Mapping[str, Any],
    assignment: ClassificationAssignment,
) -> bool:
    return (
        audit["planned_evidence_class"] == assignment.evidence_class
        and audit["classification_reason"] == assignment.reason
    )


def _assert_audit_matches_plan(
    audit: Mapping[str, Any],
    assignment: ClassificationAssignment,
) -> None:
    if not _audit_matches_plan(audit, assignment):
        raise ClassificationBackfillError(
            "classification_provenance_conflict",
            f"audit facts for run {assignment.run_id} disagree with the reviewed plan",
        )


def _legacy_scope_json(row: Mapping[str, Any]) -> str:
    """Deterministic JSON snapshot of the legacy identity facts being preserved."""

    return json.dumps(
        {
            "evaluation_scope": row["evaluation_scope"],
            "run_mode": row["run_mode"],
            "run_name": row["run_name"],
            "started_at": _stable_value(row["started_at"]),
            "trade_date": row["trade_date"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _group_counts(
    connection: Session | Connection,
    statement: str,
) -> dict[str, int]:
    return {
        str(row["group_key"]): int(row["row_count"])
        for row in connection.execute(text(statement)).mappings()
        if row["group_key"] is not None
    }


def _detail(
    assignment: ClassificationAssignment,
    action: str,
    planned_write_operations: int,
    **extra: Any,
) -> Mapping[str, Any]:
    return {
        "run_id": assignment.run_id,
        "evidence_class": assignment.evidence_class,
        "classification_reason": assignment.reason,
        "action": action,
        "planned_write_operations": planned_write_operations,
        **extra,
    }


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


__all__ = [
    "ALLOWED_BACKFILL_EVIDENCE_CLASSES",
    "ClassificationAssignment",
    "ClassificationBackfillError",
    "ClassificationBackfillReport",
    "PRESERVED_RUN_IDENTITY_FIELDS",
    "PRESERVED_RUN_TIME_FIELDS",
    "REVIEWED_CLASSIFICATION_PLAN_HASH",
    "PreservationReport",
    "RUN_WRITE_ALLOWLIST",
    "VerifiedClassificationPlan",
    "canonical_plan_hash",
    "capture_preservation_snapshot",
    "derive_plan_payload",
    "execute_backfill",
    "load_and_verify_plan",
    "verify_preservation",
]

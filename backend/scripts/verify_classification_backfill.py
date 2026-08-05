"""Read-only verifier for the reviewed classification backfill plan.

This command has no write mode.  It re-derives (or loads) the assignment
payload, pins it to the task-0.3 reviewed hash, simulates the executor with its
default ``dry_run=True`` contract, and reports preservation expectations.
Real backfill remains exclusively owned by task 9.4 and its separate approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.services.evidence.classification_backfill import (
    REVIEWED_CLASSIFICATION_PLAN_HASH,
    ClassificationBackfillError,
    capture_preservation_snapshot,
    derive_plan_payload,
    execute_backfill,
    load_and_verify_plan,
    verify_preservation,
)


def _primary_key_digest(primary_keys: list[str]) -> str:
    canonical = json.dumps(
        sorted(primary_keys), ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_classification_backfill(
    session: Session | Connection,
    *,
    expected_plan_hash: str = REVIEWED_CLASSIFICATION_PLAN_HASH,
    plan_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic read-only verification report.

    ``plan_payload`` exists for offline fixture verification.  Omitting it
    re-derives the plan from the connected database using SELECT statements.
    The executor is deliberately called without ``dry_run=False`` and this
    function never commits.
    """
    payload = dict(plan_payload) if plan_payload is not None else derive_plan_payload(session)
    plan = load_and_verify_plan(payload, expected_plan_hash=expected_plan_hash)
    before = capture_preservation_snapshot(session)
    report = execute_backfill(session, plan)  # default dry_run=True is a safety boundary
    after = capture_preservation_snapshot(session)
    preservation = verify_preservation(before, after)
    if not preservation.ok or report.write_operations_executed != 0:
        raise ClassificationBackfillError(
            "classification_verifier_write_detected",
            "read-only verification changed preserved facts or executed a write",
        )

    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "run_count": 0,
            "recommendation_count": 0,
            "outcome_count": 0,
            "sample_run_ids": [],
        }
    )
    unverified_run_ids: list[str] = []
    for assignment in plan.assignments:
        group = groups[(assignment.evidence_class, assignment.reason)]
        group["run_count"] += 1
        group["recommendation_count"] += assignment.recommendation_count
        group["outcome_count"] += assignment.outcome_count
        if len(group["sample_run_ids"]) < 3:
            group["sample_run_ids"].append(assignment.run_id)
        if assignment.evidence_class == "unverified":
            unverified_run_ids.append(assignment.run_id)

    before_keys = [str(value) for value in before["primary_keys"]]
    after_keys = [str(value) for value in after["primary_keys"]]
    before_digest = _primary_key_digest(before_keys)
    after_digest = _primary_key_digest(after_keys)
    return {
        "status": "verified_read_only_dry_run",
        "plan_hash": plan.plan_hash,
        "rule_version": plan.rule_version,
        "dry_run": report.dry_run,
        "write_authorized": False,
        "write_operations_executed": report.write_operations_executed,
        "assignments": report.total,
        "classification_groups": [
            {
                "evidence_class": evidence_class,
                "classification_reason": reason,
                **groups[(evidence_class, reason)],
            }
            for evidence_class, reason in sorted(groups)
        ],
        "unverified_run_ids": sorted(unverified_run_ids),
        "run_primary_keys": sorted(before_keys),
        "before_after_expected": {
            "run_count_before": before["run_count"],
            "run_count_after": after["run_count"],
            "recommendation_count_before": before["recommendation_count"],
            "recommendation_count_after": after["recommendation_count"],
            "run_primary_key_digest_before": before_digest,
            "run_primary_key_digest_after": after_digest,
        },
        "executor": report.to_dict(),
        "preservation": preservation.to_dict(),
        "environment_acceptance_state": "BLOCKED",
        "layer": "LAYER_1_ONLY",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the reviewed classification plan and executor in read-only "
            "dry-run mode; this command cannot perform the task 9.4 backfill."
        )
    )
    parser.add_argument(
        "--expected-plan-hash",
        default=REVIEWED_CLASSIFICATION_PLAN_HASH,
        help="Reviewed lowercase SHA-256 plan hash (defaults to task 0.3).",
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        default=None,
        help=(
            "Optional assignment payload for offline verification; without it "
            "the plan is re-derived through read-only database queries."
        ),
    )
    parser.add_argument("--compact", action="store_true")
    return parser


def _load_plan(path: Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ClassificationBackfillError(
            "classification_plan_invalid", "plan JSON must contain an object"
        )
    return payload


def _begin_read_only(connection: Connection) -> None:
    if connection.dialect.name == "mysql":
        connection.execute(text("SET TRANSACTION READ ONLY"))


def main() -> int:
    args = build_parser().parse_args()
    try:
        plan_payload = _load_plan(args.plan_json)
        # Deferred import keeps module-level/offline tests independent of the
        # configured environment and guarantees CLI output never includes a URL.
        from app.db.session import engine

        with engine.connect() as connection:
            _begin_read_only(connection)
            with Session(bind=connection) as session:
                result = verify_classification_backfill(
                    session,
                    expected_plan_hash=args.expected_plan_hash,
                    plan_payload=plan_payload,
                )
                session.rollback()
            connection.rollback()
    except ClassificationBackfillError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_code": exc.code,
                    "expected_plan_hash": args.expected_plan_hash,
                    "write_authorized": False,
                    "write_operations_executed": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

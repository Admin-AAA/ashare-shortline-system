"""Generate task 0.3's read-only migration inventory and MySQL preflight artifact.

Task 0.8 gates this runner behind the governance store: a frozen
Restore_Capacity_Criteria_Manifest that applies to the migration plan must
exist and predate the preflight trigger point, otherwise the runner prints a
BLOCKED verdict and exits non-zero without attempting any database
connection.  ``--record-observation`` (off by default) appends the observed
lock/capacity/disk readings as an immutable ``restore_capacity_observation``
object; the frozen manifest thresholds are never rewritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db.production_hardening_preflight import (
    assert_read_only_statement,
    collect_preflight,
)
from app.governance.manifests import AdmissionVerdict
from app.governance.migration_gates import (
    DEFAULT_MIGRATION_PLAN_KEY,
    observation_readings_from_preflight,
    operation_impact_register,
    preflight_admission,
    record_restore_capacity_observation,
)
from app.governance.store import GovernanceObjectStore

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    _REPOSITORY_ROOT
    / ".kiro"
    / "specs"
    / "production-evidence-risk-hardening"
    / "migration-preflight-task-0.3.json"
)

_BLOCKED_EXIT_CODE = 2


def _current_code_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPOSITORY_ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except Exception:
        return "unknown-revision"
    revision = completed.stdout.strip()
    return revision or "unknown-revision"


def write_preflight(output: Path) -> dict[str, object]:
    # Deferred imports: the admission gate must be able to BLOCK before any
    # database-facing module (engine, settings, capture helpers) is loaded.
    from sqlalchemy import text

    from app.db.session import engine
    from scripts.capture_production_evidence_baseline import (
        _forbidden_literals_from_settings,
        assert_safe_output,
    )

    with engine.connect() as connection:
        try:
            if connection.dialect.name == "mysql":
                statement = "SET TRANSACTION READ ONLY"
                assert_read_only_statement(statement)
                connection.execute(text(statement))
            payload = collect_preflight(connection)
            connection.rollback()
        except Exception:
            connection.rollback()
            raise

    assert_safe_output(
        payload,
        forbidden_literals=_forbidden_literals_from_settings(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a non-destructive production-hardening migration preflight."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--governance-store",
        type=Path,
        default=None,
        help=(
            "Governance object store root; defaults to "
            "ASHARE_GOVERNANCE_STORE_ROOT or the spec store."
        ),
    )
    parser.add_argument(
        "--migration-plan-key",
        default=DEFAULT_MIGRATION_PLAN_KEY,
        help="Stable key of the migration plan the frozen manifest must apply to.",
    )
    parser.add_argument(
        "--record-observation",
        action="store_true",
        help=(
            "After a successful preflight, append an immutable "
            "restore_capacity_observation to the governance store. "
            "Off by default: the runner stays purely read-only."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    store = GovernanceObjectStore(args.governance_store)
    now = datetime.now(timezone.utc)
    verdict = preflight_admission(store, args.migration_plan_key, now)
    if verdict.verdict is not AdmissionVerdict.ADMITTED:
        print(
            json.dumps(
                {
                    "governance_gate": verdict.verdict.value,
                    "reasons": list(verdict.reasons),
                    "migration_plan_key": args.migration_plan_key,
                    "governance_store_root": str(store.root),
                    "db_connection": "not_attempted",
                    "ddl_executed": False,
                    "dml_executed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return _BLOCKED_EXIT_CODE

    payload = write_preflight(args.output.resolve())

    observation_object_id = None
    if args.record_observation:
        observation = record_restore_capacity_observation(
            store,
            migration_plan_key=args.migration_plan_key,
            manifest_object_id=verdict.manifest.object_id,
            captured_at=now,
            readings=observation_readings_from_preflight(payload),
            impact_register=operation_impact_register(payload),
            actor="preflight_production_hardening_migrations",
            code_revision=_current_code_revision(),
        )
        observation_object_id = observation.object_id

    preflight = payload["preflight"]
    classification = preflight["classification_plan"]
    print(
        json.dumps(
            {
                "output": str(args.output),
                "governance_gate": verdict.verdict.value,
                "restore_capacity_manifest_id": verdict.manifest.object_id,
                "observation_object_id": observation_object_id,
                "apply_gate": payload["apply_gate"]["status"],
                "blocking_findings": payload["apply_gate"]["blocking_findings"],
                "classification_plan_hash": classification.get("plan_hash"),
                "classification_write_operations": classification.get(
                    "write_operations_executed", 0
                ),
                "ddl_executed": False,
                "dml_executed": False,
                "safety_check": "passed",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

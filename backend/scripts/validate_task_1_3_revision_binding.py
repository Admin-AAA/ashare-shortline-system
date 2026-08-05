"""Validate current Task 1.3 binding against Layer 1 property evidence.

This appends a code-completion validation fact.  It deliberately cannot change
Environment_Acceptance_State from BLOCKED because the four real Layer 2 evidence
kinds are not produced here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.governance.events import GovernanceEventLog  # noqa: E402
from app.governance.store import GovernanceObjectStore  # noqa: E402
from scripts.freeze_task_1_3_revision_binding import (  # noqa: E402
    current_task_1_3_binding,
)

VALIDATION_KIND = "revision_binding_validation"
REQUIRED_PROPERTIES = frozenset({6, 7, 8, 9, 10, 45, 46})


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def validate_current_binding(
    store: GovernanceObjectStore,
    *,
    binding_object_id: str,
    now: datetime,
    code_revision: str,
):
    binding_object = store.get(binding_object_id)
    binding = current_task_1_3_binding()
    if binding_object.payload.get("binding_hash") != binding.binding_hash:
        raise RuntimeError("REVISION_BINDING_STALE")

    evidence_by_property = {}
    for evidence in store.iter_kind("property_test_evidence"):
        number = int(evidence.payload.get("property_number") or 0)
        if number not in REQUIRED_PROPERTIES:
            continue
        if evidence.payload.get("result") != "PASS" or int(
            evidence.payload.get("iterations") or 0
        ) < 100:
            continue
        if number in {45, 46} and evidence.payload.get(
            "revision_binding_hash"
        ) != binding.binding_hash:
            continue
        evidence_by_property[number] = evidence
    missing = REQUIRED_PROPERTIES - set(evidence_by_property)
    if missing:
        raise RuntimeError(f"CORRECTNESS_PROOF_ROUTE_MISSING:{sorted(missing)}")

    evidence_ids = tuple(
        evidence_by_property[number].object_id
        for number in sorted(REQUIRED_PROPERTIES)
    )
    existing = [
        item
        for item in store.iter_kind(VALIDATION_KIND)
        if item.payload.get("binding_hash") == binding.binding_hash
        and tuple(item.payload.get("property_evidence_ids") or ()) == evidence_ids
    ]
    if existing:
        return existing[-1], False

    payload = {
        "binding_object_id": binding_object_id,
        "binding_hash": binding.binding_hash,
        "layer_1_result": "PASS",
        "correctness_properties": sorted(REQUIRED_PROPERTIES),
        "property_evidence_ids": list(evidence_ids),
        "code_state": "CODE_COMPLETE",
        "environment_acceptance_state": "BLOCKED",
        "task_complete": False,
        "reason_codes": [
            "LAYER_2_EVIDENCE_MISSING:migration_apply_no_op",
            "LAYER_2_EVIDENCE_MISSING:real_prefect_replay",
            "LAYER_2_EVIDENCE_MISSING:persisted_historical_query",
            "LAYER_2_EVIDENCE_MISSING:persisted_live_exclusion",
        ],
    }
    validation = store.put(
        kind=VALIDATION_KIND,
        payload=payload,
        created_at=now,
        created_by="kiro-cli:validate-task-1-3-binding",
        code_revision=code_revision,
        referenced_object_ids=(binding_object_id, *evidence_ids),
    )
    GovernanceEventLog(store).append(
        event_type="TASK_1_3_CODE_COMPLETE_VALIDATED",
        actor="kiro-cli:validate-task-1-3-binding",
        code_revision=code_revision,
        recorded_at=now,
        payload={
            "validation_object_id": validation.object_id,
            "binding_hash": binding.binding_hash,
            "code_state": "CODE_COMPLETE",
            "environment_acceptance_state": "BLOCKED",
            "task_complete": False,
        },
        referenced_object_ids=(validation.object_id, binding_object_id, *evidence_ids),
    )
    return validation, True


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--binding-object-id", required=True)
    args = parser.parse_args()
    store = GovernanceObjectStore()
    validation, created = validate_current_binding(
        store,
        binding_object_id=args.binding_object_id,
        now=datetime.now(timezone.utc),
        code_revision=_git_head(),
    )
    print(
        json.dumps(
            {
                "status": "validated" if created else "already_validated",
                "validation_object_id": validation.object_id,
                "binding_hash": validation.payload["binding_hash"],
                "code_state": "CODE_COMPLETE",
                "environment_acceptance_state": "BLOCKED",
                "task_complete": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

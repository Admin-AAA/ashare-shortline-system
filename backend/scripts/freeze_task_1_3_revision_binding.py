"""Freeze the current six-kind Task 1.3 revision binding.

The binding records code dependencies only.  It does not claim real-environment
acceptance; Layer 2 completion remains BLOCKED until all four evidence kinds
match this binding.
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

from app.governance.binding import (  # noqa: E402
    TASK_1_3_BINDING_SCOPE,
    TASK_1_3_REQUIRED_KINDS,
    DependencyDigest,
    DependencyDigestKind,
    RevisionBinding,
    digest_file,
)
from app.governance.events import GovernanceEventLog  # noqa: E402
from app.governance.store import GovernanceObjectStore  # noqa: E402

REVISION_BINDING_KIND = "revision_binding"
DEPENDENCIES: tuple[tuple[DependencyDigestKind, str], ...] = (
    (DependencyDigestKind.REPLAY, "backend/scripts/replay_all_days.py"),
    (DependencyDigestKind.EVIDENCE_IDENTITY, "backend/app/services/evidence/identity.py"),
    (DependencyDigestKind.HISTORICAL_QUERY, "backend/app/services/evidence/historical_query.py"),
    (DependencyDigestKind.LIVE_SELECTOR, "backend/app/services/evidence/selectors.py"),
    (DependencyDigestKind.MIGRATION_CONTRACT, "backend/alembic/versions/0004_evidence_provenance.py"),
    (DependencyDigestKind.ASSOCIATED_TESTS, "backend/tests/test_historical_reconstruction_replay.py"),
    (DependencyDigestKind.ASSOCIATED_TESTS, "backend/tests/test_evidence_identity.py"),
    (DependencyDigestKind.ASSOCIATED_TESTS, "backend/tests/evidence/test_selectors.py"),
    (DependencyDigestKind.ASSOCIATED_TESTS, "backend/tests/evidence/test_historical_query.py"),
    (DependencyDigestKind.ASSOCIATED_TESTS, "backend/tests/evidence/test_property_s1_evidence.py"),
    (DependencyDigestKind.ASSOCIATED_TESTS, "backend/tests/evidence/test_property_s1_revision_binding.py"),
)


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def current_task_1_3_binding() -> RevisionBinding:
    binding = RevisionBinding(
        scope_id=TASK_1_3_BINDING_SCOPE,
        digests=tuple(
            DependencyDigest(
                kind=kind,
                locator=path,
                content_sha256=digest_file(REPO_ROOT / path),
            )
            for kind, path in DEPENDENCIES
        ),
    )
    binding.require_kinds(TASK_1_3_REQUIRED_KINDS)
    return binding


def freeze_task_1_3_binding(store: GovernanceObjectStore, *, now: datetime, code_revision: str):
    binding = current_task_1_3_binding()
    existing = [
        item
        for item in store.iter_kind(REVISION_BINDING_KIND)
        if item.payload.get("scope_id") == TASK_1_3_BINDING_SCOPE
    ]
    for item in existing:
        if item.payload.get("binding_hash") == binding.binding_hash:
            return item, binding, False
    supersedes = existing[-1].object_id if existing else None
    payload = {
        **binding.canonical_payload(),
        "binding_hash": binding.binding_hash,
        "code_state": "IN_PROGRESS_PENDING_PROOF_EVIDENCE",
        "environment_acceptance_state": "BLOCKED",
        "required_layer_2_evidence": [
            "migration_apply_no_op",
            "real_prefect_replay",
            "persisted_historical_query",
            "persisted_live_exclusion",
        ],
    }
    envelope = store.put(
        kind=REVISION_BINDING_KIND,
        payload=payload,
        created_at=now,
        created_by="kiro-cli:freeze-task-1-3-binding",
        code_revision=code_revision,
        supersedes_id=supersedes,
    )
    GovernanceEventLog(store).append(
        event_type="TASK_1_3_REVISION_BINDING_FROZEN",
        actor="kiro-cli:freeze-task-1-3-binding",
        code_revision=code_revision,
        recorded_at=now,
        payload={
            "binding_object_id": envelope.object_id,
            "binding_hash": binding.binding_hash,
            "code_state": "IN_PROGRESS_PENDING_PROOF_EVIDENCE",
            "environment_acceptance_state": "BLOCKED",
        },
        referenced_object_ids=(envelope.object_id,),
    )
    return envelope, binding, True


def main() -> int:
    store = GovernanceObjectStore()
    envelope, binding, created = freeze_task_1_3_binding(
        store,
        now=datetime.now(timezone.utc),
        code_revision=_git_head(),
    )
    print(
        json.dumps(
            {
                "status": "frozen" if created else "already_frozen",
                "binding_object_id": envelope.object_id,
                "binding_hash": binding.binding_hash,
                "digest_kinds": sorted(kind.value for kind in binding.kinds()),
                "code_state": "IN_PROGRESS_PENDING_PROOF_EVIDENCE",
                "environment_acceptance_state": "BLOCKED",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

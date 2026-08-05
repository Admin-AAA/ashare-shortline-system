"""Freeze Wave 1 canonical-execution shadow thresholds before sampling.

The manifest is a non-production, content-addressed governance object.  It does
not run a shadow sample, change a read path, connect to a database, or activate
a feature flag.  Re-running returns the already frozen manifest.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.governance.events import GovernanceEventLog
from app.governance.manifests import SHADOW_THRESHOLD_KIND, validate_manifest_payload
from app.governance.store import GovernanceObjectStore

REGISTRY_SNAPSHOT_ID = (
    "registry_snapshot:sha256:"
    "c5cf11f0e46330b6ac2ff457423c514b256b680254ff03b888095189c2b2bfee"
)
STABLE_MANIFEST_KEY = "shadow-threshold.canonical-execution.v1"
TARGET_OPERATION_ID = "projection_cutover.production_read_v1"


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _rules(window_start: datetime, window_end: datetime) -> list[dict[str, object]]:
    dimensions = [
        "recommendation_identity",
        "source_snapshot_id",
        "execution_policy_fingerprint",
        "code_revision",
        "adapter_role",
    ]
    mismatch_metrics = (
        "entry_date_mismatch_count",
        "entry_price_mismatch_count",
        "exit_date_mismatch_count",
        "exit_price_mismatch_count",
        "cost_mismatch_count",
        "position_size_mismatch_count",
        "exit_reason_mismatch_count",
        "net_return_mismatch_count",
    )
    rules = [
        {
            "metric_id": metric,
            "comparator": "==",
            "threshold_decimal": 0.0,
            "aggregation": "SUM",
            "sample_window_start": window_start.isoformat(),
            "sample_window_end": window_end.isoformat(),
            "minimum_sample": 100,
            "failure_action": "BLOCK_CUTOVER",
            "identity_dimensions": dimensions,
        }
        for metric in mismatch_metrics
    ]
    rules.append(
        {
            "metric_id": "t_plus_one_same_day_exit_count",
            "comparator": "==",
            "threshold_decimal": 0.0,
            "aggregation": "SUM",
            "sample_window_start": window_start.isoformat(),
            "sample_window_end": window_end.isoformat(),
            "minimum_sample": 100,
            "failure_action": "BLOCK_CUTOVER",
            "identity_dimensions": dimensions,
        }
    )
    return rules


def freeze_shadow_threshold_manifest(
    store: GovernanceObjectStore,
    *,
    now: datetime,
    code_revision: str,
):
    existing = [
        envelope
        for envelope in store.iter_kind(SHADOW_THRESHOLD_KIND)
        if envelope.payload.get("stable_manifest_key") == STABLE_MANIFEST_KEY
    ]
    if existing:
        manifest = existing[-1]
        validate_manifest_payload(SHADOW_THRESHOLD_KIND, manifest.payload)
        return manifest, False

    frozen_at = now.astimezone(timezone.utc)
    payload = {
        "manifest_kind": SHADOW_THRESHOLD_KIND,
        "stable_manifest_key": STABLE_MANIFEST_KEY,
        "registry_snapshot_id": REGISTRY_SNAPSHOT_ID,
        "frozen_at": frozen_at.isoformat(),
        "applies_to_code_revision": code_revision,
        "supersedes_manifest_id": None,
        "secret_handle_ids": [],
        "target_operation_id": TARGET_OPERATION_ID,
        "source_projection_id": "execution-legacy-authoritative-v1",
        "candidate_projection_id": "canonical-execution-shadow-v2",
        "sample_semantics": "append_only_old_path_authoritative",
        "rules": _rules(frozen_at, frozen_at + timedelta(days=7)),
    }
    validate_manifest_payload(SHADOW_THRESHOLD_KIND, payload)
    manifest = store.put(
        kind=SHADOW_THRESHOLD_KIND,
        payload=payload,
        created_at=frozen_at,
        created_by="kiro-cli:freeze-wave1-shadow-thresholds",
        code_revision=code_revision,
        referenced_object_ids=(REGISTRY_SNAPSHOT_ID,),
    )
    GovernanceEventLog(store).append(
        event_type="SHADOW_THRESHOLD_MANIFEST_FROZEN",
        actor="kiro-cli:freeze-wave1-shadow-thresholds",
        code_revision=code_revision,
        recorded_at=frozen_at,
        payload={
            "manifest_id": manifest.object_id,
            "stable_manifest_key": STABLE_MANIFEST_KEY,
            "target_operation_id": TARGET_OPERATION_ID,
            "first_shadow_sample_not_started": True,
        },
        referenced_object_ids=(manifest.object_id, REGISTRY_SNAPSHOT_ID),
    )
    return manifest, True


def main() -> int:
    store = GovernanceObjectStore()
    manifest, created = freeze_shadow_threshold_manifest(
        store,
        now=datetime.now(timezone.utc),
        code_revision=_git_head(),
    )
    print(
        json.dumps(
            {
                "status": "frozen" if created else "already_frozen",
                "manifest_id": manifest.object_id,
                "evidence_uri": store.evidence_uri_for(manifest.object_id),
                "target_operation_id": TARGET_OPERATION_ID,
                "shadow_sample_executed": False,
                "environment_acceptance_state": "BLOCKED",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""S0 freeze sequence: baseline audit, audit inventory, registry, release scope.

This module performs the one-time, ordered freeze that must complete before
any new behavior-changing work (tasks 0.1–0.4).  Every check appends immutable
objects and events; nothing here rewrites or refreshes a captured baseline —
if order, hash, or revision cannot be proven, the audit records
``BASELINE_FREEZE_ORDER_UNPROVEN`` and the baseline stays a found artifact.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.events import GovernanceEventLog
from app.governance.registries import registry_snapshot_payload
from app.governance.scope import ReleaseScopeEntry, ReleaseScopeManifest
from app.governance.store import GovernanceObjectStore


BASELINE_ARTIFACT_KIND = "baseline_artifact"
AUDIT_INVENTORY_KIND = "audit_inventory"
GAP_RECORD_KIND = "gap_record"
REGISTRY_SNAPSHOT_KIND = "registry_snapshot"
RELEASE_SCOPE_KIND = "release_scope_manifest"
BASELINE_VERIFICATION_KIND = "baseline_verification_evidence"

_EVENT_BOOTSTRAP = "GOVERNANCE_BOOTSTRAP"
_EVENT_BASELINE_AUDIT = "BASELINE_AUDIT_VERIFIED"
_EVENT_BASELINE_ORDER_UNPROVEN = "BASELINE_FREEZE_ORDER_UNPROVEN"
_EVENT_INVENTORY_FROZEN = "AUDIT_INVENTORY_FROZEN"
_EVENT_REGISTRY_FROZEN = "REGISTRY_FROZEN"
_EVENT_SCOPE_FROZEN = "RELEASE_SCOPE_FROZEN"


def _canonical_json(value: Any) -> str:
    """Byte-compatible with the capture scripts' digest algorithm."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    import hashlib

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _aware_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceError("invalid_timestamp", "freeze clock must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class BaselineAuditResult:
    """Verification facts about one captured baseline artifact."""

    artifact_path: str
    artifact_sha256: str
    captured_at: str
    captured_revision: str
    captured_branch: str
    section_digests_match: bool
    revision_exists_in_history: bool
    schema_version_ok: bool
    task_matches: bool
    freeze_order_provable: bool
    current_revision: str
    current_branch: str
    commits_since_capture: tuple[str, ...]
    worktree_status_matches_capture: bool
    delta_note: str

    @property
    def reusable_as_canonical_baseline(self) -> bool:
        return (
            self.section_digests_match
            and self.revision_exists_in_history
            and self.schema_version_ok
            and self.task_matches
            and self.freeze_order_provable
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "captured_at": self.captured_at,
            "captured_branch": self.captured_branch,
            "captured_revision": self.captured_revision,
            "commits_since_capture": list(self.commits_since_capture),
            "current_branch": self.current_branch,
            "current_revision": self.current_revision,
            "delta_note": self.delta_note,
            "freeze_order_provable": self.freeze_order_provable,
            "reusable_as_canonical_baseline": self.reusable_as_canonical_baseline,
            "revision_exists_in_history": self.revision_exists_in_history,
            "schema_version_ok": self.schema_version_ok,
            "section_digests_match": self.section_digests_match,
            "task_matches": self.task_matches,
            "worktree_status_matches_capture": self.worktree_status_matches_capture,
        }


def audit_baseline_artifact(
    artifact_path: str | Path,
    *,
    expected_task_prefix: str,
    revision_exists: Callable[[str], bool],
    commits_between: Callable[[str, str], Iterable[str]],
    current_revision: str,
    current_branch: str,
    current_status_digest: str,
    first_new_behavior_change_at: datetime | None,
    delta_note: str,
) -> tuple[BaselineAuditResult, dict[str, Any]]:
    """Re-verify one captured baseline without mutating it."""

    path = Path(artifact_path)
    if not path.is_file():
        raise GovernanceError(
            "baseline_missing",
            f"baseline artifact {path} does not exist",
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    repository = document.get("repository") or {}
    captured_revision = str(repository.get("git_revision") or "")
    captured_branch = str(repository.get("branch") or "")
    captured_at_raw = str(document.get("captured_at") or "")
    try:
        captured_at = datetime.fromisoformat(captured_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError(
            "invalid_timestamp",
            f"baseline {path.name} has an unparseable captured_at",
        ) from exc

    declared_digests = document.get("section_digests") or {}
    recomputed = {
        section: _digest(document.get(section))
        for section in declared_digests
    }
    section_match = bool(declared_digests) and all(
        declared_digests[section] == digest for section, digest in recomputed.items()
    )

    revision_ok = bool(captured_revision) and bool(revision_exists(captured_revision))
    schema_ok = document.get("schema_version") == 1
    task_ok = str(document.get("task") or "").startswith(expected_task_prefix)
    status_matches = bool(repository.get("worktree_status_digest")) and (
        repository.get("worktree_status_digest") == current_status_digest
    )
    if first_new_behavior_change_at is not None:
        order_provable = captured_at <= _aware_now(first_new_behavior_change_at)
    else:
        order_provable = True

    commits_since = (
        tuple(commits_between(captured_revision, current_revision))
        if revision_ok and captured_revision != current_revision
        else ()
    )

    result = BaselineAuditResult(
        artifact_path=str(path),
        artifact_sha256=_file_sha256(path),
        captured_at=captured_at_raw,
        captured_revision=captured_revision,
        captured_branch=captured_branch,
        section_digests_match=section_match,
        revision_exists_in_history=revision_ok,
        schema_version_ok=schema_ok,
        task_matches=task_ok,
        freeze_order_provable=order_provable,
        current_revision=current_revision,
        current_branch=current_branch,
        commits_since_capture=commits_since,
        worktree_status_matches_capture=status_matches,
        delta_note=delta_note,
    )
    return result, document


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """One audited Existing_Artifact with its reuse decision."""

    path: str
    workspace_status: str
    content_sha256: str | None
    decision: str  # REUSE | RECONCILE | GAP
    rationale: str
    linked_tests: tuple[str, ...] = field(default_factory=tuple)
    requirement_refs: tuple[str, ...] = field(default_factory=tuple)
    gap_record_id: str | None = None

    def __post_init__(self) -> None:
        if self.decision not in {"REUSE", "RECONCILE", "GAP"}:
            raise GovernanceError(
                "invalid_reuse_decision",
                f"decision must be REUSE/RECONCILE/GAP, got {self.decision!r}",
            )
        if self.decision == "GAP" and not self.rationale.strip():
            raise GovernanceError(
                "gap_record_required",
                "GAP decisions require a reproducible rationale",
            )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "content_sha256": self.content_sha256,
            "decision": self.decision,
            "gap_record_id": self.gap_record_id,
            "linked_tests": list(self.linked_tests),
            "path": self.path,
            "rationale": self.rationale,
            "requirement_refs": list(self.requirement_refs),
            "workspace_status": self.workspace_status,
        }


def _find_by_payload(
    store: GovernanceObjectStore,
    kind: str,
    payload: Mapping[str, Any],
) -> ImmutableEnvelope | None:
    from app.engine.canonical import canonical_fingerprint

    target = canonical_fingerprint(payload)
    for envelope in store.iter_kind(kind):
        if envelope.payload_sha256 == target:
            return envelope
    return None


def _put_once(
    store: GovernanceObjectStore,
    *,
    kind: str,
    payload: Mapping[str, Any],
    created_at: datetime,
    created_by: str,
    code_revision: str,
    referenced_object_ids: tuple[str, ...] = (),
) -> tuple[ImmutableEnvelope, bool]:
    """Idempotent freeze: identical payloads reuse the stored object."""

    existing = _find_by_payload(store, kind, payload)
    if existing is not None:
        return existing, False
    envelope = store.put(
        kind=kind,
        payload=payload,
        created_at=created_at,
        created_by=created_by,
        code_revision=code_revision,
        referenced_object_ids=referenced_object_ids,
    )
    return envelope, True


@dataclass(frozen=True, slots=True)
class S0FreezeReceipt:
    bootstrap_event_id: str
    baseline_object_ids: tuple[str, ...]
    baseline_audit_object_ids: tuple[str, ...]
    barrier_event_id: str
    inventory_object_id: str
    gap_record_object_ids: tuple[str, ...]
    registry_snapshot_id: str
    release_scope_manifest_id: str

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "barrier_event_id": self.barrier_event_id,
            "baseline_audit_object_ids": list(self.baseline_audit_object_ids),
            "baseline_object_ids": list(self.baseline_object_ids),
            "bootstrap_event_id": self.bootstrap_event_id,
            "gap_record_object_ids": list(self.gap_record_object_ids),
            "inventory_object_id": self.inventory_object_id,
            "registry_snapshot_id": self.registry_snapshot_id,
            "release_scope_manifest_id": self.release_scope_manifest_id,
        }


def freeze_s0(
    *,
    store: GovernanceObjectStore,
    events: GovernanceEventLog,
    now: datetime,
    actor: str,
    code_revision: str,
    baseline_audits: tuple[tuple[BaselineAuditResult, dict[str, Any]], ...],
    bootstrap_note: Mapping[str, Any],
    inventory_entries: tuple[InventoryEntry, ...],
    gap_records: tuple[Mapping[str, Any], ...],
    scope_entries: tuple[ReleaseScopeEntry, ...],
) -> S0FreezeReceipt:
    """Run the ordered S0 freeze.  Raises if any baseline is not provable."""

    moment = _aware_now(now)

    bootstrap = events.append(
        event_type=_EVENT_BOOTSTRAP,
        actor=actor,
        code_revision=code_revision,
        recorded_at=moment,
        payload=dict(bootstrap_note),
    )

    baseline_ids: list[str] = []
    audit_ids: list[str] = []
    order_failures: list[str] = []
    for result, document in baseline_audits:
        baseline_envelope, _ = _put_once(
            store,
            kind=BASELINE_ARTIFACT_KIND,
            payload={
                "artifact": document,
                "artifact_path": result.artifact_path,
                "artifact_sha256": result.artifact_sha256,
            },
            created_at=moment,
            created_by=actor,
            code_revision=code_revision,
        )
        baseline_ids.append(baseline_envelope.object_id)
        audit_envelope, _ = _put_once(
            store,
            kind=BASELINE_VERIFICATION_KIND,
            payload=result.canonical_payload(),
            created_at=moment,
            created_by=actor,
            code_revision=code_revision,
            referenced_object_ids=(baseline_envelope.object_id,),
        )
        audit_ids.append(audit_envelope.object_id)
        if not result.reusable_as_canonical_baseline:
            order_failures.append(result.artifact_path)

    if order_failures:
        barrier = events.append(
            event_type=_EVENT_BASELINE_ORDER_UNPROVEN,
            actor=actor,
            code_revision=code_revision,
            recorded_at=moment,
            payload={
                "failed_artifacts": sorted(order_failures),
                "note": "S0 stays BLOCKED; baselines must not be refreshed to fix this.",
            },
            referenced_object_ids=tuple(audit_ids),
        )
        raise GovernanceError(
            "baseline_freeze_order_unproven",
            f"baseline freeze order/hash/revision unproven for: {order_failures}",
        ) from None

    barrier = events.append(
        event_type=_EVENT_BASELINE_AUDIT,
        actor=actor,
        code_revision=code_revision,
        recorded_at=moment,
        payload={
            "baseline_object_ids": baseline_ids,
            "baseline_audit_object_ids": audit_ids,
        },
        referenced_object_ids=tuple(baseline_ids + audit_ids),
    )

    gap_ids: list[str] = []
    for record in gap_records:
        gap_envelope, _ = _put_once(
            store,
            kind=GAP_RECORD_KIND,
            payload=dict(record),
            created_at=moment,
            created_by=actor,
            code_revision=code_revision,
        )
        gap_ids.append(gap_envelope.object_id)

    inventory_payload = {
        "entries": [entry.canonical_payload() for entry in inventory_entries],
        "gap_record_object_ids": gap_ids,
    }
    inventory_envelope, _ = _put_once(
        store,
        kind=AUDIT_INVENTORY_KIND,
        payload=inventory_payload,
        created_at=moment,
        created_by=actor,
        code_revision=code_revision,
        referenced_object_ids=tuple(gap_ids),
    )
    events.append(
        event_type=_EVENT_INVENTORY_FROZEN,
        actor=actor,
        code_revision=code_revision,
        recorded_at=moment,
        payload={"inventory_object_id": inventory_envelope.object_id},
        referenced_object_ids=(inventory_envelope.object_id,),
    )

    registry_payload = registry_snapshot_payload()
    registry_envelope, _ = _put_once(
        store,
        kind=REGISTRY_SNAPSHOT_KIND,
        payload=registry_payload,
        created_at=moment,
        created_by=actor,
        code_revision=code_revision,
    )
    events.append(
        event_type=_EVENT_REGISTRY_FROZEN,
        actor=actor,
        code_revision=code_revision,
        recorded_at=moment,
        payload={"registry_snapshot_id": registry_envelope.object_id},
        referenced_object_ids=(registry_envelope.object_id,),
    )

    if not scope_entries:
        raise GovernanceError(
            "scope_manifest_required",
            "S0 freeze requires a fully populated ReleaseScopeManifest",
        )
    scope_manifest = ReleaseScopeManifest(
        registry_snapshot_id=registry_envelope.object_id,
        entries=scope_entries,
        behavior_change_barrier_event_id=barrier.object_id,
    )
    scope_payload = scope_manifest.canonical_payload()
    scope_envelope, _ = _put_once(
        store,
        kind=RELEASE_SCOPE_KIND,
        payload=scope_payload,
        created_at=moment,
        created_by=actor,
        code_revision=code_revision,
        referenced_object_ids=(registry_envelope.object_id, barrier.object_id),
    )
    events.append(
        event_type=_EVENT_SCOPE_FROZEN,
        actor=actor,
        code_revision=code_revision,
        recorded_at=moment,
        payload={"release_scope_manifest_id": scope_envelope.object_id},
        referenced_object_ids=(scope_envelope.object_id,),
    )

    return S0FreezeReceipt(
        bootstrap_event_id=bootstrap.object_id,
        baseline_object_ids=tuple(baseline_ids),
        baseline_audit_object_ids=tuple(audit_ids),
        barrier_event_id=barrier.object_id,
        inventory_object_id=inventory_envelope.object_id,
        gap_record_object_ids=tuple(gap_ids),
        registry_snapshot_id=registry_envelope.object_id,
        release_scope_manifest_id=scope_envelope.object_id,
    )


__all__ = [
    "AUDIT_INVENTORY_KIND",
    "BASELINE_ARTIFACT_KIND",
    "BASELINE_VERIFICATION_KIND",
    "BaselineAuditResult",
    "GAP_RECORD_KIND",
    "InventoryEntry",
    "REGISTRY_SNAPSHOT_KIND",
    "RELEASE_SCOPE_KIND",
    "S0FreezeReceipt",
    "audit_baseline_artifact",
    "freeze_s0",
]

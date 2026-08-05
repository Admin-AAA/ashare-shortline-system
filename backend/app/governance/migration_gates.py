"""Preflight admission gates binding migration plans to frozen restore/capacity manifests.

Task 0.8 of the production-evidence-risk-hardening spec: no migration
preflight may run before a Restore_Capacity_Criteria_Manifest that applies to
the plan is frozen in the governance store.  Real observations (metadata
locks, capacity, disk headroom) are captured as append-only
``restore_capacity_observation`` objects; manifest thresholds are never
rewritten from observations.  This module performs no database access — it
only reads and appends governance objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping

from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.manifests import (
    AdmissionVerdict,
    RESTORE_CAPACITY_KIND,
    validate_manifest_payload,
)
from app.governance.store import GovernanceObjectStore


DEFAULT_MIGRATION_PLAN_KEY = "production-hardening-migration-plan"
RESTORE_CAPACITY_OBSERVATION_KIND = "restore_capacity_observation"

REASON_MANIFEST_MISSING = "restore_capacity_manifest_missing"
REASON_FROZEN_AFTER_TRIGGER = "manifest_frozen_after_trigger_point"
_REASON_INVALID_PREFIX = "restore_capacity_manifest_invalid"

# Credential-shaped values must never reach a persisted observation.  Same
# pattern family as manifests._SECRET_VALUE_PATTERNS and the capture script's
# _FORBIDDEN_VALUE_PATTERNS: observations are allowlisted projections, and any
# string that still looks like a secret fails closed.
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*"),
    re.compile(r"(?i)\b(?:mysql|postgres(?:ql)?|mariadb)\+?[^:]*://[^\s/:@]+:[^\s@]+@"),
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token)\s*[=:]\s*\S+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


def _aware_utc(value: object, *, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GovernanceError(
                "invalid_timestamp",
                f"{field_name} must be an ISO timestamp",
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceError(
            "invalid_timestamp",
            f"{field_name} must be a timezone-aware datetime",
        )
    return value.astimezone(timezone.utc)


def _canonical_moment(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _assert_no_secret_values(value: Any, *, path: str = "$") -> None:
    if isinstance(value, str):
        for pattern in _FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(value):
                raise GovernanceError(
                    "secret_value_in_observation",
                    f"observation field {path} carries a credential-like value",
                )
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _assert_no_secret_values(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_secret_values(item, path=f"{path}[{index}]")


def restore_capacity_manifest_for(
    store: GovernanceObjectStore,
    migration_plan_key: str,
) -> ImmutableEnvelope | None:
    """Return the latest frozen Restore_Capacity_Criteria_Manifest for a plan.

    Only stored objects of kind ``restore_capacity_criteria_manifest`` whose
    payload names ``migration_plan_key`` apply; each candidate is re-validated
    with ``manifests.validate_manifest_payload`` and an invalid candidate
    fails closed by raising ``GovernanceError``.  When several valid manifests
    apply, the one with the newest ``frozen_at`` wins (ties break on the
    object id so the choice is deterministic).  ``None`` means no applicable
    frozen manifest exists.
    """

    if not isinstance(migration_plan_key, str) or not migration_plan_key.strip():
        raise GovernanceError(
            "invalid_migration_plan_key",
            "migration_plan_key must be a non-empty stable key",
        )
    candidates: list[ImmutableEnvelope] = []
    for envelope in store.iter_kind(RESTORE_CAPACITY_KIND):
        if envelope.payload.get("migration_plan_key") != migration_plan_key:
            continue
        validate_manifest_payload(RESTORE_CAPACITY_KIND, envelope.payload)
        candidates.append(envelope)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda envelope: (
            _aware_utc(envelope.payload.get("frozen_at"), field_name="frozen_at"),
            envelope.object_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class PreflightAdmissionVerdict:
    """Structured admission result for one migration preflight attempt."""

    verdict: AdmissionVerdict
    reasons: tuple[str, ...]
    migration_plan_key: str
    checked_at: datetime
    manifest: ImmutableEnvelope | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "checked_at": _canonical_moment(self.checked_at),
            "manifest_object_id": self.manifest.object_id if self.manifest else None,
            "migration_plan_key": self.migration_plan_key,
            "reasons": list(self.reasons),
            "verdict": self.verdict.value,
        }


def preflight_admission(
    store: GovernanceObjectStore,
    migration_plan_key: str,
    now: datetime,
) -> PreflightAdmissionVerdict:
    """Decide whether a migration preflight may start at trigger point ``now``.

    The verdict is BLOCKED when no applicable frozen manifest exists
    (``restore_capacity_manifest_missing``), when a stored candidate is
    invalid (``restore_capacity_manifest_invalid:<code>``), or when the
    manifest was frozen at or after the trigger point
    (``manifest_frozen_after_trigger_point``).  Otherwise the verdict is
    ADMITTED and carries the manifest envelope.
    """

    trigger = _aware_utc(now, field_name="now")
    try:
        manifest = restore_capacity_manifest_for(store, migration_plan_key)
    except GovernanceError as exc:
        return PreflightAdmissionVerdict(
            verdict=AdmissionVerdict.BLOCKED,
            reasons=(f"{_REASON_INVALID_PREFIX}:{exc.code}",),
            migration_plan_key=migration_plan_key,
            checked_at=trigger,
        )
    if manifest is None:
        return PreflightAdmissionVerdict(
            verdict=AdmissionVerdict.BLOCKED,
            reasons=(REASON_MANIFEST_MISSING,),
            migration_plan_key=migration_plan_key,
            checked_at=trigger,
        )
    frozen_at = _aware_utc(manifest.payload.get("frozen_at"), field_name="frozen_at")
    if frozen_at >= trigger:
        return PreflightAdmissionVerdict(
            verdict=AdmissionVerdict.BLOCKED,
            reasons=(REASON_FROZEN_AFTER_TRIGGER,),
            migration_plan_key=migration_plan_key,
            checked_at=trigger,
            manifest=manifest,
        )
    return PreflightAdmissionVerdict(
        verdict=AdmissionVerdict.ADMITTED,
        reasons=(),
        migration_plan_key=migration_plan_key,
        checked_at=trigger,
        manifest=manifest,
    )


def observation_readings_from_preflight(
    preflight_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Allowlisted lock/capacity/disk projection of a preflight artifact.

    Only stable, non-sensitive fields are copied; raw payloads, variables, and
    environment details never flow into an observation object.
    """

    manifest = preflight_payload.get("manifest") or {}
    preflight = preflight_payload.get("preflight") or {}
    locks = preflight.get("metadata_locks") or {}
    disk = preflight.get("disk_headroom") or {}
    scale = preflight.get("table_scale") or {}
    return {
        "plan_hash": manifest.get("manifest_checksum"),
        "metadata_locks": {
            "status": locks.get("status"),
            "pending_count": locks.get("pending_count"),
            "granted_exclusive_count": locks.get("granted_exclusive_count"),
        },
        "disk_headroom": {
            "status": disk.get("status"),
            "affected_table_bytes": disk.get("affected_table_bytes"),
            "conservative_required_free_bytes": disk.get("conservative_required_free_bytes"),
            "filesystem_free_bytes": disk.get("filesystem_free_bytes"),
        },
        "table_scale": {
            "status": scale.get("status"),
            "affected_total_bytes": scale.get("affected_total_bytes"),
            "observed_table_count": len(scale.get("tables") or []),
        },
    }


def operation_impact_register(
    preflight_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Join the additive plan with observed scale/lock/disk readings.

    Emits, per additive operation, the target object, estimated rows,
    lock/capacity impact, batch checksum, precondition, postcondition, and the
    plan hash — the per-migration safety record task 0.8 requires.  All fields
    are drawn from the reviewed plan manifest plus allowlisted preflight
    readings, so the register is safe to log or persist.
    """

    manifest = preflight_payload.get("manifest") or {}
    preflight = preflight_payload.get("preflight") or {}
    plan_hash = manifest.get("manifest_checksum")

    scale_by_table = {
        item.get("table"): item
        for item in (preflight.get("table_scale") or {}).get("tables") or []
    }
    lock_counts: dict[Any, dict[str, int]] = {}
    for item in (preflight.get("metadata_locks") or {}).get("locks") or []:
        entry = lock_counts.setdefault(
            item.get("table"), {"pending": 0, "granted_exclusive": 0}
        )
        count = int(item.get("count") or 0)
        if str(item.get("lock_status") or "").upper() == "PENDING":
            entry["pending"] += count
        elif "EXCLUSIVE" in str(item.get("lock_type") or "").upper():
            entry["granted_exclusive"] += count

    disk = preflight.get("disk_headroom") or {}
    locks = preflight.get("metadata_locks") or {}
    capacity_summary = {
        "lock_status": locks.get("status"),
        "disk_status": disk.get("status"),
        "affected_table_bytes": disk.get("affected_table_bytes"),
        "conservative_required_free_bytes": disk.get("conservative_required_free_bytes"),
        "filesystem_free_bytes": disk.get("filesystem_free_bytes"),
    }

    operations: list[dict[str, Any]] = []
    for batch in manifest.get("batches") or []:
        for operation in batch.get("operations") or []:
            scale = scale_by_table.get(operation.get("table")) or {}
            lock_entry = lock_counts.get(
                operation.get("table"), {"pending": 0, "granted_exclusive": 0}
            )
            operations.append(
                {
                    "operation_id": operation.get("operation_id"),
                    "kind": operation.get("kind"),
                    "target_object": operation.get("object_name"),
                    "table": operation.get("table"),
                    "estimated_rows": int(scale.get("estimated_rows") or 0),
                    "affected_bytes": int(scale.get("data_bytes") or 0)
                    + int(scale.get("index_bytes") or 0),
                    "lock_pending_count": lock_entry["pending"],
                    "lock_granted_exclusive_count": lock_entry["granted_exclusive"],
                    "batch_checksum": batch.get("manifest_checksum"),
                    "precondition": operation.get("precondition"),
                    "postcondition": operation.get("postcondition"),
                    "plan_hash": plan_hash,
                }
            )
    return {
        "plan_hash": plan_hash,
        "capacity_summary": capacity_summary,
        "operations": operations,
    }


def record_restore_capacity_observation(
    store: GovernanceObjectStore,
    *,
    migration_plan_key: str,
    manifest_object_id: str,
    captured_at: datetime,
    readings: Mapping[str, Any],
    impact_register: Mapping[str, Any] | None = None,
    actor: str,
    code_revision: str,
) -> ImmutableEnvelope:
    """Append one immutable ``restore_capacity_observation`` evidence attempt.

    The observation references the admitted manifest but never rewrites it:
    thresholds live only in the frozen manifest, and this function performs no
    store reads or writes other than the single append-only ``put``.  The
    payload is scanned for credential-shaped values before persisting and
    fails closed on any match.
    """

    moment = _aware_utc(captured_at, field_name="captured_at")
    payload = {
        "observation_kind": RESTORE_CAPACITY_OBSERVATION_KIND,
        "migration_plan_key": migration_plan_key,
        "restore_capacity_manifest_id": manifest_object_id,
        "captured_at": _canonical_moment(moment),
        "readings": dict(readings),
        "impact_register": dict(impact_register) if impact_register is not None else None,
        "threshold_writeback": "forbidden",
        "note": (
            "point-in-time observation; Restore_Capacity_Criteria_Manifest "
            "thresholds are never rewritten from observations"
        ),
    }
    _assert_no_secret_values(payload)
    return store.put(
        kind=RESTORE_CAPACITY_OBSERVATION_KIND,
        payload=payload,
        created_at=moment,
        created_by=actor,
        code_revision=code_revision,
        referenced_object_ids=(manifest_object_id,),
    )


__all__ = [
    "DEFAULT_MIGRATION_PLAN_KEY",
    "REASON_FROZEN_AFTER_TRIGGER",
    "REASON_MANIFEST_MISSING",
    "RESTORE_CAPACITY_OBSERVATION_KIND",
    "PreflightAdmissionVerdict",
    "observation_readings_from_preflight",
    "operation_impact_register",
    "preflight_admission",
    "record_restore_capacity_observation",
    "restore_capacity_manifest_for",
]

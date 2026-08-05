"""Exact, immutable approval contracts for one approval-gated operation.

A :class:`SeparateWriteApproval` is deliberately narrower than release scope:
it authorizes exactly one operation instance, target environment, plan hash,
artifact checksum, actor and time window.  Matching is equality-only; there is
no operation-kind, dependency or feature-flag inheritance.

This module is governance-only.  It stores identities and hashes, never secret
values, and exposes no production-write, database or migration adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any, Mapping

from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.events import GovernanceEventLog
from app.governance.registries import operation_ids
from app.governance.store import GovernanceObjectStore


SEPARATE_WRITE_APPROVAL_KIND = "separate_write_approval"
APPROVAL_GRANTED_EVENT = "SEPARATE_WRITE_APPROVAL_GRANTED"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]*$")
_ACCEPTANCE_WRITE_PREFIX = "acceptance_write."


def _approval_operation_id(value: object) -> str:
    """Accept gated registry IDs or a non-gated, namespaced acceptance write.

    Layer-2 acceptance fixtures are real writes but are not release operations,
    so adding them to the nine-item Approval-Gated Operation Registry would
    incorrectly alter ReleaseScopeManifest completion semantics.  They still
    require the same exact, expiring, non-transitive approval envelope.
    """

    operation_id = _identity(value, field_name="operation_id")
    if operation_id in operation_ids() or operation_id.startswith(_ACCEPTANCE_WRITE_PREFIX):
        return operation_id
    raise GovernanceError(
        "unknown_operation_id",
        f"operation {operation_id!r} is neither gated nor a namespaced acceptance write",
    )


class ApprovalVerdict(StrEnum):
    VALID = "VALID"
    MISSING = "MISSING"
    MISMATCH = "MISMATCH"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"


def _aware_utc(value: object, *, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GovernanceError(
                "invalid_timestamp", f"{field_name} must be an ISO timestamp"
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceError(
            "invalid_timestamp", f"{field_name} must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


def _moment(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _identity(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise GovernanceError(
            "invalid_approval_identity", f"{field_name} must be a safe non-empty identity"
        )
    return value


def _digest(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise GovernanceError(
            "invalid_approval_digest", f"{field_name} must be a non-empty checksum identity"
        )
    if any(character.isspace() for character in value):
        raise GovernanceError(
            "invalid_approval_digest", f"{field_name} must not contain whitespace"
        )
    return value


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """The exact operation facts presented at an authorization boundary."""

    operation_id: str
    operation_instance_id: str
    target_environment_key: str
    plan_hash: str
    artifact_checksum: str
    actor: str
    feature_flag_id: str | None = None

    def __post_init__(self) -> None:
        _approval_operation_id(self.operation_id)
        for field_name in (
            "operation_instance_id",
            "target_environment_key",
            "actor",
        ):
            _identity(getattr(self, field_name), field_name=field_name)
        _digest(self.plan_hash, field_name="plan_hash")
        _digest(self.artifact_checksum, field_name="artifact_checksum")
        if self.feature_flag_id is not None:
            _identity(self.feature_flag_id, field_name="feature_flag_id")

    def canonical_payload(self) -> dict[str, str | None]:
        return {
            "actor": self.actor,
            "artifact_checksum": self.artifact_checksum,
            "feature_flag_id": self.feature_flag_id,
            "operation_id": self.operation_id,
            "operation_instance_id": self.operation_instance_id,
            "plan_hash": self.plan_hash,
            "target_environment_key": self.target_environment_key,
        }


@dataclass(frozen=True, slots=True)
class SeparateWriteApproval:
    """One non-transitive write grant with an explicit validity window."""

    binding: ApprovalBinding
    issued_at: datetime
    expires_at: datetime
    approved_by: str
    approval_reason_code: str

    def __post_init__(self) -> None:
        issued = _aware_utc(self.issued_at, field_name="issued_at")
        expires = _aware_utc(self.expires_at, field_name="expires_at")
        if expires <= issued:
            raise GovernanceError(
                "invalid_approval_window", "expires_at must be later than issued_at"
            )
        _identity(self.approved_by, field_name="approved_by")
        _identity(self.approval_reason_code, field_name="approval_reason_code")
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "expires_at", expires)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "approval_kind": SEPARATE_WRITE_APPROVAL_KIND,
            "approval_reason_code": self.approval_reason_code,
            "approved_by": self.approved_by,
            "binding": self.binding.canonical_payload(),
            "expires_at": _moment(self.expires_at),
            "issued_at": _moment(self.issued_at),
            "non_transitive": True,
        }


@dataclass(frozen=True, slots=True)
class ApprovalValidation:
    verdict: ApprovalVerdict
    mismatches: tuple[str, ...] = ()
    approval_object_id: str | None = None

    @property
    def authorized(self) -> bool:
        return self.verdict is ApprovalVerdict.VALID

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "approval_object_id": self.approval_object_id,
            "mismatches": list(self.mismatches),
            "verdict": self.verdict.value,
        }


def validate_separate_write_approval(
    approval: SeparateWriteApproval | None,
    *,
    expected: ApprovalBinding,
    at: datetime,
    approval_object_id: str | None = None,
) -> ApprovalValidation:
    """Validate exact equality; a grant can never authorize a related operation."""

    if approval is None:
        return ApprovalValidation(ApprovalVerdict.MISSING, ("approval_missing",))
    moment = _aware_utc(at, field_name="at")
    if moment < approval.issued_at:
        return ApprovalValidation(
            ApprovalVerdict.NOT_YET_VALID,
            ("approval_not_yet_valid",),
            approval_object_id,
        )
    if moment >= approval.expires_at:
        return ApprovalValidation(
            ApprovalVerdict.EXPIRED,
            ("approval_expired",),
            approval_object_id,
        )

    actual = approval.binding.canonical_payload()
    wanted = expected.canonical_payload()
    mismatches = tuple(
        f"{field_name}_mismatch"
        for field_name in wanted
        if actual.get(field_name) != wanted.get(field_name)
    )
    return ApprovalValidation(
        ApprovalVerdict.MISMATCH if mismatches else ApprovalVerdict.VALID,
        mismatches,
        approval_object_id,
    )


def store_separate_write_approval(
    store: GovernanceObjectStore,
    events: GovernanceEventLog,
    *,
    approval: SeparateWriteApproval,
    code_revision: str,
) -> ImmutableEnvelope:
    """Freeze a grant and append an allowlisted audit event.

    The payload contains only stable identities/checksums.  It intentionally
    has no field through which a credential or complete execution payload can
    enter the governance store.
    """

    envelope = store.put(
        kind=SEPARATE_WRITE_APPROVAL_KIND,
        payload=approval.canonical_payload(),
        created_at=approval.issued_at,
        created_by=approval.approved_by,
        code_revision=code_revision,
    )
    events.append(
        event_type=APPROVAL_GRANTED_EVENT,
        actor=approval.approved_by,
        code_revision=code_revision,
        recorded_at=approval.issued_at,
        payload={
            "approval_object_id": envelope.object_id,
            **approval.binding.canonical_payload(),
            "expires_at": _moment(approval.expires_at),
        },
        referenced_object_ids=(envelope.object_id,),
    )
    return envelope


def separate_write_approval_from_envelope(
    envelope: ImmutableEnvelope,
) -> SeparateWriteApproval:
    if envelope.kind != SEPARATE_WRITE_APPROVAL_KIND:
        raise GovernanceError(
            "approval_kind_mismatch",
            f"expected {SEPARATE_WRITE_APPROVAL_KIND}, got {envelope.kind}",
        )
    payload: Mapping[str, Any] = envelope.payload
    if payload.get("approval_kind") != SEPARATE_WRITE_APPROVAL_KIND:
        raise GovernanceError("approval_kind_mismatch", "approval payload kind is invalid")
    if payload.get("non_transitive") is not True:
        raise GovernanceError(
            "approval_transitivity_forbidden", "approval must declare non_transitive=true"
        )
    binding_payload = payload.get("binding")
    if not isinstance(binding_payload, Mapping):
        raise GovernanceError("invalid_approval", "binding must be an object")
    return SeparateWriteApproval(
        binding=ApprovalBinding(
            operation_id=binding_payload.get("operation_id"),
            operation_instance_id=binding_payload.get("operation_instance_id"),
            target_environment_key=binding_payload.get("target_environment_key"),
            plan_hash=binding_payload.get("plan_hash"),
            artifact_checksum=binding_payload.get("artifact_checksum"),
            actor=binding_payload.get("actor"),
            feature_flag_id=binding_payload.get("feature_flag_id"),
        ),
        issued_at=payload.get("issued_at"),
        expires_at=payload.get("expires_at"),
        approved_by=payload.get("approved_by"),
        approval_reason_code=payload.get("approval_reason_code"),
    )


def load_separate_write_approval(
    store: GovernanceObjectStore,
    approval_object_id: str,
) -> SeparateWriteApproval:
    return separate_write_approval_from_envelope(store.get(approval_object_id))


__all__ = [
    "APPROVAL_GRANTED_EVENT",
    "SEPARATE_WRITE_APPROVAL_KIND",
    "ApprovalBinding",
    "ApprovalValidation",
    "ApprovalVerdict",
    "SeparateWriteApproval",
    "load_separate_write_approval",
    "separate_write_approval_from_envelope",
    "store_separate_write_approval",
    "validate_separate_write_approval",
]

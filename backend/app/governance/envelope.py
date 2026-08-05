"""Immutable content-addressed envelope shared by every governance object.

All registries, manifests, baselines, evidence attempts, assessments, and
decisions serialize through this envelope so identity, hashing, and supersede
semantics stay in exactly one place.  Canonical JSON primitives come from
``app.engine.canonical`` — this module adds no second hashing infrastructure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from app.engine.canonical import canonical_fingerprint, canonical_json, canonicalize


GOVERNANCE_SCHEMA_VERSION = 1
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OBJECT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}:sha256:[0-9a-f]{64}$")


class GovernanceError(ValueError):
    """Stable, caller-safe error for governance contract violations."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _require_kind(kind: object) -> str:
    if not isinstance(kind, str) or not _KIND_PATTERN.fullmatch(kind):
        raise GovernanceError(
            "invalid_governance_kind",
            "kind must be a lowercase snake_case identifier",
        )
    return kind


def _require_object_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not _OBJECT_ID_PATTERN.fullmatch(value):
        raise GovernanceError(
            "invalid_object_id",
            f"{field_name} must look like '<kind>:sha256:<64-hex>'",
        )
    return value


def _aware_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceError(
            "invalid_timestamp",
            f"{field_name} must be a timezone-aware datetime",
        )
    return value.astimezone(timezone.utc)


def _canonical_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise GovernanceError("invalid_timestamp", f"{field_name} must be an ISO string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError(
            "invalid_timestamp",
            f"{field_name} must be an ISO timestamp",
        ) from exc
    return _aware_utc(parsed, field_name=field_name)


@dataclass(frozen=True, slots=True)
class ImmutableEnvelope:
    """One immutable governance object with content-addressed identity.

    ``object_id`` is ``<kind>:sha256:<hash>`` where the hash covers every
    header field plus the payload hash, so two envelopes never collide unless
    they are byte-identical in meaning.  Revisions create a new envelope whose
    ``supersedes_id`` points at the previous object; the old object stays
    resolvable forever.
    """

    kind: str
    payload: Mapping[str, Any]
    created_at: datetime
    created_by: str
    code_revision: str
    schema_version: int = GOVERNANCE_SCHEMA_VERSION
    supersedes_id: str | None = None
    referenced_object_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _require_kind(self.kind))
        if isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise GovernanceError(
                "invalid_schema_version",
                "schema_version must be a positive integer",
            )
        # Raises TypeError via canonicalize for unsupported payload values.
        canonicalize(self.payload)
        object.__setattr__(
            self,
            "created_at",
            _aware_utc(self.created_at, field_name="created_at"),
        )
        if not isinstance(self.created_by, str) or not self.created_by.strip():
            raise GovernanceError(
                "invalid_actor",
                "created_by must name the capturing actor",
            )
        if not isinstance(self.code_revision, str) or not self.code_revision.strip():
            raise GovernanceError(
                "invalid_code_revision",
                "code_revision must identify the capturing code",
            )
        if self.supersedes_id is not None:
            _require_object_id(self.supersedes_id, field_name="supersedes_id")
        if not isinstance(self.referenced_object_ids, tuple):
            object.__setattr__(
                self,
                "referenced_object_ids",
                tuple(self.referenced_object_ids),
            )
        for reference in self.referenced_object_ids:
            _require_object_id(reference, field_name="referenced_object_ids")

    @property
    def payload_sha256(self) -> str:
        return canonical_fingerprint(self.payload)

    def header_payload(self) -> dict[str, Any]:
        return {
            "created_at": _canonical_datetime(self.created_at),
            "created_by": self.created_by,
            "code_revision": self.code_revision,
            "kind": self.kind,
            "payload_sha256": self.payload_sha256,
            "referenced_object_ids": sorted(self.referenced_object_ids),
            "schema_version": self.schema_version,
            "supersedes_id": self.supersedes_id,
        }

    @property
    def object_id(self) -> str:
        return f"{self.kind}:sha256:{canonical_fingerprint(self.header_payload())}"

    def to_document(self) -> dict[str, Any]:
        """Return the persisted JSON document (header + payload)."""

        document = self.header_payload()
        document["object_id"] = self.object_id
        document["payload"] = canonicalize(self.payload)
        return document

    def to_canonical_json(self) -> str:
        return canonical_json(self.to_document())

    @staticmethod
    def from_document(document: Mapping[str, Any]) -> ImmutableEnvelope:
        """Rehydrate and fully re-verify a persisted document."""

        if not isinstance(document, Mapping):
            raise GovernanceError("invalid_document", "governance document must be an object")
        envelope = ImmutableEnvelope(
            kind=document.get("kind"),
            payload=document.get("payload"),
            created_at=_parse_datetime(document.get("created_at"), field_name="created_at"),
            created_by=document.get("created_by"),
            code_revision=document.get("code_revision"),
            schema_version=document.get("schema_version"),
            supersedes_id=document.get("supersedes_id"),
            referenced_object_ids=tuple(document.get("referenced_object_ids") or ()),
        )
        declared_id = document.get("object_id")
        if declared_id != envelope.object_id:
            raise GovernanceError(
                "object_id_mismatch",
                "persisted object_id does not match recomputed identity",
            )
        declared_hash = document.get("payload_sha256")
        if declared_hash != envelope.payload_sha256:
            raise GovernanceError(
                "payload_hash_mismatch",
                "persisted payload_sha256 does not match recomputed hash",
            )
        return envelope


__all__ = [
    "GOVERNANCE_SCHEMA_VERSION",
    "GovernanceError",
    "ImmutableEnvelope",
]

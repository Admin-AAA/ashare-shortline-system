"""Append-only content-addressed governance object store.

Objects persist as ``<root>/<kind>/<object-hash>.json`` and are never
rewritten: writing the same identity with identical bytes is a no-op, writing
different bytes under an existing identity is refused.  ``evidence://sha256/``
URIs resolve only to bytes whose recomputed identity matches, which is the
admission rule the spec requires for every Evidence_URI.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.governance.envelope import GovernanceError, ImmutableEnvelope


EVIDENCE_URI_SCHEME = "evidence://sha256/"
_STORE_ENV_VAR = "ASHARE_GOVERNANCE_STORE_ROOT"
DEFAULT_STORE_ROOT = (
    Path(__file__).resolve().parents[3]
    / ".kiro"
    / "specs"
    / "production-evidence-risk-hardening"
    / "governance"
    / "store"
)


def default_store_root() -> Path:
    override = os.environ.get(_STORE_ENV_VAR)
    if override:
        return Path(override)
    return DEFAULT_STORE_ROOT


class GovernanceObjectStore:
    """File-backed immutable store; one instance per root."""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root is not None else default_store_root()

    @property
    def root(self) -> Path:
        return self._root

    def put(
        self,
        *,
        kind: str,
        payload: Mapping[str, Any],
        created_at: datetime,
        created_by: str,
        code_revision: str,
        schema_version: int = 1,
        supersedes_id: str | None = None,
        referenced_object_ids: tuple[str, ...] = (),
    ) -> ImmutableEnvelope:
        """Persist one envelope; returns the stored (verified) envelope."""

        envelope = ImmutableEnvelope(
            kind=kind,
            payload=payload,
            created_at=created_at,
            created_by=created_by,
            code_revision=code_revision,
            schema_version=schema_version,
            supersedes_id=supersedes_id,
            referenced_object_ids=referenced_object_ids,
        )
        path = self._path_for(envelope.object_id)
        document = envelope.to_canonical_json()
        if path.exists():
            existing = path.read_bytes()
            if existing != document.encode("utf-8"):
                raise GovernanceError(
                    "immutable_overwrite_refused",
                    f"object {envelope.object_id} already exists with different bytes",
                )
            return envelope
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(document, encoding="utf-8")
        if path.exists():
            # Concurrent writer won the race; bytes must be identical.
            temporary.unlink(missing_ok=True)
            if path.read_bytes() != document.encode("utf-8"):
                raise GovernanceError(
                    "immutable_overwrite_refused",
                    f"object {envelope.object_id} already exists with different bytes",
                )
            return envelope
        os.replace(temporary, path)
        return envelope

    def get(self, object_id: str) -> ImmutableEnvelope:
        path = self._path_for(object_id)
        if not path.exists():
            raise GovernanceError(
                "object_not_found",
                f"governance object {object_id} is not stored under {self._root}",
            )
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GovernanceError(
                "corrupt_object",
                f"governance object {object_id} is not valid JSON",
            ) from exc
        envelope = ImmutableEnvelope.from_document(document)
        if envelope.object_id != object_id:
            raise GovernanceError(
                "object_id_mismatch",
                "stored bytes recompute to a different identity",
            )
        return envelope

    def resolve_evidence_uri(self, uri: str) -> ImmutableEnvelope:
        """Resolve an immutable evidence URI and re-verify its hash."""

        if not isinstance(uri, str) or not uri.startswith(EVIDENCE_URI_SCHEME):
            raise GovernanceError(
                "invalid_evidence_uri",
                "Evidence_URI must be an immutable evidence://sha256/<digest> reference",
            )
        digest = uri[len(EVIDENCE_URI_SCHEME):]
        matches = list(self._root.glob(f"*/{digest}.json")) if self._root.exists() else []
        if len(matches) != 1:
            raise GovernanceError(
                "evidence_uri_unresolvable",
                f"Evidence_URI {uri} resolves to {len(matches)} stored objects",
            )
        try:
            document = json.loads(matches[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GovernanceError(
                "corrupt_object",
                f"evidence object {uri} is not valid JSON",
            ) from exc
        envelope = ImmutableEnvelope.from_document(document)
        if envelope.object_id.rsplit(":", 1)[-1] != digest:
            raise GovernanceError(
                "evidence_hash_mismatch",
                "stored evidence bytes recompute to a different digest",
            )
        return envelope

    def evidence_uri_for(self, object_id: str) -> str:
        return EVIDENCE_URI_SCHEME + object_id.rsplit(":", 1)[-1]

    def iter_kind(self, kind: str) -> Iterator[ImmutableEnvelope]:
        directory = self._root / kind
        if not directory.exists():
            return
        for path in sorted(directory.glob("*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise GovernanceError(
                    "corrupt_object",
                    f"governance object at {path} is not valid JSON",
                ) from exc
            yield ImmutableEnvelope.from_document(document)

    def supersede_chain(self, object_id: str) -> list[ImmutableEnvelope]:
        """Walk ``supersedes_id`` backwards from the given object."""

        chain: list[ImmutableEnvelope] = []
        seen: set[str] = set()
        current: ImmutableEnvelope | None = self.get(object_id)
        while current is not None:
            if current.object_id in seen:
                raise GovernanceError(
                    "supersede_cycle",
                    "supersedes chain contains a cycle",
                )
            seen.add(current.object_id)
            chain.append(current)
            current = self.get(current.supersedes_id) if current.supersedes_id else None
        return chain

    def _path_for(self, object_id: str) -> Path:
        kind, _, digest = object_id.rpartition(":sha256:")
        if not kind or not digest:
            raise GovernanceError(
                "invalid_object_id",
                f"object id {object_id!r} does not match '<kind>:sha256:<digest>'",
            )
        return self._root / kind / f"{digest}.json"


__all__ = [
    "DEFAULT_STORE_ROOT",
    "EVIDENCE_URI_SCHEME",
    "GovernanceObjectStore",
    "default_store_root",
]

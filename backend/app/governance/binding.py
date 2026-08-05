"""Immutable revision bindings for revision-bound code completion.

A revision binding pins the exact dependency digests under which a task's
CODE_COMPLETE claim is valid.  Task 1.3 binds six digest kinds: REPLAY,
EVIDENCE_IDENTITY, HISTORICAL_QUERY, LIVE_SELECTOR, MIGRATION_CONTRACT, and
ASSOCIATED_TESTS.  Any digest drift makes the binding STALE: the historical
completion fact is preserved in the event log, but the current Code_State
falls back to IN_PROGRESS until a fresh binding passes Layer 1 and every
applicable correctness obligation.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from app.engine.canonical import canonical_fingerprint
from app.governance.envelope import GovernanceError


class DependencyDigestKind(StrEnum):
    REPLAY = "REPLAY"
    EVIDENCE_IDENTITY = "EVIDENCE_IDENTITY"
    HISTORICAL_QUERY = "HISTORICAL_QUERY"
    LIVE_SELECTOR = "LIVE_SELECTOR"
    MIGRATION_CONTRACT = "MIGRATION_CONTRACT"
    ASSOCIATED_TESTS = "ASSOCIATED_TESTS"


class RevisionBindingStatus(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_LOCATOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]*$")


@dataclass(frozen=True, slots=True)
class DependencyDigest:
    """One pinned dependency: a file, symbol set, or contract by content hash."""

    kind: DependencyDigestKind
    locator: str
    content_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DependencyDigestKind):
            raise GovernanceError(
                "invalid_digest_kind",
                "kind must be a DependencyDigestKind",
            )
        if not isinstance(self.locator, str) or not _SAFE_LOCATOR_PATTERN.fullmatch(self.locator):
            raise GovernanceError(
                "invalid_digest_locator",
                "locator must be a safe canonical identifier",
            )
        if not _SHA256_PATTERN.fullmatch(self.content_sha256 or ""):
            raise GovernanceError(
                "invalid_digest_hash",
                "content_sha256 must be a lowercase SHA-256 digest",
            )

    def canonical_payload(self) -> dict[str, str]:
        return {
            "content_sha256": self.content_sha256,
            "kind": self.kind.value,
            "locator": self.locator,
        }


def digest_file(path: str | Path) -> str:
    """Hash one dependency file's bytes for binding membership."""

    file_path = Path(path)
    if not file_path.is_file():
        raise GovernanceError(
            "dependency_missing",
            f"dependency locator {file_path} is not a readable file",
        )
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class RevisionBinding:
    """Sorted, content-hashed dependency set for one binding scope."""

    scope_id: str
    digests: tuple[DependencyDigest, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.scope_id, str) or not _SAFE_LOCATOR_PATTERN.fullmatch(self.scope_id):
            raise GovernanceError(
                "invalid_binding_scope",
                "scope_id must be a safe canonical identifier",
            )
        ordered = tuple(sorted(self.digests, key=lambda item: (item.kind.value, item.locator)))
        object.__setattr__(self, "digests", ordered)
        locators = [(item.kind, item.locator) for item in ordered]
        if len(set(locators)) != len(locators):
            raise GovernanceError(
                "duplicate_digest_locator",
                "each (kind, locator) pair may appear exactly once",
            )

    def kinds(self) -> frozenset[DependencyDigestKind]:
        return frozenset(item.kind for item in self.digests)

    def require_kinds(self, required: Iterable[DependencyDigestKind]) -> None:
        missing = set(required) - set(self.kinds())
        if missing:
            raise GovernanceError(
                "binding_scope_incomplete",
                f"binding {self.scope_id} misses digest kinds: {sorted(k.value for k in missing)}",
            )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "digests": [item.canonical_payload() for item in self.digests],
            "scope_id": self.scope_id,
        }

    @property
    def binding_hash(self) -> str:
        return canonical_fingerprint(self.canonical_payload())

    def verify(self, current: "RevisionBinding") -> RevisionBindingStatus:
        """Compare against a freshly computed binding for the same scope."""

        if current.scope_id != self.scope_id:
            raise GovernanceError(
                "binding_scope_mismatch",
                "revision bindings belong to different scopes",
            )
        if current.binding_hash == self.binding_hash:
            return RevisionBindingStatus.CURRENT
        return RevisionBindingStatus.STALE

    def stale_members(self, current: "RevisionBinding") -> tuple[DependencyDigest, ...]:
        """List the digest entries that changed relative to ``current``."""

        fresh = {(item.kind, item.locator): item for item in current.digests}
        changed = [
            item
            for item in self.digests
            if fresh.get((item.kind, item.locator)) != item
        ]
        return tuple(changed)


TASK_1_3_BINDING_SCOPE = "task_1_3_historical_reconstruction"
TASK_1_3_REQUIRED_KINDS: tuple[DependencyDigestKind, ...] = (
    DependencyDigestKind.REPLAY,
    DependencyDigestKind.EVIDENCE_IDENTITY,
    DependencyDigestKind.HISTORICAL_QUERY,
    DependencyDigestKind.LIVE_SELECTOR,
    DependencyDigestKind.MIGRATION_CONTRACT,
    DependencyDigestKind.ASSOCIATED_TESTS,
)




TASK_1_3_LAYER2_EVIDENCE_KINDS: tuple[str, ...] = (
    "migration_apply_no_op",
    "real_prefect_replay",
    "persisted_historical_query",
    "persisted_live_exclusion",
)


@dataclass(frozen=True, slots=True)
class Task13Layer2Evidence:
    evidence_kind: str
    result: str
    revision_binding_hash: str
    environment_identity: str


@dataclass(frozen=True, slots=True)
class Task13CompletionDecision:
    code_state: str
    environment_state: str
    task_complete: bool
    reason_codes: tuple[str, ...]


def evaluate_task_1_3_completion(
    *,
    bound_revision: RevisionBinding,
    current_revision: RevisionBinding,
    layer1_passed: bool,
    correctness_proofs_passed: bool,
    layer2_evidence: Iterable[Task13Layer2Evidence] = (),
) -> Task13CompletionDecision:
    """Evaluate Task 1.3 without allowing Layer 1 to imply environment acceptance."""
    bound_revision.require_kinds(TASK_1_3_REQUIRED_KINDS)
    current_revision.require_kinds(TASK_1_3_REQUIRED_KINDS)
    if bound_revision.verify(current_revision) is RevisionBindingStatus.STALE:
        return Task13CompletionDecision(
            code_state="IN_PROGRESS",
            environment_state="BLOCKED",
            task_complete=False,
            reason_codes=("REVISION_BINDING_STALE",),
        )
    reasons: list[str] = []
    if not layer1_passed:
        reasons.append("LAYER_1_NOT_PASSING")
    if not correctness_proofs_passed:
        reasons.append("CORRECTNESS_PROOF_ROUTE_MISSING")
    code_state = "CODE_COMPLETE" if not reasons else "IN_PROGRESS"

    evidence_by_kind: dict[str, Task13Layer2Evidence] = {}
    environments: set[str] = set()
    for evidence in layer2_evidence:
        if evidence.evidence_kind in evidence_by_kind:
            reasons.append(f"DUPLICATE_LAYER_2_EVIDENCE:{evidence.evidence_kind}")
            continue
        evidence_by_kind[evidence.evidence_kind] = evidence
        if evidence.result != "PASS":
            reasons.append(f"LAYER_2_NOT_PASSING:{evidence.evidence_kind}")
        if evidence.revision_binding_hash != current_revision.binding_hash:
            reasons.append(f"LAYER_2_REVISION_MISMATCH:{evidence.evidence_kind}")
        if evidence.environment_identity:
            environments.add(evidence.environment_identity)
        else:
            reasons.append(f"LAYER_2_ENVIRONMENT_MISSING:{evidence.evidence_kind}")
    for kind in TASK_1_3_LAYER2_EVIDENCE_KINDS:
        if kind not in evidence_by_kind:
            reasons.append(f"LAYER_2_EVIDENCE_MISSING:{kind}")
    if len(environments) > 1:
        reasons.append("LAYER_2_ENVIRONMENT_MISMATCH")

    layer2_ok = (
        code_state == "CODE_COMPLETE"
        and not any(reason.startswith("LAYER_2_") or reason.startswith("DUPLICATE_") for reason in reasons)
        and set(evidence_by_kind) == set(TASK_1_3_LAYER2_EVIDENCE_KINDS)
    )
    return Task13CompletionDecision(
        code_state=code_state,
        environment_state="ACCEPTED" if layer2_ok else "BLOCKED",
        task_complete=layer2_ok,
        reason_codes=tuple(sorted(set(reasons))),
    )
__all__ = [
    "DependencyDigest",
    "DependencyDigestKind",
    "RevisionBinding",
    "RevisionBindingStatus",
    "TASK_1_3_BINDING_SCOPE",
    "TASK_1_3_LAYER2_EVIDENCE_KINDS",
    "TASK_1_3_REQUIRED_KINDS",
    "Task13CompletionDecision",
    "Task13Layer2Evidence",
    "digest_file",
    "evaluate_task_1_3_completion",
]

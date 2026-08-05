"""ReleaseScopeManifest: frozen inclusion decisions for gated operations.

The manifest registers every Approval-Gated Operation exactly once with its
inclusion, completion impact, target environment identity, plan identity,
evidence bindings, and dependencies.  It is not a write authorization: every
INCLUDED operation still needs its own Separate_Write_Approval at execution
time, and every EXCLUDED operation records the terminal
NOT_AUTHORIZED_NOT_EXECUTED without entering the completion work set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.governance.envelope import GovernanceError
from app.governance.registries import (
    MIGRATION_APPLY_OPERATION_ID,
    operation_ids,
)


class ReleaseInclusion(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"


class CompletionImpact(StrEnum):
    COMPLETION_CRITICAL = "COMPLETION_CRITICAL"
    NON_CRITICAL = "NON_CRITICAL"
    ACTIVATION_ONLY = "ACTIVATION_ONLY"


class EvidenceBindingDirection(StrEnum):
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    direction: EvidenceBindingDirection
    evidence_set_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.direction, EvidenceBindingDirection):
            raise GovernanceError(
                "invalid_evidence_binding",
                "direction must be PRODUCES or CONSUMES",
            )
        if not isinstance(self.evidence_set_key, str) or not self.evidence_set_key.strip():
            raise GovernanceError(
                "invalid_evidence_binding",
                "evidence_set_key must be a non-empty stable key",
            )

    def canonical_payload(self) -> dict[str, str]:
        return {
            "direction": self.direction.value,
            "evidence_set_key": self.evidence_set_key,
        }


@dataclass(frozen=True, slots=True)
class ReleaseScopeEntry:
    operation_id: str
    inclusion: ReleaseInclusion
    completion_impact: CompletionImpact
    target_environment_key: str
    operation_plan_key: str
    evidence_bindings: tuple[EvidenceBinding, ...] = field(default_factory=tuple)
    dependency_operation_ids: tuple[str, ...] = field(default_factory=tuple)
    declaration_reason_code: str = ""

    def __post_init__(self) -> None:
        if self.operation_id not in operation_ids():
            raise GovernanceError(
                "unknown_operation_id",
                f"scope entry references unknown operation {self.operation_id!r}",
            )
        if not isinstance(self.inclusion, ReleaseInclusion):
            raise GovernanceError("invalid_inclusion", "inclusion must be INCLUDED or EXCLUDED")
        if not isinstance(self.completion_impact, CompletionImpact):
            raise GovernanceError(
                "invalid_completion_impact",
                "completion_impact must be a CompletionImpact",
            )
        if self.inclusion is ReleaseInclusion.EXCLUDED:
            if self.completion_impact is CompletionImpact.COMPLETION_CRITICAL:
                raise GovernanceError(
                    "excluded_completion_critical",
                    "EXCLUDED operations cannot be COMPLETION_CRITICAL",
                )
        if not self.target_environment_key.strip():
            raise GovernanceError(
                "invalid_scope_entry",
                "target_environment_key must be a non-empty stable identity",
            )
        if not self.operation_plan_key.strip():
            raise GovernanceError(
                "invalid_scope_entry",
                "operation_plan_key must be a non-empty stable identity",
            )
        for dependency in self.dependency_operation_ids:
            if dependency not in operation_ids():
                raise GovernanceError(
                    "unknown_operation_id",
                    f"scope entry dependency {dependency!r} is not a registered operation",
                )
        if self.operation_id == MIGRATION_APPLY_OPERATION_ID:
            if (
                self.inclusion is not ReleaseInclusion.INCLUDED
                or self.completion_impact is not CompletionImpact.COMPLETION_CRITICAL
            ):
                raise GovernanceError(
                    "migration_scope_forced",
                    "migration_apply.task_1_3_schema_v1 must be INCLUDED+COMPLETION_CRITICAL",
                )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "completion_impact": self.completion_impact.value,
            "declaration_reason_code": self.declaration_reason_code,
            "dependency_operation_ids": sorted(self.dependency_operation_ids),
            "evidence_bindings": [item.canonical_payload() for item in self.evidence_bindings],
            "operation_id": self.operation_id,
            "operation_plan_key": self.operation_plan_key,
            "release_inclusion": self.inclusion.value,
            "target_environment_key": self.target_environment_key,
        }


@dataclass(frozen=True, slots=True)
class ReleaseScopeManifest:
    """The complete, validated scope payload ready for envelope freeze."""

    registry_snapshot_id: str
    entries: tuple[ReleaseScopeEntry, ...]
    behavior_change_barrier_event_id: str

    def __post_init__(self) -> None:
        if not self.registry_snapshot_id.strip():
            raise GovernanceError(
                "invalid_scope_manifest",
                "registry_snapshot_id must reference the frozen registry snapshot",
            )
        if not self.behavior_change_barrier_event_id.strip():
            raise GovernanceError(
                "invalid_scope_manifest",
                "behavior_change_barrier_event_id must reference the barrier event",
            )
        ordered = tuple(sorted(self.entries, key=lambda item: item.operation_id))
        object.__setattr__(self, "entries", ordered)
        seen: set[str] = set()
        for entry in ordered:
            if entry.operation_id in seen:
                raise GovernanceError(
                    "duplicate_scope_entry",
                    f"operation {entry.operation_id!r} appears more than once",
                )
            seen.add(entry.operation_id)
        missing = operation_ids() - seen
        if missing:
            raise GovernanceError(
                "scope_manifest_incomplete",
                f"scope manifest misses operations: {sorted(missing)}",
            )
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        dependencies = {
            entry.operation_id: set(entry.dependency_operation_ids) for entry in self.entries
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise GovernanceError(
                    "scope_dependency_cycle",
                    f"dependency cycle reaches {node!r}",
                )
            visiting.add(node)
            for dependency in dependencies.get(node, ()):
                visit(dependency)
            visiting.discard(node)
            visited.add(node)

        for operation_id in dependencies:
            visit(operation_id)

    def entry_for(self, operation_id: str) -> ReleaseScopeEntry:
        for entry in self.entries:
            if entry.operation_id == operation_id:
                return entry
        raise GovernanceError(
            "unknown_operation_id",
            f"operation {operation_id!r} is not covered by this scope manifest",
        )

    def included_operations(self) -> tuple[str, ...]:
        return tuple(
            entry.operation_id
            for entry in self.entries
            if entry.inclusion is ReleaseInclusion.INCLUDED
        )

    def excluded_operations(self) -> tuple[str, ...]:
        return tuple(
            entry.operation_id
            for entry in self.entries
            if entry.inclusion is ReleaseInclusion.EXCLUDED
        )

    def completion_critical_operations(self) -> tuple[str, ...]:
        """The gated subset of the Completion_Required_Work_Set."""

        return tuple(
            entry.operation_id
            for entry in self.entries
            if entry.inclusion is ReleaseInclusion.INCLUDED
            and entry.completion_impact is CompletionImpact.COMPLETION_CRITICAL
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "behavior_change_barrier_event_id": self.behavior_change_barrier_event_id,
            "entries": [entry.canonical_payload() for entry in self.entries],
            "registry_snapshot_id": self.registry_snapshot_id,
        }


__all__ = [
    "CompletionImpact",
    "EvidenceBinding",
    "EvidenceBindingDirection",
    "ReleaseInclusion",
    "ReleaseScopeEntry",
    "ReleaseScopeManifest",
]

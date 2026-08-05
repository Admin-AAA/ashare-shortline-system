"""Pure Layer-1 contracts for rollback, completion, and final reporting.

This module deliberately has no executor, persistence, ORM, or environment
integration.  A read-path rollback can change only the selector held by
``ReadPathState``; append-only facts remain immutable snapshots.  Spec
completion and production readiness are separate exact conjunctions.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping, Sequence

from app.engine.canonical import canonical_json
from app.governance.envelope import GovernanceError
from app.governance.registries import FEATURE_FLAG_REGISTRY, FeatureFlagId, operation_ids
from app.governance.scope import ReleaseScopeManifest
from app.governance.scope_guard import DeferredInitiative
from app.governance.state import (
    EnvironmentAcceptanceState,
    OperationExecutionState,
    ProductionActivationState,
    SpecCompletionState,
)


class FeatureFlagRuntimeMode(StrEnum):
    """The four normative runtime modes; only ENFORCE is production-ready."""

    OFF = "OFF"
    SHADOW = "SHADOW"
    AUDIT = "AUDIT"
    ENFORCE = "ENFORCE"


class CleanForwardSampleStatus(StrEnum):
    UNVERIFIED_CURRENT_POLICY = "UNVERIFIED_CURRENT_POLICY"
    OBSERVED_NO_POSITIVE_EXPECTATION_CLAIM = "OBSERVED_NO_POSITIVE_EXPECTATION_CLAIM"


class PositiveExpectationClaim(StrEnum):
    """Final reports intentionally have no positive-claim state."""

    NOT_CLAIMED = "NOT_CLAIMED"


DEFERRED_DISPOSITION = "DEFERRED_OUT_OF_SCOPE_REQUIRES_INDEPENDENT_GOVERNANCE"


def _non_empty(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GovernanceError("invalid_completion_contract", f"{field_name} must be non-empty")
    return value


def _state_value(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


@dataclass(frozen=True, slots=True)
class AppendOnlyDataSnapshot:
    """Stable identities and fingerprints of append-only facts.

    Existing identities must remain present with the same fingerprint.  A later
    snapshot may contain additional identities; removal or mutation is never a
    valid rollback.
    """

    collections: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]

    def __post_init__(self) -> None:
        normalized: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        seen_collections: set[str] = set()
        for collection, facts in self.collections:
            collection = _non_empty(collection, field_name="collection")
            if collection in seen_collections:
                raise GovernanceError(
                    "duplicate_append_only_collection",
                    f"append-only collection {collection!r} is duplicated",
                )
            seen_collections.add(collection)
            normalized_facts = tuple(sorted(facts))
            fact_ids = [fact_id for fact_id, _ in normalized_facts]
            if len(fact_ids) != len(set(fact_ids)):
                raise GovernanceError(
                    "duplicate_append_only_fact",
                    f"collection {collection!r} contains duplicate fact identities",
                )
            for fact_id, fingerprint in normalized_facts:
                _non_empty(fact_id, field_name="fact_id")
                _non_empty(fingerprint, field_name="fact_fingerprint")
            normalized.append((collection, normalized_facts))
        object.__setattr__(self, "collections", tuple(sorted(normalized)))

    @classmethod
    def capture(
        cls,
        collections: Mapping[str, Mapping[str, str]],
    ) -> AppendOnlyDataSnapshot:
        return cls(
            tuple(
                (collection, tuple(facts.items()))
                for collection, facts in collections.items()
            )
        )

    def as_mapping(self) -> dict[str, dict[str, str]]:
        return {
            collection: dict(facts)
            for collection, facts in self.collections
        }

    @property
    def fact_count(self) -> int:
        return sum(len(facts) for _, facts in self.collections)


@dataclass(frozen=True, slots=True)
class HistoryCollectionConservation:
    collection: str
    before_n: int
    after_n: int
    preserved_n: int
    missing_fact_ids: tuple[str, ...] = ()
    mutated_fact_ids: tuple[str, ...] = ()

    @property
    def preserved(self) -> bool:
        return (
            self.after_n >= self.before_n
            and self.preserved_n == self.before_n
            and not self.missing_fact_ids
            and not self.mutated_fact_ids
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "after_n": self.after_n,
            "before_n": self.before_n,
            "collection": self.collection,
            "missing_fact_ids": list(self.missing_fact_ids),
            "mutated_fact_ids": list(self.mutated_fact_ids),
            "preserved": self.preserved,
            "preserved_n": self.preserved_n,
        }


@dataclass(frozen=True, slots=True)
class HistoryConservation:
    collections: tuple[HistoryCollectionConservation, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.collections, key=lambda item: item.collection))
        if len({item.collection for item in ordered}) != len(ordered):
            raise GovernanceError(
                "duplicate_history_collection",
                "history conservation contains duplicate collections",
            )
        object.__setattr__(self, "collections", ordered)

    @classmethod
    def compare(
        cls,
        before: AppendOnlyDataSnapshot,
        after: AppendOnlyDataSnapshot,
    ) -> HistoryConservation:
        before_map = before.as_mapping()
        after_map = after.as_mapping()
        results: list[HistoryCollectionConservation] = []
        for collection in sorted(set(before_map) | set(after_map)):
            old = before_map.get(collection, {})
            new = after_map.get(collection, {})
            missing = tuple(sorted(set(old) - set(new)))
            mutated = tuple(
                sorted(fact_id for fact_id in set(old) & set(new) if old[fact_id] != new[fact_id])
            )
            preserved_n = sum(
                1 for fact_id, fingerprint in old.items() if new.get(fact_id) == fingerprint
            )
            results.append(
                HistoryCollectionConservation(
                    collection=collection,
                    before_n=len(old),
                    after_n=len(new),
                    preserved_n=preserved_n,
                    missing_fact_ids=missing,
                    mutated_fact_ids=mutated,
                )
            )
        return cls(tuple(results))

    @property
    def preserved(self) -> bool:
        return all(item.preserved for item in self.collections)

    @property
    def before_n(self) -> int:
        return sum(item.before_n for item in self.collections)

    @property
    def after_n(self) -> int:
        return sum(item.after_n for item in self.collections)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "after_n": self.after_n,
            "before_n": self.before_n,
            "collections": [item.canonical_payload() for item in self.collections],
            "preserved": self.preserved,
        }


def assert_append_only_preserved(
    before: AppendOnlyDataSnapshot,
    after: AppendOnlyDataSnapshot,
) -> HistoryConservation:
    conservation = HistoryConservation.compare(before, after)
    if not conservation.preserved:
        raise GovernanceError(
            "append_only_history_not_preserved",
            "read-path rollback may not remove or mutate append-only facts",
        )
    return conservation


@dataclass(frozen=True, slots=True)
class ReadPathState:
    selector: str
    append_only_data: AppendOnlyDataSnapshot

    def __post_init__(self) -> None:
        _non_empty(self.selector, field_name="selector")
        if not isinstance(self.append_only_data, AppendOnlyDataSnapshot):
            raise GovernanceError(
                "invalid_read_path_state",
                "append_only_data must be an AppendOnlyDataSnapshot",
            )


@dataclass(frozen=True, slots=True)
class ReadPathRollback:
    """A capability restricted to selector restoration only.

    There is intentionally no table/column/data downgrade field or callback in
    this contract.  ``apply`` carries the exact same append-only snapshot
    object into the result and verifies history conservation.
    """

    restore_selector: str

    def __post_init__(self) -> None:
        _non_empty(self.restore_selector, field_name="restore_selector")

    def apply(self, current: ReadPathState) -> ReadPathState:
        rolled_back = replace(current, selector=self.restore_selector)
        assert_append_only_preserved(current.append_only_data, rolled_back.append_only_data)
        return rolled_back


def rollback_read_path(current: ReadPathState, *, restore_selector: str) -> ReadPathState:
    return ReadPathRollback(restore_selector=restore_selector).apply(current)


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    state: SpecCompletionState
    required_work_set: frozenset[str]
    blockers: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return self.state is SpecCompletionState.COMPLETE

    def canonical_payload(self) -> dict[str, object]:
        return {
            "blockers": list(self.blockers),
            "required_work_set": sorted(self.required_work_set),
            "spec_completion_state": self.state.value,
        }


def build_completion_required_work_set(
    non_gated_required_work: Sequence[str] | set[str] | frozenset[str],
    manifest: ReleaseScopeManifest,
) -> frozenset[str]:
    """Return exactly non-gated work plus INCLUDED+COMPLETION_CRITICAL work."""

    non_gated = tuple(non_gated_required_work)
    for subject_id in non_gated:
        _non_empty(subject_id, field_name="non_gated_required_work[]")
    if len(non_gated) != len(set(non_gated)):
        raise GovernanceError(
            "duplicate_required_work",
            "non-gated required work identities must be unique",
        )
    mislabeled = set(non_gated) & operation_ids()
    if mislabeled:
        raise GovernanceError(
            "gated_work_mislabeled_non_gated",
            f"approval-gated operations cannot be supplied as non-gated work: {sorted(mislabeled)}",
        )
    return frozenset(non_gated) | frozenset(manifest.completion_critical_operations())


def _environment_state(value: object) -> object:
    return getattr(value, "environment_state", value)


def evaluate_spec_completion(
    non_gated_statuses: Mapping[str, EnvironmentAcceptanceState | object],
    operation_states: Mapping[str, OperationExecutionState],
    manifest: ReleaseScopeManifest,
) -> CompletionDecision:
    """Exact acceptance conjunction; excluded/activation-only states are ignored."""

    required = build_completion_required_work_set(tuple(non_gated_statuses), manifest)
    blockers: list[str] = []
    non_gated_ids = set(non_gated_statuses)
    for subject_id in sorted(required):
        if subject_id in non_gated_ids:
            state = _environment_state(non_gated_statuses[subject_id])
            accepted = state is EnvironmentAcceptanceState.ACCEPTED or state == "ACCEPTED"
        else:
            state = operation_states.get(subject_id)
            accepted = state is OperationExecutionState.ACCEPTED or state == "ACCEPTED"
        if not accepted:
            blockers.append(f"NOT_ACCEPTED:{subject_id}:{_state_value(state) if state is not None else 'MISSING'}")
    return CompletionDecision(
        state=SpecCompletionState.COMPLETE if not blockers else SpecCompletionState.BLOCKED,
        required_work_set=required,
        blockers=tuple(blockers),
    )


@dataclass(frozen=True, slots=True)
class ProductionReadinessDecision:
    ready: bool
    blockers: tuple[str, ...]

    def canonical_payload(self) -> dict[str, object]:
        return {"blockers": list(self.blockers), "production_ready": self.ready}


def _flag_key(value: object) -> str:
    return value.value if isinstance(value, FeatureFlagId) else str(value)


def _normalized_flag_mapping(values: Mapping[FeatureFlagId | str, object]) -> tuple[dict[str, object], tuple[str, ...]]:
    normalized: dict[str, object] = {}
    duplicates: list[str] = []
    for raw_key, value in values.items():
        key = _flag_key(raw_key)
        if key in normalized:
            duplicates.append(key)
        normalized[key] = value
    return normalized, tuple(sorted(set(duplicates)))


def evaluate_production_readiness(
    flag_runtime_modes: Mapping[FeatureFlagId | str, FeatureFlagRuntimeMode | str],
    activation_states: Mapping[FeatureFlagId | str, ProductionActivationState | str],
) -> ProductionReadinessDecision:
    """Require the exact six registry keys, each ENFORCE and ACTIVE."""

    required = {binding.flag_id.value for binding in FEATURE_FLAG_REGISTRY}
    modes, mode_duplicates = _normalized_flag_mapping(flag_runtime_modes)
    activations, activation_duplicates = _normalized_flag_mapping(activation_states)
    blockers: list[str] = []
    blockers.extend(f"DUPLICATE_RUNTIME_MODE:{item}" for item in mode_duplicates)
    blockers.extend(f"DUPLICATE_ACTIVATION_STATE:{item}" for item in activation_duplicates)
    blockers.extend(f"UNREGISTERED_RUNTIME_MODE:{item}" for item in sorted(set(modes) - required))
    blockers.extend(f"UNREGISTERED_ACTIVATION_STATE:{item}" for item in sorted(set(activations) - required))
    for flag_id in sorted(required):
        mode = modes.get(flag_id)
        activation = activations.get(flag_id)
        if mode is None:
            blockers.append(f"MISSING_RUNTIME_MODE:{flag_id}")
        elif not (mode is FeatureFlagRuntimeMode.ENFORCE or mode == "ENFORCE"):
            blockers.append(f"RUNTIME_MODE_NOT_ENFORCE:{flag_id}:{_state_value(mode)}")
        if activation is None:
            blockers.append(f"MISSING_ACTIVATION_STATE:{flag_id}")
        elif not (activation is ProductionActivationState.ACTIVE or activation == "ACTIVE"):
            blockers.append(f"ACTIVATION_NOT_ACTIVE:{flag_id}:{_state_value(activation)}")
    return ProductionReadinessDecision(ready=not blockers, blockers=tuple(sorted(blockers)))


def production_ready(
    flag_runtime_modes: Mapping[FeatureFlagId | str, FeatureFlagRuntimeMode | str],
    activation_states: Mapping[FeatureFlagId | str, ProductionActivationState | str],
) -> bool:
    return evaluate_production_readiness(flag_runtime_modes, activation_states).ready


@dataclass(frozen=True, slots=True)
class FinalEvidenceReport:
    """Deterministic final schema with no representable positive claim."""

    payload: Mapping[str, object]

    def canonical_payload(self) -> dict[str, object]:
        return dict(self.payload)

    def to_canonical_json(self) -> str:
        return canonical_json(self.payload)


def build_final_evidence_report(
    *,
    scope_manifest: ReleaseScopeManifest,
    scope_manifest_identity: str,
    clean_forward_n: int,
    known_unknowns: Sequence[str],
    operation_states: Mapping[str, OperationExecutionState | str],
    flag_runtime_modes: Mapping[FeatureFlagId | str, FeatureFlagRuntimeMode | str],
    activation_states: Mapping[FeatureFlagId | str, ProductionActivationState | str],
    history_conservation: HistoryConservation,
) -> FinalEvidenceReport:
    """Build the complete canonical Layer-1 report without executing anything."""

    identity = _non_empty(scope_manifest_identity, field_name="scope_manifest_identity")
    if isinstance(clean_forward_n, bool) or not isinstance(clean_forward_n, int) or clean_forward_n < 0:
        raise GovernanceError("invalid_clean_forward_n", "clean_forward_n must be a non-negative integer")
    if not isinstance(history_conservation, HistoryConservation):
        raise GovernanceError(
            "invalid_history_conservation",
            "history_conservation must be a HistoryConservation",
        )

    registered_operations = operation_ids()
    unknown_operation_ids = sorted(set(operation_states) - registered_operations)
    if unknown_operation_ids:
        raise GovernanceError(
            "unknown_operation_state",
            f"operation states contain unregistered IDs: {unknown_operation_ids}",
        )

    excluded_terminal: list[dict[str, str]] = []
    for operation_id in scope_manifest.excluded_operations():
        state = operation_states.get(operation_id)
        if not (
            state is OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED
            or state == "NOT_AUTHORIZED_NOT_EXECUTED"
        ):
            raise GovernanceError(
                "excluded_operation_not_terminal",
                f"excluded operation {operation_id!r} must be NOT_AUTHORIZED_NOT_EXECUTED",
            )
        excluded_terminal.append(
            {
                "operation_id": operation_id,
                "operation_execution_state": OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED.value,
            }
        )

    modes, _ = _normalized_flag_mapping(flag_runtime_modes)
    activations, _ = _normalized_flag_mapping(activation_states)
    readiness = evaluate_production_readiness(flag_runtime_modes, activation_states)
    flag_rows: list[dict[str, str]] = []
    unactivated: list[dict[str, str]] = []
    automatic_unknowns: set[str] = set()
    for binding in sorted(FEATURE_FLAG_REGISTRY, key=lambda item: item.flag_id.value):
        flag_id = binding.flag_id.value
        mode = modes.get(flag_id)
        activation = activations.get(flag_id)
        mode_value = _state_value(mode) if mode is not None else "UNKNOWN"
        activation_value = _state_value(activation) if activation is not None else "UNKNOWN"
        row = {
            "activation_state": activation_value,
            "flag_id": flag_id,
            "runtime_mode": mode_value,
        }
        flag_rows.append(row)
        if activation_value != ProductionActivationState.ACTIVE.value:
            unactivated.append(row.copy())
        if mode is None:
            automatic_unknowns.add(f"MISSING_FLAG_RUNTIME_MODE:{flag_id}")
        if activation is None:
            automatic_unknowns.add(f"MISSING_FLAG_ACTIVATION_STATE:{flag_id}")

    operation_rows: list[dict[str, str]] = []
    for operation_id in sorted(registered_operations):
        state = operation_states.get(operation_id)
        state_value = _state_value(state) if state is not None else "UNKNOWN"
        operation_rows.append(
            {"operation_execution_state": state_value, "operation_id": operation_id}
        )
        if state is None:
            automatic_unknowns.add(f"MISSING_OPERATION_STATE:{operation_id}")

    supplied_unknowns = {_non_empty(item, field_name="known_unknowns[]") for item in known_unknowns}
    if clean_forward_n == 0:
        clean_status = CleanForwardSampleStatus.UNVERIFIED_CURRENT_POLICY
        automatic_unknowns.add("CURRENT_POLICY_POSITIVE_EXPECTATION_UNVERIFIED")
    else:
        clean_status = CleanForwardSampleStatus.OBSERVED_NO_POSITIVE_EXPECTATION_CLAIM

    deferred = [
        {"disposition": DEFERRED_DISPOSITION, "initiative": initiative.value}
        for initiative in DeferredInitiative
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "scope": {
            "registry_snapshot_id": scope_manifest.registry_snapshot_id,
            "release_scope_manifest_identity": identity,
        },
        "provenance_clean_true_forward": {
            "n": clean_forward_n,
            "positive_expectation_claim": PositiveExpectationClaim.NOT_CLAIMED.value,
            "status": clean_status.value,
        },
        "known_unknowns": sorted(supplied_unknowns | automatic_unknowns),
        "operations": operation_rows,
        "excluded_terminal_operations": excluded_terminal,
        "feature_flags": flag_rows,
        "unactivated_flags": unactivated,
        "production_readiness": readiness.canonical_payload(),
        "history_conservation": history_conservation.canonical_payload(),
        "deferred_initiatives": deferred,
    }
    return FinalEvidenceReport(payload=payload)


__all__ = [
    "AppendOnlyDataSnapshot",
    "CleanForwardSampleStatus",
    "CompletionDecision",
    "DEFERRED_DISPOSITION",
    "FeatureFlagRuntimeMode",
    "FinalEvidenceReport",
    "HistoryCollectionConservation",
    "HistoryConservation",
    "PositiveExpectationClaim",
    "ProductionReadinessDecision",
    "ReadPathRollback",
    "ReadPathState",
    "assert_append_only_preserved",
    "build_completion_required_work_set",
    "build_final_evidence_report",
    "evaluate_production_readiness",
    "evaluate_spec_completion",
    "production_ready",
    "rollback_read_path",
]

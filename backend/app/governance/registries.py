"""Central registries: approval-gated operations and feature flags.

The operation registry fixes the nine stable operation IDs; the feature flag
registry fixes the six flag bindings.  Both serialize into one
content-addressed registry snapshot envelope.  Adding a new gated operation
requires a new stable ID and a new snapshot revision — IDs are never reused.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.governance.envelope import GovernanceError


class OperationKind(StrEnum):
    MIGRATION_APPLY = "migration_apply"
    CLASSIFICATION_BACKFILL = "classification_backfill"
    PROJECTION_CUTOVER = "projection_cutover"
    ENFORCEMENT = "enforcement"


MIGRATION_APPLY_OPERATION_ID = "migration_apply.task_1_3_schema_v1"
CLASSIFICATION_BACKFILL_OPERATION_ID = "classification_backfill.provenance_v1"
PROJECTION_CUTOVER_OPERATION_ID = "projection_cutover.production_read_v1"

OPERATION_REGISTRY: tuple[tuple[str, OperationKind], ...] = (
    (MIGRATION_APPLY_OPERATION_ID, OperationKind.MIGRATION_APPLY),
    (CLASSIFICATION_BACKFILL_OPERATION_ID, OperationKind.CLASSIFICATION_BACKFILL),
    (PROJECTION_CUTOVER_OPERATION_ID, OperationKind.PROJECTION_CUTOVER),
    ("enforcement.evidence_projection", OperationKind.ENFORCEMENT),
    ("enforcement.canonical_execution", OperationKind.ENFORCEMENT),
    ("enforcement.release_gate", OperationKind.ENFORCEMENT),
    ("enforcement.portfolio_risk", OperationKind.ENFORCEMENT),
    ("enforcement.scheduler_claim", OperationKind.ENFORCEMENT),
    ("enforcement.dashboard_refresh", OperationKind.ENFORCEMENT),
)


class FeatureFlagId(StrEnum):
    EVIDENCE_PROJECTION = "evidence_projection"
    CANONICAL_EXECUTION = "canonical_execution"
    RELEASE_GATE = "release_gate"
    PORTFOLIO_RISK = "portfolio_risk"
    SCHEDULER_CLAIM = "scheduler_claim"
    DASHBOARD_REFRESH = "dashboard_refresh"


@dataclass(frozen=True, slots=True)
class FeatureFlagBinding:
    """One registry row: flag -> Layer 2 evidence set -> cutover dependency."""

    flag_id: FeatureFlagId
    layer_2_evidence_set_id: str
    cutover_dependency: str  # operation id or "NONE"
    evidence_contract: tuple[str, ...] = field(default_factory=tuple)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "cutover_dependency": self.cutover_dependency,
            "evidence_contract": list(self.evidence_contract),
            "flag_id": self.flag_id.value,
            "layer_2_evidence_set_id": self.layer_2_evidence_set_id,
        }


FEATURE_FLAG_REGISTRY: tuple[FeatureFlagBinding, ...] = (
    FeatureFlagBinding(
        flag_id=FeatureFlagId.EVIDENCE_PROJECTION,
        layer_2_evidence_set_id="L2_FLAG_EVIDENCE_PROJECTION_V1",
        cutover_dependency=PROJECTION_CUTOVER_OPERATION_ID,
        evidence_contract=(
            "migration_apply_no_op",
            "real_prefect_replay",
            "persisted_historical_query",
            "persisted_live_exclusion",
        ),
    ),
    FeatureFlagBinding(
        flag_id=FeatureFlagId.CANONICAL_EXECUTION,
        layer_2_evidence_set_id="L2_FLAG_CANONICAL_EXECUTION_V1",
        cutover_dependency="NONE",
        evidence_contract=("trade_parity", "t_plus_one", "append_only_versions"),
    ),
    FeatureFlagBinding(
        flag_id=FeatureFlagId.RELEASE_GATE,
        layer_2_evidence_set_id="L2_FLAG_RELEASE_GOVERNANCE_V1",
        cutover_dependency="NONE",
        evidence_contract=("single_use_claim", "deterministic_decision", "publish_rejection"),
    ),
    FeatureFlagBinding(
        flag_id=FeatureFlagId.PORTFOLIO_RISK,
        layer_2_evidence_set_id="L2_FLAG_PORTFOLIO_RISK_V1",
        cutover_dependency="NONE",
        evidence_contract=("atomic_reservation", "exposure_bounds", "halt_resume"),
    ),
    FeatureFlagBinding(
        flag_id=FeatureFlagId.SCHEDULER_CLAIM,
        layer_2_evidence_set_id="L2_FLAG_SCHEDULER_CLAIM_V1",
        cutover_dependency="NONE",
        evidence_contract=("owner_exclusivity", "at_most_once_claim", "bounded_misfire"),
    ),
    FeatureFlagBinding(
        flag_id=FeatureFlagId.DASHBOARD_REFRESH,
        layer_2_evidence_set_id="L2_FLAG_DASHBOARD_OBSERVABILITY_V1",
        cutover_dependency=PROJECTION_CUTOVER_OPERATION_ID,
        evidence_contract=("truthful_labels", "stale_truth", "correlation_chain"),
    ),
)

_FLAG_IDS = frozenset(item.flag_id for item in FEATURE_FLAG_REGISTRY)
_OPERATION_IDS = frozenset(operation_id for operation_id, _ in OPERATION_REGISTRY)


def operation_ids() -> frozenset[str]:
    return _OPERATION_IDS


def operation_kind(operation_id: str) -> OperationKind:
    for registered_id, kind in OPERATION_REGISTRY:
        if registered_id == operation_id:
            return kind
    raise GovernanceError(
        "unknown_operation_id",
        f"operation {operation_id!r} is not in the Approval-Gated Operation Registry",
    )


def feature_flag_binding(flag_id: FeatureFlagId | str) -> FeatureFlagBinding:
    normalized = flag_id if isinstance(flag_id, FeatureFlagId) else FeatureFlagId(flag_id)
    for binding in FEATURE_FLAG_REGISTRY:
        if binding.flag_id is normalized:
            return binding
    raise GovernanceError(
        "unknown_feature_flag",
        f"flag {normalized.value!r} is not in the Feature Flag Registry",
    )


def validate_operation_registry() -> None:
    ids = [operation_id for operation_id, _ in OPERATION_REGISTRY]
    if len(ids) != 9 or len(set(ids)) != 9:
        raise GovernanceError(
            "operation_registry_invalid",
            "operation registry must contain exactly nine unique IDs",
        )


def validate_feature_flag_registry() -> None:
    if len(FEATURE_FLAG_REGISTRY) != 6 or len(_FLAG_IDS) != 6:
        raise GovernanceError(
            "flag_registry_invalid",
            "feature flag registry must contain exactly six unique flags",
        )
    layer_2_ids = [item.layer_2_evidence_set_id for item in FEATURE_FLAG_REGISTRY]
    if len(set(layer_2_ids)) != 6:
        raise GovernanceError(
            "flag_registry_invalid",
            "Layer 2 evidence set IDs must be one-to-one with flags",
        )
    for binding in FEATURE_FLAG_REGISTRY:
        if binding.cutover_dependency != "NONE":
            operation_kind(binding.cutover_dependency)
        if not binding.layer_2_evidence_set_id.startswith("L2_FLAG_"):
            raise GovernanceError(
                "flag_registry_invalid",
                f"unexpected Layer 2 evidence set ID {binding.layer_2_evidence_set_id!r}",
            )


def registry_snapshot_payload() -> dict[str, object]:
    """Canonical payload for the content-addressed registry snapshot."""

    validate_operation_registry()
    validate_feature_flag_registry()
    return {
        "feature_flags": [item.canonical_payload() for item in FEATURE_FLAG_REGISTRY],
        "operations": [
            {"operation_id": operation_id, "operation_kind": kind.value}
            for operation_id, kind in OPERATION_REGISTRY
        ],
    }


__all__ = [
    "CLASSIFICATION_BACKFILL_OPERATION_ID",
    "FEATURE_FLAG_REGISTRY",
    "FeatureFlagBinding",
    "FeatureFlagId",
    "MIGRATION_APPLY_OPERATION_ID",
    "OPERATION_REGISTRY",
    "OperationKind",
    "PROJECTION_CUTOVER_OPERATION_ID",
    "feature_flag_binding",
    "operation_ids",
    "operation_kind",
    "registry_snapshot_payload",
    "validate_feature_flag_registry",
    "validate_operation_registry",
]

"""Fail-closed Layer 1 boundary for approval-gated operations.

The boundary performs governance admission only.  It has no Production_Write,
DB, Alembic, subprocess or secret-resolution port and therefore cannot execute
a gated operation.  A successful pre-execution decision stops at
``AUTHORIZED_NOT_EXECUTED``.  Post-execution and immediate-second-run facts are
validated later from immutable, identity-only evidence supplied by an external
Layer 2 adapter.

Ordering is intentional: frozen scope and registry admission happen before an
approval provider is consulted.  An ``EXCLUDED`` entry returns its terminal
``NOT_AUTHORIZED_NOT_EXECUTED`` result immediately, so no plan is interpreted,
no approval is requested and no executor/secret resolver exists to call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Mapping

from app.governance.approval import (
    ApprovalBinding,
    ApprovalValidation,
    ApprovalVerdict,
    SeparateWriteApproval,
    separate_write_approval_from_envelope,
    validate_separate_write_approval,
)
from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.events import GovernanceEventLog
from app.governance.freeze import REGISTRY_SNAPSHOT_KIND, RELEASE_SCOPE_KIND
from app.governance.manifests import RESTORE_CAPACITY_KIND, validate_manifest_payload
from app.governance.migration_gates import preflight_admission
from app.governance.registries import (
    FEATURE_FLAG_REGISTRY,
    FeatureFlagId,
    MIGRATION_APPLY_OPERATION_ID,
    feature_flag_binding,
    operation_kind,
    registry_snapshot_payload,
)
from app.governance.scope import (
    CompletionImpact,
    EvidenceBinding,
    EvidenceBindingDirection,
    ReleaseInclusion,
    ReleaseScopeEntry,
    ReleaseScopeManifest,
)
from app.governance.state import (
    OperationExecutionState,
    excluded_operation_terminal_state,
)
from app.governance.store import GovernanceObjectStore


SCOPE_FROZEN_EVENT = "RELEASE_SCOPE_FROZEN"
GATED_ADMISSION_EVENT = "GATED_OPERATION_ADMISSION"
MIGRATION_POSTCHECK_EVENT = "MIGRATION_POSTCHECK_VERIFIED"

RELEASE_SCOPE_INVALID = "RELEASE_SCOPE_INVALID"
RELEASE_SCOPE_STALE = "RELEASE_SCOPE_STALE"
FEATURE_FLAG_REGISTRY_MISMATCH = "FEATURE_FLAG_REGISTRY_MISMATCH"
APPROVAL_MISSING_OR_MISMATCH = "APPROVAL_MISSING_OR_MISMATCH"
MIGRATION_NOT_ADDITIVE = "MIGRATION_NOT_ADDITIVE"
MIGRATION_SAFETY_GATE_FAILED = "MIGRATION_SAFETY_GATE_FAILED"

_ADDITIVE_ALLOWLIST = frozenset({"create_table", "add_column", "create_index"})


class GatedAdmissionVerdict(StrEnum):
    EXCLUDED = "EXCLUDED"
    BLOCKED = "BLOCKED"
    AUTHORIZED_NOT_EXECUTED = "AUTHORIZED_NOT_EXECUTED"
    POSTCHECKS_VERIFIED = "POSTCHECKS_VERIFIED"


def _aware_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GovernanceError("invalid_timestamp", f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _nonempty(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GovernanceError("invalid_gate_identity", f"{field_name} must be non-empty")
    return value


@dataclass(frozen=True, slots=True)
class GatedOperationRequest:
    """Identity-only facts for one exact operation attempt."""

    release_scope_manifest_id: str
    operation_id: str
    operation_instance_id: str
    target_environment_key: str
    operation_plan_key: str
    plan_hash: str
    artifact_checksum: str
    actor: str
    requested_at: datetime
    feature_flag_id: str | None = None
    layer_2_evidence_set_id: str | None = None
    projection_cutover_dependency: str | None = None

    def __post_init__(self) -> None:
        operation_kind(self.operation_id)
        for field_name in (
            "release_scope_manifest_id",
            "operation_instance_id",
            "target_environment_key",
            "operation_plan_key",
            "plan_hash",
            "artifact_checksum",
            "actor",
        ):
            _nonempty(getattr(self, field_name), field_name=field_name)
        object.__setattr__(
            self, "requested_at", _aware_utc(self.requested_at, field_name="requested_at")
        )

    @property
    def approval_binding(self) -> ApprovalBinding:
        return ApprovalBinding(
            operation_id=self.operation_id,
            operation_instance_id=self.operation_instance_id,
            target_environment_key=self.target_environment_key,
            plan_hash=self.plan_hash,
            artifact_checksum=self.artifact_checksum,
            actor=self.actor,
            feature_flag_id=self.feature_flag_id,
        )


@dataclass(frozen=True, slots=True)
class MigrationPlanOperation:
    operation_id: str
    kind: str
    precondition_id: str
    postcondition_id: str
    no_op_verification_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "kind",
            "precondition_id",
            "postcondition_id",
            "no_op_verification_id",
        ):
            _nonempty(getattr(self, field_name), field_name=field_name)


@dataclass(frozen=True, slots=True)
class ReadOnlyMigrationPlan:
    """Reviewed plan projection; it contains contracts, never executable DDL."""

    plan_key: str
    plan_hash: str
    artifact_checksum: str
    created_at: datetime
    operations: tuple[MigrationPlanOperation, ...]
    read_only: bool = True

    def __post_init__(self) -> None:
        for field_name in ("plan_key", "plan_hash", "artifact_checksum"):
            _nonempty(getattr(self, field_name), field_name=field_name)
        object.__setattr__(self, "created_at", _aware_utc(self.created_at, field_name="created_at"))
        if not self.operations:
            raise GovernanceError("migration_plan_empty", "migration plan must contain operations")
        ids = [item.operation_id for item in self.operations]
        if len(ids) != len(set(ids)):
            raise GovernanceError("migration_plan_duplicate_operation", "plan operation IDs must be unique")

    @classmethod
    def from_manifest(cls, payload: Mapping[str, Any], *, created_at: datetime) -> "ReadOnlyMigrationPlan":
        """Project the existing declarative migration manifest into this contract."""

        operations: list[MigrationPlanOperation] = []
        for batch in payload.get("batches") or ():
            if not isinstance(batch, Mapping):
                raise GovernanceError("migration_plan_invalid", "each batch must be an object")
            for operation in batch.get("operations") or ():
                if not isinstance(operation, Mapping):
                    raise GovernanceError("migration_plan_invalid", "each operation must be an object")
                operations.append(
                    MigrationPlanOperation(
                        operation_id=operation.get("operation_id"),
                        kind=operation.get("kind"),
                        precondition_id=operation.get("precondition"),
                        postcondition_id=operation.get("postcondition"),
                        no_op_verification_id=operation.get("no_op_verify"),
                    )
                )
        checksum = payload.get("manifest_checksum")
        return cls(
            plan_key=payload.get("plan_key") or "production-hardening-migration-plan",
            plan_hash=checksum,
            artifact_checksum=payload.get("artifact_checksum") or checksum,
            created_at=created_at,
            operations=tuple(operations),
            read_only=payload.get("execution_mode") == "plan_and_read_only_preflight",
        )


@dataclass(frozen=True, slots=True)
class GateAssertion:
    assertion_id: str
    passed: bool

    def __post_init__(self) -> None:
        _nonempty(self.assertion_id, field_name="assertion_id")
        if not isinstance(self.passed, bool):
            raise GovernanceError("invalid_gate_assertion", "passed must be boolean")


@dataclass(frozen=True, slots=True)
class MigrationPreExecutionGates:
    """Point-in-time facts required before authorization can be exposed."""

    plan: ReadOnlyMigrationPlan
    restore_capacity_manifest_id: str
    checked_at: datetime
    verified_restore_point: bool
    capacity_check_passed: bool
    preconditions: tuple[GateAssertion, ...]

    def __post_init__(self) -> None:
        _nonempty(self.restore_capacity_manifest_id, field_name="restore_capacity_manifest_id")
        object.__setattr__(self, "checked_at", _aware_utc(self.checked_at, field_name="checked_at"))


@dataclass(frozen=True, slots=True)
class MigrationRunSnapshot:
    """Hash-only state projection; comparing snapshots cannot expose row data."""

    schema_model_hash: str
    data_counts_hash: str
    migration_state_hash: str

    def __post_init__(self) -> None:
        for field_name in ("schema_model_hash", "data_counts_hash", "migration_state_hash"):
            _nonempty(getattr(self, field_name), field_name=field_name)


@dataclass(frozen=True, slots=True)
class MigrationPostExecutionGates:
    """Immutable evidence from the first run and immediate second run."""

    operation_instance_id: str
    plan_hash: str
    artifact_checksum: str
    checked_at: datetime
    postconditions: tuple[GateAssertion, ...]
    second_run_no_op_checks: tuple[GateAssertion, ...]
    first_run_snapshot: MigrationRunSnapshot
    second_run_snapshot: MigrationRunSnapshot

    def __post_init__(self) -> None:
        for field_name in ("operation_instance_id", "plan_hash", "artifact_checksum"):
            _nonempty(getattr(self, field_name), field_name=field_name)
        object.__setattr__(self, "checked_at", _aware_utc(self.checked_at, field_name="checked_at"))


@dataclass(frozen=True, slots=True)
class FrozenScopeAdmission:
    scope_envelope: ImmutableEnvelope
    scope: ReleaseScopeManifest
    entry: ReleaseScopeEntry


@dataclass(frozen=True, slots=True)
class GatedAdmissionResult:
    verdict: GatedAdmissionVerdict
    operation_state: OperationExecutionState
    reason_code: str
    reasons: tuple[str, ...] = field(default_factory=tuple)
    release_scope_manifest_id: str | None = None
    approval_object_id: str | None = None
    scope_admission: FrozenScopeAdmission | None = field(default=None, repr=False)
    request: GatedOperationRequest | None = field(default=None, repr=False)
    migration_plan: ReadOnlyMigrationPlan | None = field(default=None, repr=False)

    @property
    def production_write_admitted(self) -> bool:
        """Always false: Layer 1 never turns governance proof into execution."""

        return False

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "approval_object_id": self.approval_object_id,
            "operation_state": self.operation_state.value,
            "production_write_admitted": False,
            "reason_code": self.reason_code,
            "reasons": list(self.reasons),
            "release_scope_manifest_id": self.release_scope_manifest_id,
            "verdict": self.verdict.value,
        }


@dataclass(frozen=True, slots=True)
class EnforcementActivationDecision:
    """Pure activation projection; it never writes a runtime flag."""

    admitted: bool
    target_flag_id: str | None
    reason_codes: tuple[str, ...]
    runtime_modes: tuple[tuple[str, str], ...]
    activation_states: tuple[tuple[str, str], ...]

    def runtime_mode_for(self, flag_id: str) -> str | None:
        return dict(self.runtime_modes).get(flag_id)

    def activation_state_for(self, flag_id: str) -> str | None:
        return dict(self.activation_states).get(flag_id)


def evaluate_enforcement_activation(
    *,
    authorization: GatedAdmissionResult,
    layer_2_evidence_set_id: str,
    layer_2_environment_state: str,
    dependency_operation_states: Mapping[str, OperationExecutionState | str],
    current_runtime_modes: Mapping[FeatureFlagId | str, str],
    current_activation_states: Mapping[FeatureFlagId | str, str],
) -> EnforcementActivationDecision:
    """Project one exact flag activation after all immutable gates pass.

    An authorization receipt is necessary but not sufficient.  Its request
    must still match the registry tuple, the dedicated Layer 2 set must be
    ACCEPTED, and a non-NONE cutover dependency must be ACCEPTED.  The result
    changes only the target flag in copied canonical mappings.
    """

    modes = {str(key): str(value) for key, value in current_runtime_modes.items()}
    activations = {str(key): str(value) for key, value in current_activation_states.items()}
    required_flags = {item.flag_id.value for item in FEATURE_FLAG_REGISTRY}
    reasons: list[str] = []
    request = authorization.request
    target_flag_id: str | None = None
    binding = None
    if (
        authorization.verdict is not GatedAdmissionVerdict.AUTHORIZED_NOT_EXECUTED
        or authorization.operation_state is not OperationExecutionState.AUTHORIZED_NOT_EXECUTED
        or request is None
        or not request.operation_id.startswith("enforcement.")
    ):
        reasons.append("EXACT_ENFORCEMENT_APPROVAL_REQUIRED")
    else:
        target_flag_id = request.operation_id.removeprefix("enforcement.")
        try:
            binding = feature_flag_binding(FeatureFlagId(target_flag_id))
        except (ValueError, GovernanceError):
            reasons.append(FEATURE_FLAG_REGISTRY_MISMATCH)
        if binding is not None and (
            request.feature_flag_id,
            request.layer_2_evidence_set_id,
            request.projection_cutover_dependency,
        ) != (
            binding.flag_id.value,
            binding.layer_2_evidence_set_id,
            binding.cutover_dependency,
        ):
            reasons.append(FEATURE_FLAG_REGISTRY_MISMATCH)

    if set(modes) != required_flags or set(activations) != required_flags:
        reasons.append("FLAG_STATE_SET_MISMATCH")
    if binding is not None:
        if layer_2_evidence_set_id != binding.layer_2_evidence_set_id:
            reasons.append("LAYER_2_EVIDENCE_SET_MISMATCH")
        if layer_2_environment_state != "ACCEPTED":
            reasons.append("LAYER_2_EVIDENCE_NOT_ACCEPTED")
        dependency = binding.cutover_dependency
        if dependency != "NONE":
            state = dependency_operation_states.get(dependency)
            if not (state is OperationExecutionState.ACCEPTED or state == "ACCEPTED"):
                reasons.append("PROJECTION_CUTOVER_DEPENDENCY_NOT_ACCEPTED")
        if target_flag_id is not None and activations.get(target_flag_id) != "AUTHORIZED_NOT_ACTIVE":
            reasons.append("FLAG_NOT_AUTHORIZED_NOT_ACTIVE")

    if not reasons and binding is not None and target_flag_id is not None:
        modes[target_flag_id] = "ENFORCE"
        activations[target_flag_id] = "ACTIVE"
    return EnforcementActivationDecision(
        admitted=not reasons,
        target_flag_id=target_flag_id,
        reason_codes=tuple(sorted(set(reasons))),
        runtime_modes=tuple(sorted(modes.items())),
        activation_states=tuple(sorted(activations.items())),
    )


ApprovalProvider = Callable[[ApprovalBinding], SeparateWriteApproval | ImmutableEnvelope | None]


def _parse_scope(payload: Mapping[str, Any]) -> ReleaseScopeManifest:
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise GovernanceError("scope_manifest_incomplete", "scope entries must be a list")
    entries: list[ReleaseScopeEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise GovernanceError("invalid_scope_entry", "scope entry must be an object")
        bindings: list[EvidenceBinding] = []
        for binding in raw.get("evidence_bindings") or ():
            if not isinstance(binding, Mapping):
                raise GovernanceError("invalid_evidence_binding", "evidence binding must be an object")
            bindings.append(
                EvidenceBinding(
                    direction=EvidenceBindingDirection(binding.get("direction")),
                    evidence_set_key=binding.get("evidence_set_key"),
                )
            )
        entries.append(
            ReleaseScopeEntry(
                operation_id=raw.get("operation_id"),
                inclusion=ReleaseInclusion(raw.get("release_inclusion")),
                completion_impact=CompletionImpact(raw.get("completion_impact")),
                target_environment_key=raw.get("target_environment_key"),
                operation_plan_key=raw.get("operation_plan_key"),
                evidence_bindings=tuple(bindings),
                dependency_operation_ids=tuple(raw.get("dependency_operation_ids") or ()),
                declaration_reason_code=raw.get("declaration_reason_code") or "",
            )
        )
    return ReleaseScopeManifest(
        registry_snapshot_id=payload.get("registry_snapshot_id"),
        entries=tuple(entries),
        behavior_change_barrier_event_id=payload.get("behavior_change_barrier_event_id"),
    )


def _scope_freeze_events(events: GovernanceEventLog) -> tuple[ImmutableEnvelope, ...]:
    return tuple(
        event
        for event in events.iter_events()
        if event.payload.get("event_type") == SCOPE_FROZEN_EVENT
    )


def admit_frozen_scope(
    *,
    request: GatedOperationRequest,
    store: GovernanceObjectStore,
    events: GovernanceEventLog,
) -> FrozenScopeAdmission:
    """Verify content identity, current freeze event, registry and flag binding."""

    envelope = store.get(request.release_scope_manifest_id)
    if envelope.kind != RELEASE_SCOPE_KIND:
        raise GovernanceError(RELEASE_SCOPE_INVALID, "referenced object is not a release scope")
    scope = _parse_scope(envelope.payload)

    registry = store.get(scope.registry_snapshot_id)
    if registry.kind != REGISTRY_SNAPSHOT_KIND or registry.payload != registry_snapshot_payload():
        raise GovernanceError(RELEASE_SCOPE_INVALID, "scope registry snapshot is not authoritative")
    if scope.registry_snapshot_id not in envelope.referenced_object_ids:
        raise GovernanceError(RELEASE_SCOPE_INVALID, "scope does not bind its registry object")
    if scope.behavior_change_barrier_event_id not in envelope.referenced_object_ids:
        raise GovernanceError(RELEASE_SCOPE_INVALID, "scope does not bind its behavior barrier")
    store.get(scope.behavior_change_barrier_event_id)

    freeze_events = _scope_freeze_events(events)
    applicable = tuple(
        event
        for event in freeze_events
        if (event.payload.get("payload") or {}).get("release_scope_manifest_id") == envelope.object_id
        and envelope.object_id in event.referenced_object_ids
    )
    if not applicable:
        raise GovernanceError(RELEASE_SCOPE_INVALID, "scope has no append-only freeze event")
    latest = max(freeze_events, key=lambda event: (event.created_at, event.object_id))
    latest_scope_id = (latest.payload.get("payload") or {}).get("release_scope_manifest_id")
    if latest_scope_id != envelope.object_id:
        raise GovernanceError(RELEASE_SCOPE_STALE, "a newer frozen release scope exists")
    if envelope.created_at >= request.requested_at or max(item.created_at for item in applicable) >= request.requested_at:
        raise GovernanceError(RELEASE_SCOPE_INVALID, "scope was not frozen before the request")

    entry = scope.entry_for(request.operation_id)
    if request.operation_id.startswith("enforcement."):
        expected_flag = request.operation_id.removeprefix("enforcement.")
        try:
            binding = feature_flag_binding(FeatureFlagId(expected_flag))
        except (ValueError, GovernanceError) as exc:
            raise GovernanceError(
                FEATURE_FLAG_REGISTRY_MISMATCH, "operation has no exact feature flag binding"
            ) from exc
        supplied = (
            request.feature_flag_id,
            request.layer_2_evidence_set_id,
            request.projection_cutover_dependency,
        )
        expected = (
            binding.flag_id.value,
            binding.layer_2_evidence_set_id,
            binding.cutover_dependency,
        )
        if supplied != expected:
            raise GovernanceError(
                FEATURE_FLAG_REGISTRY_MISMATCH,
                "flag/evidence/cutover binding does not match the frozen registry",
            )
    elif any(
        item is not None
        for item in (
            request.feature_flag_id,
            request.layer_2_evidence_set_id,
            request.projection_cutover_dependency,
        )
    ):
        raise GovernanceError(
            FEATURE_FLAG_REGISTRY_MISMATCH,
            "non-enforcement operations cannot borrow a feature flag binding",
        )
    return FrozenScopeAdmission(envelope, scope, entry)


def _assert_assertion_set(
    assertions: tuple[GateAssertion, ...],
    expected_ids: set[str],
    *,
    label: str,
) -> None:
    actual_ids = [item.assertion_id for item in assertions]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
        raise GovernanceError(
            MIGRATION_SAFETY_GATE_FAILED, f"{label} must cover every plan operation exactly once"
        )
    if not all(item.passed for item in assertions):
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, f"one or more {label} failed")


def validate_migration_pre_execution_gates(
    *,
    request: GatedOperationRequest,
    gates: MigrationPreExecutionGates,
    store: GovernanceObjectStore,
) -> None:
    """Validate read-only/additive/restore/capacity/precondition gates only."""

    plan = gates.plan
    if not plan.read_only or plan.created_at >= request.requested_at:
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "an earlier read-only plan is required")
    if (
        plan.plan_key != request.operation_plan_key
        or plan.plan_hash != request.plan_hash
        or plan.artifact_checksum != request.artifact_checksum
    ):
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "plan identity/checksum mismatch")
    disallowed = sorted({item.kind for item in plan.operations} - _ADDITIVE_ALLOWLIST)
    if disallowed:
        raise GovernanceError(
            MIGRATION_NOT_ADDITIVE, f"migration operations are outside additive allowlist: {disallowed}"
        )

    manifest = store.get(gates.restore_capacity_manifest_id)
    if manifest.kind != RESTORE_CAPACITY_KIND:
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "restore/capacity object kind mismatch")
    validate_manifest_payload(RESTORE_CAPACITY_KIND, manifest.payload)
    if (
        manifest.payload.get("migration_plan_key") != plan.plan_key
        or manifest.payload.get("target_environment_key") != request.target_environment_key
    ):
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "restore/capacity manifest binding mismatch")
    admitted = preflight_admission(store, plan.plan_key, gates.checked_at)
    if admitted.manifest is None or admitted.manifest.object_id != manifest.object_id:
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "restore/capacity manifest is not admitted")
    if not gates.verified_restore_point or not gates.capacity_check_passed:
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "restore point or capacity check failed")
    _assert_assertion_set(
        gates.preconditions,
        {item.operation_id for item in plan.operations},
        label="preconditions",
    )


def validate_migration_post_execution_gates(
    *,
    authorization: GatedAdmissionResult,
    evidence: MigrationPostExecutionGates,
) -> None:
    """Validate postconditions and immediate second-run no-op evidence.

    This consumes hashes/assertions only; it does not perform either run.
    """

    if (
        authorization.verdict is not GatedAdmissionVerdict.AUTHORIZED_NOT_EXECUTED
        or authorization.operation_state is not OperationExecutionState.AUTHORIZED_NOT_EXECUTED
        or authorization.request is None
        or authorization.migration_plan is None
    ):
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "no exact migration authorization")
    request = authorization.request
    plan = authorization.migration_plan
    if (
        evidence.operation_instance_id != request.operation_instance_id
        or evidence.plan_hash != request.plan_hash
        or evidence.artifact_checksum != request.artifact_checksum
        or evidence.checked_at < request.requested_at
    ):
        raise GovernanceError(MIGRATION_SAFETY_GATE_FAILED, "postcheck identity mismatch")
    operation_ids = {item.operation_id for item in plan.operations}
    _assert_assertion_set(evidence.postconditions, operation_ids, label="postconditions")
    _assert_assertion_set(
        evidence.second_run_no_op_checks,
        operation_ids,
        label="second_run_no_op_checks",
    )
    if evidence.first_run_snapshot != evidence.second_run_snapshot:
        raise GovernanceError(
            MIGRATION_SAFETY_GATE_FAILED,
            "second run changed schema, data counts or migration state",
        )


class GatedExecutorBoundary:
    """Governance admission façade with no execution capability."""

    def __init__(
        self,
        *,
        store: GovernanceObjectStore,
        events: GovernanceEventLog,
        code_revision: str,
    ) -> None:
        self._store = store
        self._events = events
        self._code_revision = _nonempty(code_revision, field_name="code_revision")

    def _record(self, request: GatedOperationRequest, result: GatedAdmissionResult) -> None:
        references = tuple(
            item
            for item in (result.release_scope_manifest_id, result.approval_object_id)
            if item is not None
        )
        self._events.append(
            event_type=GATED_ADMISSION_EVENT,
            actor=request.actor,
            code_revision=self._code_revision,
            recorded_at=request.requested_at,
            payload={
                "operation_id": request.operation_id,
                "operation_instance_id": request.operation_instance_id,
                "approval_object_id": result.approval_object_id,
                "operation_state": result.operation_state.value,
                "production_write_admitted": False,
                "reason_code": result.reason_code,
                "release_scope_manifest_id": result.release_scope_manifest_id,
                "verdict": result.verdict.value,
            },
            referenced_object_ids=references,
        )

    def admit(
        self,
        request: GatedOperationRequest,
        *,
        approval_provider: ApprovalProvider,
        migration_gates: MigrationPreExecutionGates | None = None,
    ) -> GatedAdmissionResult:
        """Return an authorization receipt, never invoke Production_Write."""

        try:
            scope_admission = admit_frozen_scope(
                request=request, store=self._store, events=self._events
            )
        except (GovernanceError, TypeError, ValueError) as exc:
            code = exc.code if isinstance(exc, GovernanceError) else RELEASE_SCOPE_INVALID
            result = GatedAdmissionResult(
                verdict=GatedAdmissionVerdict.BLOCKED,
                operation_state=OperationExecutionState.PLANNED,
                reason_code=code,
                reasons=(str(exc),),
                release_scope_manifest_id=request.release_scope_manifest_id,
                request=request,
            )
            self._record(request, result)
            return result

        entry = scope_admission.entry
        if entry.inclusion is ReleaseInclusion.EXCLUDED:
            result = GatedAdmissionResult(
                verdict=GatedAdmissionVerdict.EXCLUDED,
                operation_state=excluded_operation_terminal_state(),
                reason_code="scope_excluded_terminal",
                release_scope_manifest_id=scope_admission.scope_envelope.object_id,
                scope_admission=scope_admission,
                request=request,
            )
            self._record(request, result)
            return result

        if (
            entry.target_environment_key != request.target_environment_key
            or entry.operation_plan_key != request.operation_plan_key
        ):
            result = GatedAdmissionResult(
                verdict=GatedAdmissionVerdict.BLOCKED,
                operation_state=OperationExecutionState.PLANNED,
                reason_code=RELEASE_SCOPE_INVALID,
                reasons=("scope_environment_or_plan_mismatch",),
                release_scope_manifest_id=scope_admission.scope_envelope.object_id,
                scope_admission=scope_admission,
                request=request,
            )
            self._record(request, result)
            return result

        plan: ReadOnlyMigrationPlan | None = None
        if request.operation_id == MIGRATION_APPLY_OPERATION_ID:
            if migration_gates is None:
                result = GatedAdmissionResult(
                    verdict=GatedAdmissionVerdict.BLOCKED,
                    operation_state=OperationExecutionState.PLANNED,
                    reason_code=MIGRATION_SAFETY_GATE_FAILED,
                    reasons=("migration_gates_missing",),
                    release_scope_manifest_id=scope_admission.scope_envelope.object_id,
                    scope_admission=scope_admission,
                    request=request,
                )
                self._record(request, result)
                return result
            try:
                validate_migration_pre_execution_gates(
                    request=request, gates=migration_gates, store=self._store
                )
                plan = migration_gates.plan
            except (GovernanceError, TypeError, ValueError) as exc:
                code = exc.code if isinstance(exc, GovernanceError) else MIGRATION_SAFETY_GATE_FAILED
                result = GatedAdmissionResult(
                    verdict=GatedAdmissionVerdict.BLOCKED,
                    operation_state=OperationExecutionState.PLANNED,
                    reason_code=code,
                    reasons=(str(exc),),
                    release_scope_manifest_id=scope_admission.scope_envelope.object_id,
                    scope_admission=scope_admission,
                    request=request,
                )
                self._record(request, result)
                return result
        elif migration_gates is not None:
            result = GatedAdmissionResult(
                verdict=GatedAdmissionVerdict.BLOCKED,
                operation_state=OperationExecutionState.PLANNED,
                reason_code=MIGRATION_SAFETY_GATE_FAILED,
                reasons=("migration_gates_not_transferable",),
                release_scope_manifest_id=scope_admission.scope_envelope.object_id,
                scope_admission=scope_admission,
                request=request,
            )
            self._record(request, result)
            return result

        approval_object_id: str | None = None
        try:
            supplied = approval_provider(request.approval_binding)
            if isinstance(supplied, ImmutableEnvelope):
                approval_object_id = supplied.object_id
                approval = separate_write_approval_from_envelope(supplied)
            elif supplied is None or isinstance(supplied, SeparateWriteApproval):
                approval = supplied
            else:
                raise GovernanceError("invalid_approval", "approval provider returned an invalid type")
            validation: ApprovalValidation = validate_separate_write_approval(
                approval,
                expected=request.approval_binding,
                at=request.requested_at,
                approval_object_id=approval_object_id,
            )
        except (GovernanceError, TypeError, ValueError) as exc:
            validation = ApprovalValidation(
                verdict=ApprovalVerdict.MISMATCH,
                mismatches=(f"approval_invalid:{getattr(exc, 'code', type(exc).__name__)}",),
            )
        if not validation.authorized:
            result = GatedAdmissionResult(
                verdict=GatedAdmissionVerdict.BLOCKED,
                operation_state=OperationExecutionState.NOT_AUTHORIZED,
                reason_code=APPROVAL_MISSING_OR_MISMATCH,
                reasons=validation.mismatches,
                release_scope_manifest_id=scope_admission.scope_envelope.object_id,
                approval_object_id=validation.approval_object_id,
                scope_admission=scope_admission,
                request=request,
                migration_plan=plan,
            )
            self._record(request, result)
            return result

        result = GatedAdmissionResult(
            verdict=GatedAdmissionVerdict.AUTHORIZED_NOT_EXECUTED,
            operation_state=OperationExecutionState.AUTHORIZED_NOT_EXECUTED,
            reason_code="exact_approval_and_preconditions_admitted",
            release_scope_manifest_id=scope_admission.scope_envelope.object_id,
            approval_object_id=validation.approval_object_id,
            scope_admission=scope_admission,
            request=request,
            migration_plan=plan,
        )
        self._record(request, result)
        return result

    def verify_migration_post_execution(
        self,
        authorization: GatedAdmissionResult,
        evidence: MigrationPostExecutionGates,
    ) -> GatedAdmissionResult:
        """Verify supplied Layer 2 evidence; never perform the migration runs."""

        validate_migration_post_execution_gates(authorization=authorization, evidence=evidence)
        assert authorization.request is not None
        result = GatedAdmissionResult(
            verdict=GatedAdmissionVerdict.POSTCHECKS_VERIFIED,
            operation_state=OperationExecutionState.EXECUTED,
            reason_code="postconditions_and_second_run_noop_verified",
            release_scope_manifest_id=authorization.release_scope_manifest_id,
            approval_object_id=authorization.approval_object_id,
            scope_admission=authorization.scope_admission,
            request=authorization.request,
            migration_plan=authorization.migration_plan,
        )
        self._events.append(
            event_type=MIGRATION_POSTCHECK_EVENT,
            actor=authorization.request.actor,
            code_revision=self._code_revision,
            recorded_at=evidence.checked_at,
            payload={
                "operation_id": authorization.request.operation_id,
                "operation_instance_id": authorization.request.operation_instance_id,
                **result.canonical_payload(),
                "schema_model_hash": evidence.second_run_snapshot.schema_model_hash,
                "data_counts_hash": evidence.second_run_snapshot.data_counts_hash,
                "migration_state_hash": evidence.second_run_snapshot.migration_state_hash,
            },
            referenced_object_ids=tuple(
                item
                for item in (result.release_scope_manifest_id, result.approval_object_id)
                if item is not None
            ),
        )
        return result


__all__ = [
    "APPROVAL_MISSING_OR_MISMATCH",
    "FEATURE_FLAG_REGISTRY_MISMATCH",
    "GATED_ADMISSION_EVENT",
    "MIGRATION_NOT_ADDITIVE",
    "MIGRATION_POSTCHECK_EVENT",
    "MIGRATION_SAFETY_GATE_FAILED",
    "RELEASE_SCOPE_INVALID",
    "RELEASE_SCOPE_STALE",
    "ApprovalProvider",
    "EnforcementActivationDecision",
    "FrozenScopeAdmission",
    "GateAssertion",
    "GatedAdmissionResult",
    "GatedAdmissionVerdict",
    "GatedExecutorBoundary",
    "GatedOperationRequest",
    "MigrationPlanOperation",
    "MigrationPostExecutionGates",
    "MigrationPreExecutionGates",
    "MigrationRunSnapshot",
    "ReadOnlyMigrationPlan",
    "admit_frozen_scope",
    "evaluate_enforcement_activation",
    "validate_migration_post_execution_gates",
    "validate_migration_pre_execution_gates",
]

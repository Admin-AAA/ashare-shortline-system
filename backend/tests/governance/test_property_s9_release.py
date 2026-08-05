"""S9 release-governance properties and task 9.28 constructed examples.

All examples are pure Layer-1 checks.  Stores live under TemporaryDirectory,
inputs are constructed in memory, and no Production_Write, database, migration,
network, subprocess, secret resolver, or runtime feature-flag adapter exists in
this module.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st
import pytest

from app.governance.approval import (
    ApprovalBinding,
    ApprovalVerdict,
    SeparateWriteApproval,
    validate_separate_write_approval,
)
from app.governance.binding import (
    TASK_1_3_BINDING_SCOPE,
    TASK_1_3_REQUIRED_KINDS,
    DependencyDigest,
    RevisionBinding,
    RevisionBindingStatus,
    evaluate_task_1_3_completion,
)
from app.governance.completion import (
    AppendOnlyDataSnapshot,
    FeatureFlagRuntimeMode,
    HistoryConservation,
    ReadPathState,
    assert_append_only_preserved,
    build_completion_required_work_set,
    build_final_evidence_report,
    evaluate_production_readiness,
    evaluate_spec_completion,
    rollback_read_path,
)
from app.governance.envelope import GovernanceError
from app.governance.events import GovernanceEventLog
from app.governance.freeze import REGISTRY_SNAPSHOT_KIND, RELEASE_SCOPE_KIND
from app.governance.gated_executor import (
    FEATURE_FLAG_REGISTRY_MISMATCH,
    MIGRATION_NOT_ADDITIVE,
    MIGRATION_SAFETY_GATE_FAILED,
    RELEASE_SCOPE_INVALID,
    RELEASE_SCOPE_STALE,
    GateAssertion,
    GatedAdmissionResult,
    GatedAdmissionVerdict,
    GatedOperationRequest,
    MigrationPlanOperation,
    MigrationPostExecutionGates,
    MigrationPreExecutionGates,
    MigrationRunSnapshot,
    ReadOnlyMigrationPlan,
    admit_frozen_scope,
    evaluate_enforcement_activation,
    validate_migration_post_execution_gates,
    validate_migration_pre_execution_gates,
)
from app.governance.manifests import (
    LAYER_2_EVIDENCE_KIND,
    RESTORE_CAPACITY_KIND,
    SHADOW_THRESHOLD_KIND,
    AdmissionContext,
    AdmissionVerdict,
    Layer2EvidenceClaim,
    admit_acceptance_attempt,
    validate_manifest_payload,
)
from app.governance.registries import (
    FEATURE_FLAG_REGISTRY,
    MIGRATION_APPLY_OPERATION_ID,
    OPERATION_REGISTRY,
    PROJECTION_CUTOVER_OPERATION_ID,
    FeatureFlagId,
    feature_flag_binding,
    operation_ids,
    registry_snapshot_payload,
    validate_feature_flag_registry,
)
from app.governance.scope import (
    CompletionImpact,
    ReleaseInclusion,
    ReleaseScopeEntry,
    ReleaseScopeManifest,
)
from app.governance.state import (
    CodeState,
    EnvironmentAcceptanceState,
    OperationExecutionState,
    ProductionActivationState,
    SpecCompletionState,
    apply_layer1_environment_result,
    excluded_operation_terminal_state,
    transition_code_state,
)
from app.governance.store import GovernanceObjectStore
from app.services.execution_parity import (
    ShadowParityVerdict,
    compare_execution_payloads,
    evaluate_shadow_threshold_manifest,
)

_REVISION = "a" * 40
_NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
_REGISTERED = tuple(operation_id for operation_id, _kind in OPERATION_REGISTRY)
_NON_MIGRATION = tuple(item for item in _REGISTERED if item != MIGRATION_APPLY_OPERATION_ID)
_FLAG_IDS = tuple(binding.flag_id.value for binding in FEATURE_FLAG_REGISTRY)
_SAFE_TOKEN = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=24
)


def _entry(
    operation_id: str,
    *,
    inclusion: ReleaseInclusion = ReleaseInclusion.EXCLUDED,
    impact: CompletionImpact = CompletionImpact.NON_CRITICAL,
    reason: str = "property",
) -> ReleaseScopeEntry:
    if operation_id == MIGRATION_APPLY_OPERATION_ID:
        inclusion = ReleaseInclusion.INCLUDED
        impact = CompletionImpact.COMPLETION_CRITICAL
    return ReleaseScopeEntry(
        operation_id=operation_id,
        inclusion=inclusion,
        completion_impact=impact,
        target_environment_key="env-test",
        operation_plan_key=f"plan/{operation_id}",
        declaration_reason_code=reason,
    )


def _scope(
    dispositions: dict[str, tuple[ReleaseInclusion, CompletionImpact]] | None = None,
    *,
    registry_snapshot_id: str = "registry-snapshot",
    barrier_id: str = "behavior-barrier",
    order: tuple[str, ...] = _REGISTERED,
) -> ReleaseScopeManifest:
    dispositions = dispositions or {}
    return ReleaseScopeManifest(
        registry_snapshot_id=registry_snapshot_id,
        entries=tuple(
            _entry(
                operation_id,
                inclusion=dispositions.get(
                    operation_id,
                    (ReleaseInclusion.EXCLUDED, CompletionImpact.NON_CRITICAL),
                )[0],
                impact=dispositions.get(
                    operation_id,
                    (ReleaseInclusion.EXCLUDED, CompletionImpact.NON_CRITICAL),
                )[1],
            )
            for operation_id in order
        ),
        behavior_change_barrier_event_id=barrier_id,
    )


def _freeze_scope(
    root: str | Path,
    scope_factory,
    *,
    frozen_at: datetime = _NOW,
    supersedes_id: str | None = None,
):
    store = GovernanceObjectStore(root)
    events = GovernanceEventLog(store)
    registry = store.put(
        kind=REGISTRY_SNAPSHOT_KIND,
        payload=registry_snapshot_payload(),
        created_at=frozen_at - timedelta(minutes=2),
        created_by="property-author",
        code_revision=_REVISION,
    )
    barrier = events.append(
        event_type="BASELINE_AUDIT_VERIFIED",
        actor="property-author",
        code_revision=_REVISION,
        recorded_at=frozen_at - timedelta(minutes=1),
        payload={"barrier": "s9"},
    )
    manifest = scope_factory(registry.object_id, barrier.object_id)
    envelope = store.put(
        kind=RELEASE_SCOPE_KIND,
        payload=manifest.canonical_payload(),
        created_at=frozen_at,
        created_by="property-author",
        code_revision=_REVISION,
        supersedes_id=supersedes_id,
        referenced_object_ids=(registry.object_id, barrier.object_id),
    )
    events.append(
        event_type="RELEASE_SCOPE_FROZEN",
        actor="property-author",
        code_revision=_REVISION,
        recorded_at=frozen_at + timedelta(seconds=1),
        payload={"release_scope_manifest_id": envelope.object_id},
        referenced_object_ids=(envelope.object_id,),
    )
    return store, events, manifest, envelope


def _request(
    scope_id: str,
    operation_id: str,
    *,
    requested_at: datetime = _NOW + timedelta(minutes=1),
    flag_tuple: tuple[str, str, str] | None = None,
) -> GatedOperationRequest:
    if flag_tuple is None and operation_id.startswith("enforcement."):
        binding = feature_flag_binding(operation_id.removeprefix("enforcement."))
        flag_tuple = (
            binding.flag_id.value,
            binding.layer_2_evidence_set_id,
            binding.cutover_dependency,
        )
    flag, evidence, dependency = flag_tuple or (None, None, None)
    return GatedOperationRequest(
        release_scope_manifest_id=scope_id,
        operation_id=operation_id,
        operation_instance_id=f"attempt/{operation_id}",
        target_environment_key="env-test",
        operation_plan_key=f"plan/{operation_id}",
        plan_hash="plan-hash-v1",
        artifact_checksum="artifact-v1",
        actor="release-actor",
        requested_at=requested_at,
        feature_flag_id=flag,
        layer_2_evidence_set_id=evidence,
        projection_cutover_dependency=dependency,
    )


def _approval(binding: ApprovalBinding) -> SeparateWriteApproval:
    return SeparateWriteApproval(
        binding=binding,
        issued_at=_NOW - timedelta(minutes=5),
        expires_at=_NOW + timedelta(hours=1),
        approved_by="release-approver",
        approval_reason_code="CHANGE-9",
    )


def _restore_manifest_payload(plan_key: str = f"plan/{MIGRATION_APPLY_OPERATION_ID}"):
    return {
        "manifest_kind": RESTORE_CAPACITY_KIND,
        "stable_manifest_key": "restore-capacity-v1",
        "registry_snapshot_id": "registry-snapshot-v1",
        "frozen_at": (_NOW - timedelta(hours=2)).isoformat(),
        "applies_to_code_revision": _REVISION,
        "target_environment_key": "env-test",
        "migration_plan_key": plan_key,
        "restore": {
            "restore_point_kind": "snapshot-v1",
            "verification": "SNAPSHOT_CONFIRMED",
        },
        "capacity_rules": [
            {"metric": "DISK_FREE_RATIO", "comparator": ">=", "threshold": 0.25}
        ],
    }


def _migration_contract(root: str | Path, *, operation_kind: str = "add_column"):
    store = GovernanceObjectStore(root)
    restore = store.put(
        kind=RESTORE_CAPACITY_KIND,
        payload=_restore_manifest_payload(),
        created_at=_NOW - timedelta(hours=2),
        created_by="property-author",
        code_revision=_REVISION,
    )
    operation = MigrationPlanOperation(
        operation_id="ddl.operation.1",
        kind=operation_kind,
        precondition_id="pre.1",
        postcondition_id="post.1",
        no_op_verification_id="noop.1",
    )
    plan = ReadOnlyMigrationPlan(
        plan_key=f"plan/{MIGRATION_APPLY_OPERATION_ID}",
        plan_hash="plan-hash-v1",
        artifact_checksum="artifact-v1",
        created_at=_NOW - timedelta(hours=1),
        operations=(operation,),
    )
    request = _request(
        "release_scope_manifest:sha256:" + "1" * 64,
        MIGRATION_APPLY_OPERATION_ID,
    )
    gates = MigrationPreExecutionGates(
        plan=plan,
        restore_capacity_manifest_id=restore.object_id,
        checked_at=_NOW - timedelta(minutes=30),
        verified_restore_point=True,
        capacity_check_passed=True,
        preconditions=(GateAssertion("ddl.operation.1", True),),
    )
    return store, request, gates


def _authorized_migration(request, plan) -> GatedAdmissionResult:
    return GatedAdmissionResult(
        verdict=GatedAdmissionVerdict.AUTHORIZED_NOT_EXECUTED,
        operation_state=OperationExecutionState.AUTHORIZED_NOT_EXECUTED,
        reason_code="exact_approval_and_preconditions_admitted",
        request=request,
        migration_plan=plan,
    )


def _postchecks(request, *, snapshot_equal: bool = True):
    first = MigrationRunSnapshot("schema-v1", "counts-v1", "state-v1")
    second = first if snapshot_equal else MigrationRunSnapshot("schema-v2", "counts-v1", "state-v1")
    return MigrationPostExecutionGates(
        operation_instance_id=request.operation_instance_id,
        plan_hash=request.plan_hash,
        artifact_checksum=request.artifact_checksum,
        checked_at=request.requested_at + timedelta(minutes=1),
        postconditions=(GateAssertion("ddl.operation.1", True),),
        second_run_no_op_checks=(GateAssertion("ddl.operation.1", True),),
        first_run_snapshot=first,
        second_run_snapshot=second,
    )


def _shadow_manifest(metric_id: str):
    return {
        "manifest_kind": SHADOW_THRESHOLD_KIND,
        "stable_manifest_key": "shadow-v1",
        "registry_snapshot_id": "registry-v1",
        "frozen_at": (_NOW - timedelta(hours=1)).isoformat(),
        "applies_to_code_revision": _REVISION,
        "target_operation_id": PROJECTION_CUTOVER_OPERATION_ID,
        "source_projection_id": "old-read-path",
        "candidate_projection_id": "candidate-read-path",
        "rules": [
            {
                "metric_id": metric_id,
                "comparator": "==",
                "threshold_decimal": 0,
                "minimum_sample": 1,
                "failure_action": "BLOCK_CUTOVER",
                "identity_dimensions": ["recommendation_id"],
            }
        ],
    }


def _execution_payload():
    return {
        "entry_triggered": True,
        "entry_date": "2026-08-04",
        "entry_price": "10.00",
        "exit_date": "2026-08-05",
        "exit_price": "10.50",
        "exit_reason": "horizon",
        "gross_return_pct": "5.0",
        "cost_pct": "0.1",
        "position_size": "0.25",
        "net_return_pct": "4.9",
    }


def _layer2_payload():
    return {
        "manifest_kind": LAYER_2_EVIDENCE_KIND,
        "stable_manifest_key": "layer2-v1",
        "registry_snapshot_id": "registry-v1",
        "frozen_at": (_NOW - timedelta(hours=1)).isoformat(),
        "applies_to_code_revision": _REVISION,
        "layer_2_evidence_set_id": "L2_FLAG_TEST_V1",
        "real_environment_key": "real-env-v1",
        "acceptance_command_ids": ["acceptance-command-v1"],
        "artifact_contracts": [
            {
                "contract_id": "result-contract-v1",
                "schema_uri": "schema://layer2/result/v1",
                "evidence_uri_allowlist": ["evidence://sha256/"],
                "required_fields": ["result", "attempt_identity"],
            }
        ],
        "assertions": [
            {"assertion_id": "exit-zero", "required": True, "predicate": "exit_code_zero"},
            {"assertion_id": "schema-valid", "required": True, "predicate": "schema_valid"},
        ],
        "pass_rule": "ALL_REQUIRED_ASSERTIONS_PASS",
    }


def _task13_binding(seed: bytes) -> RevisionBinding:
    return RevisionBinding(
        scope_id=TASK_1_3_BINDING_SCOPE,
        digests=tuple(
            DependencyDigest(
                kind=kind,
                locator=f"dependency/{index}",
                content_sha256=hashlib.sha256(seed + bytes([index])).hexdigest(),
            )
            for index, kind in enumerate(TASK_1_3_REQUIRED_KINDS)
        ),
    )


# Properties 33–44, 47–49, 52: each owns an explicit 100-example budget.


@settings(max_examples=100, deadline=None)
@given(order=st.permutations(_REGISTERED), invalid=st.sampled_from(("none", "missing", "duplicate", "unknown", "excluded_critical", "migration_wrong")))
def test_property_33_release_scope_manifest_is_total_and_canonical(order, invalid):
    """Property 33: ReleaseScopeManifest is total and canonical."""
    entries = tuple(_entry(operation_id) for operation_id in order)
    if invalid == "none":
        manifest = ReleaseScopeManifest("registry", entries, "barrier")
        assert tuple(item.operation_id for item in manifest.entries) == tuple(sorted(_REGISTERED))
        assert set(manifest.canonical_payload()) == {
            "behavior_change_barrier_event_id", "entries", "registry_snapshot_id"
        }
        return
    with pytest.raises(GovernanceError):
        if invalid == "missing":
            ReleaseScopeManifest("registry", entries[:-1], "barrier")
        elif invalid == "duplicate":
            ReleaseScopeManifest("registry", entries + (entries[0],), "barrier")
        elif invalid == "unknown":
            _entry("destructive.unknown")
        elif invalid == "excluded_critical":
            ReleaseScopeEntry(
                operation_id=_NON_MIGRATION[0],
                inclusion=ReleaseInclusion.EXCLUDED,
                completion_impact=CompletionImpact.COMPLETION_CRITICAL,
                target_environment_key="env-test",
                operation_plan_key="plan-test",
            )
        else:
            ReleaseScopeEntry(
                operation_id=MIGRATION_APPLY_OPERATION_ID,
                inclusion=ReleaseInclusion.EXCLUDED,
                completion_impact=CompletionImpact.NON_CRITICAL,
                target_environment_key="env-test",
                operation_plan_key="plan-test",
            )


@settings(max_examples=100, deadline=None)
@given(dispositions=st.lists(st.sampled_from(("excluded", "activation", "critical")), min_size=len(_NON_MIGRATION), max_size=len(_NON_MIGRATION)))
def test_property_34_release_scope_deterministically_constructs_completion_work(dispositions):
    """Property 34: Release scope deterministically constructs completion work."""
    choices = {}
    for operation_id, disposition in zip(_NON_MIGRATION, dispositions, strict=True):
        choices[operation_id] = {
            "excluded": (ReleaseInclusion.EXCLUDED, CompletionImpact.NON_CRITICAL),
            "activation": (ReleaseInclusion.INCLUDED, CompletionImpact.ACTIVATION_ONLY),
            "critical": (ReleaseInclusion.INCLUDED, CompletionImpact.COMPLETION_CRITICAL),
        }[disposition]
    manifest = _scope(choices)
    expected_gated = {
        entry.operation_id
        for entry in manifest.entries
        if entry.inclusion is ReleaseInclusion.INCLUDED
        and entry.completion_impact is CompletionImpact.COMPLETION_CRITICAL
    }
    expected = frozenset({"non-gated.alpha", "non-gated.beta"} | expected_gated)
    excluded_states = {
        operation_id: excluded_operation_terminal_state()
        for operation_id in manifest.excluded_operations()
    }
    assert set(excluded_states) == set(manifest.excluded_operations())
    assert set(excluded_states.values()) == (
        {OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED}
        if excluded_states
        else set()
    )
    assert build_completion_required_work_set(("non-gated.beta", "non-gated.alpha"), manifest) == expected
    operation_states = {
        operation_id: (
            OperationExecutionState.ACCEPTED
            if operation_id in expected_gated
            else OperationExecutionState.FAILED
        )
        for operation_id in operation_ids()
    }
    decision = evaluate_spec_completion(
        {
            "non-gated.alpha": EnvironmentAcceptanceState.ACCEPTED,
            "non-gated.beta": EnvironmentAcceptanceState.ACCEPTED,
        },
        operation_states,
        manifest,
    )
    assert decision.state is SpecCompletionState.COMPLETE
    assert decision.required_work_set == expected
    assert all(
        operation_states[item] is OperationExecutionState.FAILED
        for item in operation_ids() - expected_gated
    )


@settings(max_examples=100, deadline=None)
@given(reason=_SAFE_TOKEN)
def test_property_35_release_scope_revisions_are_immutable_and_precede_behavior(reason):
    """Property 35: Release scope revisions are immutable and precede behavior."""
    with TemporaryDirectory(prefix="s9-property-35-") as root:
        store, events, old, old_envelope = _freeze_scope(
            root,
            lambda registry, barrier: _scope(
                registry_snapshot_id=registry, barrier_id=barrier
            ),
        )
        old_document = store.get(old_envelope.object_id).to_document()
        changed = {
            _NON_MIGRATION[0]: (
                ReleaseInclusion.INCLUDED,
                CompletionImpact.COMPLETION_CRITICAL,
            )
        }
        new_manifest = _scope(
            changed,
            registry_snapshot_id=old.registry_snapshot_id,
            barrier_id=old.behavior_change_barrier_event_id,
        )
        assert build_completion_required_work_set((), old) != build_completion_required_work_set(
            (), new_manifest
        )
        new_envelope = store.put(
            kind=RELEASE_SCOPE_KIND,
            payload={**new_manifest.canonical_payload(), "revision_reason": reason},
            created_at=_NOW + timedelta(minutes=2),
            created_by="property-author",
            code_revision=_REVISION,
            supersedes_id=old_envelope.object_id,
            referenced_object_ids=old_envelope.referenced_object_ids,
        )
        events.append(
            event_type="RELEASE_SCOPE_FROZEN",
            actor="property-author",
            code_revision=_REVISION,
            recorded_at=_NOW + timedelta(minutes=2, seconds=1),
            payload={"release_scope_manifest_id": new_envelope.object_id},
            referenced_object_ids=(new_envelope.object_id,),
        )
        assert new_envelope.object_id != old_envelope.object_id
        assert store.get(old_envelope.object_id).to_document() == old_document
        assert [item.object_id for item in store.supersede_chain(new_envelope.object_id)] == [
            new_envelope.object_id, old_envelope.object_id
        ]
        with pytest.raises(GovernanceError) as stale:
            admit_frozen_scope(
                request=_request(
                    old_envelope.object_id,
                    MIGRATION_APPLY_OPERATION_ID,
                    requested_at=_NOW + timedelta(minutes=3),
                ),
                store=store,
                events=events,
            )
        assert stale.value.code == RELEASE_SCOPE_STALE
        with pytest.raises(GovernanceError) as early:
            admit_frozen_scope(
                request=_request(
                    new_envelope.object_id,
                    MIGRATION_APPLY_OPERATION_ID,
                    requested_at=_NOW + timedelta(minutes=2),
                ),
                store=store,
                events=events,
            )
        assert early.value.code == RELEASE_SCOPE_INVALID


@settings(max_examples=100, deadline=None)
@given(binding=st.sampled_from(FEATURE_FLAG_REGISTRY), mismatched_component=st.sampled_from(("none", "flag", "evidence", "dependency")))
def test_property_36_stable_flag_registry_bindings_are_exact(binding, mismatched_component):
    """Property 36: Stable flag registry bindings are exact."""
    validate_feature_flag_registry()
    assert len(FEATURE_FLAG_REGISTRY) == 6
    assert len({row.layer_2_evidence_set_id for row in FEATURE_FLAG_REGISTRY}) == 6
    operation_id = f"enforcement.{binding.flag_id.value}"
    dispositions = {operation_id: (ReleaseInclusion.INCLUDED, CompletionImpact.ACTIVATION_ONLY)}
    with TemporaryDirectory(prefix="s9-property-36-") as root:
        store, events, _manifest, envelope = _freeze_scope(
            root,
            lambda registry, barrier: _scope(
                dispositions, registry_snapshot_id=registry, barrier_id=barrier
            ),
        )
        supplied = [
            binding.flag_id.value,
            binding.layer_2_evidence_set_id,
            binding.cutover_dependency,
        ]
        if mismatched_component != "none":
            supplied[{"flag": 0, "evidence": 1, "dependency": 2}[mismatched_component]] += ".wrong"
        request = _request(envelope.object_id, operation_id, flag_tuple=tuple(supplied))
        if mismatched_component == "none":
            admission = admit_frozen_scope(request=request, store=store, events=events)
            assert admission.entry.operation_id == operation_id
        else:
            with pytest.raises(GovernanceError) as caught:
                admit_frozen_scope(request=request, store=store, events=events)
            assert caught.value.code == FEATURE_FLAG_REGISTRY_MISMATCH


@settings(max_examples=100, deadline=None)
@given(case=st.sampled_from(("valid", "not_read_only", "late_plan", "drop", "truncate", "historical_delete", "destructive_rebuild", "downgrade")))
def test_property_37_migration_plans_are_read_only_prerequisites_and_additive_only(case):
    """Property 37: Migration plans are read-only prerequisites and additive-only."""
    kind = "add_column" if case in {"valid", "not_read_only", "late_plan"} else case
    with TemporaryDirectory(prefix="s9-property-37-") as root:
        store, request, gates = _migration_contract(root, operation_kind=kind)
        if case == "not_read_only":
            gates = replace(gates, plan=replace(gates.plan, read_only=False))
        elif case == "late_plan":
            gates = replace(gates, plan=replace(gates.plan, created_at=request.requested_at))
        if case == "valid":
            validate_migration_pre_execution_gates(request=request, gates=gates, store=store)
        else:
            with pytest.raises(GovernanceError) as caught:
                validate_migration_pre_execution_gates(request=request, gates=gates, store=store)
            assert caught.value.code in {MIGRATION_NOT_ADDITIVE, MIGRATION_SAFETY_GATE_FAILED}


@settings(max_examples=100, deadline=None)
@given(failed_gate=st.sampled_from(("none", "restore", "capacity", "pre", "post", "noop", "second_run")))
def test_property_38_migration_requires_all_safety_gates_and_is_repeatable(failed_gate):
    """Property 38: Migration execution requires every safety gate and is repeatable."""
    with TemporaryDirectory(prefix="s9-property-38-") as root:
        store, request, gates = _migration_contract(root)
        if failed_gate == "restore":
            gates = replace(gates, verified_restore_point=False)
        elif failed_gate == "capacity":
            gates = replace(gates, capacity_check_passed=False)
        elif failed_gate == "pre":
            gates = replace(gates, preconditions=(GateAssertion("ddl.operation.1", False),))
        if failed_gate in {"restore", "capacity", "pre"}:
            with pytest.raises(GovernanceError) as caught:
                validate_migration_pre_execution_gates(request=request, gates=gates, store=store)
            assert caught.value.code == MIGRATION_SAFETY_GATE_FAILED
            return
        validate_migration_pre_execution_gates(request=request, gates=gates, store=store)
        authorization = _authorized_migration(request, gates.plan)
        evidence = _postchecks(request, snapshot_equal=failed_gate != "second_run")
        if failed_gate == "post":
            evidence = replace(evidence, postconditions=(GateAssertion("ddl.operation.1", False),))
        elif failed_gate == "noop":
            evidence = replace(
                evidence,
                second_run_no_op_checks=(GateAssertion("ddl.operation.1", False),),
            )
        if failed_gate == "none":
            validate_migration_post_execution_gates(
                authorization=authorization, evidence=evidence
            )
            assert evidence.first_run_snapshot == evidence.second_run_snapshot
        else:
            with pytest.raises(GovernanceError) as caught:
                validate_migration_post_execution_gates(
                    authorization=authorization, evidence=evidence
                )
            assert caught.value.code == MIGRATION_SAFETY_GATE_FAILED


@settings(max_examples=100, deadline=None)
@given(operation_id=st.sampled_from(_REGISTERED), mismatch=st.sampled_from(("none", "operation", "instance", "environment", "plan", "artifact", "actor", "flag", "expired", "future")))
def test_property_39_approvals_are_exact_and_nontransitive(operation_id, mismatch):
    """Property 39: Approvals are exact and non-transitive."""
    flag_id = operation_id.removeprefix("enforcement.") if operation_id.startswith("enforcement.") else None
    expected = ApprovalBinding(
        operation_id=operation_id,
        operation_instance_id="attempt-1",
        target_environment_key="env-test",
        plan_hash="plan-v1",
        artifact_checksum="artifact-v1",
        actor="actor-1",
        feature_flag_id=flag_id,
    )
    actual = expected
    replacements = {
        "operation": {"operation_id": next(item for item in _REGISTERED if item != operation_id)},
        "instance": {"operation_instance_id": "attempt-2"},
        "environment": {"target_environment_key": "env-other"},
        "plan": {"plan_hash": "plan-v2"},
        "artifact": {"artifact_checksum": "artifact-v2"},
        "actor": {"actor": "actor-2"},
        "flag": {"feature_flag_id": "other-flag" if flag_id else "borrowed-flag"},
    }
    if mismatch in replacements:
        actual = replace(actual, **replacements[mismatch])
    approval = _approval(actual)
    at = _NOW
    if mismatch == "expired":
        at = approval.expires_at
    elif mismatch == "future":
        at = approval.issued_at - timedelta(microseconds=1)
    result = validate_separate_write_approval(approval, expected=expected, at=at)
    assert result.authorized is (mismatch == "none")
    assert result.verdict is (
        ApprovalVerdict.VALID
        if mismatch == "none"
        else ApprovalVerdict.EXPIRED
        if mismatch == "expired"
        else ApprovalVerdict.NOT_YET_VALID
        if mismatch == "future"
        else ApprovalVerdict.MISMATCH
    )


_PARITY_FIELDS = {
    "entry_date": "entry_date_mismatch_count",
    "entry_price": "entry_price_mismatch_count",
    "exit_date": "exit_date_mismatch_count",
    "exit_price": "exit_price_mismatch_count",
    "cost_pct": "cost_mismatch_count",
    "position_size": "position_size_mismatch_count",
    "exit_reason": "exit_reason_mismatch_count",
    "net_return_pct": "net_return_mismatch_count",
}


@settings(max_examples=100, deadline=None)
@given(field=st.sampled_from(tuple(_PARITY_FIELDS)), replacement_value=_SAFE_TOKEN)
def test_property_40_shadow_threshold_failure_preserves_old_read_path(field, replacement_value):
    """Property 40: Shadow threshold failure preserves the old read path."""
    source = _execution_payload()
    candidate = {**source, field: source[field] + ".changed." + replacement_value}
    observation = compare_execution_payloads(
        recommendation_identity="recommendation-1",
        source_snapshot_id="snapshot-1",
        execution_policy_fingerprint="f" * 64,
        code_revision=_REVISION,
        source_adapter_role="old-authoritative",
        candidate_adapter_role="candidate-shadow",
        source_payload=source,
        candidate_payload=candidate,
        candidate_t_plus_one=True,
    )
    decision = evaluate_shadow_threshold_manifest(
        manifest_id="shadow-threshold:sha256:" + "2" * 64,
        manifest_payload=_shadow_manifest(_PARITY_FIELDS[field]),
        observations=(observation,),
    )
    assert decision.verdict is ShadowParityVerdict.BLOCKED
    assert decision.old_projection_remains_authoritative is True
    assert decision.environment_acceptance_state == "BLOCKED"


@settings(max_examples=100, deadline=None)
@given(existing=st.dictionaries(_SAFE_TOKEN, _SAFE_TOKEN, min_size=1, max_size=12), appended=st.dictionaries(_SAFE_TOKEN, _SAFE_TOKEN, max_size=12), selector=_SAFE_TOKEN)
def test_property_41_read_path_rollback_preserves_append_only_data(existing, appended, selector):
    """Property 41: Read-path rollback preserves append-only data."""
    before = AppendOnlyDataSnapshot.capture({"evidence": existing})
    merged = {**existing, **{key: value for key, value in appended.items() if key not in existing}}
    after_append = AppendOnlyDataSnapshot.capture({"evidence": merged})
    conservation = assert_append_only_preserved(before, after_append)
    current = ReadPathState("candidate-read-path", after_append)
    rolled_back = rollback_read_path(current, restore_selector=selector)
    assert conservation.preserved
    assert rolled_back.selector == selector
    assert rolled_back.append_only_data is current.append_only_data
    assert rolled_back.append_only_data.as_mapping()["evidence"] == merged


@settings(max_examples=100, deadline=None)
@given(binding=st.sampled_from(FEATURE_FLAG_REGISTRY), failed_requirement=st.sampled_from(("none", "approval", "evidence_id", "evidence_state", "dependency")))
def test_property_42_enforcement_activation_requires_exact_evidence_and_dependency(binding, failed_requirement):
    """Property 42: Enforcement activation requires exact evidence and dependency."""
    operation_id = f"enforcement.{binding.flag_id.value}"
    request = _request(
        "release_scope_manifest:sha256:" + "3" * 64,
        operation_id,
    )
    authorization = GatedAdmissionResult(
        verdict=(
            GatedAdmissionVerdict.BLOCKED
            if failed_requirement == "approval"
            else GatedAdmissionVerdict.AUTHORIZED_NOT_EXECUTED
        ),
        operation_state=(
            OperationExecutionState.NOT_AUTHORIZED
            if failed_requirement == "approval"
            else OperationExecutionState.AUTHORIZED_NOT_EXECUTED
        ),
        reason_code="constructed-layer1-receipt",
        request=request,
    )
    modes = {flag_id: "SHADOW" for flag_id in _FLAG_IDS}
    activations = {flag_id: "AUTHORIZED_NOT_ACTIVE" for flag_id in _FLAG_IDS}
    dependencies = (
        {binding.cutover_dependency: OperationExecutionState.ACCEPTED}
        if binding.cutover_dependency != "NONE"
        else {}
    )
    evidence_id = binding.layer_2_evidence_set_id
    evidence_state = "ACCEPTED"
    if failed_requirement == "evidence_id":
        evidence_id += ".wrong"
    elif failed_requirement == "evidence_state":
        evidence_state = "BLOCKED"
    elif failed_requirement == "dependency":
        if binding.cutover_dependency == "NONE":
            authorization = replace(authorization, verdict=GatedAdmissionVerdict.BLOCKED)
        else:
            dependencies[binding.cutover_dependency] = OperationExecutionState.FAILED
    decision = evaluate_enforcement_activation(
        authorization=authorization,
        layer_2_evidence_set_id=evidence_id,
        layer_2_environment_state=evidence_state,
        dependency_operation_states=dependencies,
        current_runtime_modes=modes,
        current_activation_states=activations,
    )
    assert decision.admitted is (failed_requirement == "none")
    for flag_id in _FLAG_IDS:
        if failed_requirement == "none" and flag_id == binding.flag_id.value:
            assert decision.runtime_mode_for(flag_id) == "ENFORCE"
            assert decision.activation_state_for(flag_id) == "ACTIVE"
        else:
            assert decision.runtime_mode_for(flag_id) == modes[flag_id]
            assert decision.activation_state_for(flag_id) == activations[flag_id]


_SECRET_VALUES = st.sampled_from(
    (
        "Bearer abcdefghijklmnop.token",
        "password=hunter2",
        "api_key=abcdef0123456789",
        "mysql://reader:supersecret@db.internal/ashare",
        "".join(("-----", "BEGIN ", "PRIVATE ", "KEY", "-----")),
        "sk-abcdefghijklmnopqrstuvwx",
    )
)


@settings(max_examples=100, deadline=None)
@given(secret=_SECRET_VALUES, depth=st.integers(min_value=0, max_value=3))
def test_property_43_persisted_operational_artifacts_never_contain_secrets(secret, depth):
    """Property 43: Persisted operational artifacts never contain secrets."""
    payload = _shadow_manifest("net_return_mismatch_count")
    nested: object = secret
    for index in range(depth):
        nested = {f"nested_{index}": [nested]}
    payload["operational_note"] = nested
    with pytest.raises(GovernanceError) as caught:
        validate_manifest_payload(SHADOW_THRESHOLD_KIND, payload)
    assert caught.value.code == "secret_value_in_manifest"


@settings(max_examples=100, deadline=None)
@given(code_transition=st.sampled_from(((CodeState.NOT_STARTED, CodeState.IN_PROGRESS), (CodeState.IN_PROGRESS, CodeState.CODE_COMPLETE), (CodeState.CODE_COMPLETE, CodeState.IN_PROGRESS))), environment=st.sampled_from(tuple(EnvironmentAcceptanceState)), activation=st.sampled_from(tuple(ProductionActivationState)))
def test_property_44_code_environment_and_activation_states_remain_independent(code_transition, environment, activation):
    """Property 44: Code, environment, and activation states remain independent."""
    before = (code_transition[0], environment, activation)
    transition_code_state(*code_transition)
    after = (code_transition[1], environment, activation)
    assert before[1:] == after[1:]
    if environment is not EnvironmentAcceptanceState.ACCEPTED:
        assert apply_layer1_environment_result(environment) is environment
    else:
        with pytest.raises(GovernanceError) as caught:
            apply_layer1_environment_result(environment)
        assert caught.value.code == "layer1_cannot_accept"


@settings(max_examples=100, deadline=None)
@given(non_gated_ok=st.lists(st.booleans(), min_size=3, max_size=3), gated_ok=st.lists(st.booleans(), min_size=3, max_size=3))
def test_property_47_spec_completion_is_exactly_the_acceptance_conjunction(non_gated_ok, gated_ok):
    """Property 47: Spec completion is exactly the acceptance conjunction."""
    critical = _NON_MIGRATION[:2]
    manifest = _scope(
        {
            critical[0]: (ReleaseInclusion.INCLUDED, CompletionImpact.COMPLETION_CRITICAL),
            critical[1]: (ReleaseInclusion.INCLUDED, CompletionImpact.COMPLETION_CRITICAL),
            _NON_MIGRATION[2]: (ReleaseInclusion.INCLUDED, CompletionImpact.ACTIVATION_ONLY),
        }
    )
    non_gated = {
        f"work-{index}": (
            EnvironmentAcceptanceState.ACCEPTED if passed else EnvironmentAcceptanceState.BLOCKED
        )
        for index, passed in enumerate(non_gated_ok)
    }
    required_gated = manifest.completion_critical_operations()
    operation_states = {
        operation_id: (
            OperationExecutionState.ACCEPTED
            if gated_ok[index % len(gated_ok)]
            else OperationExecutionState.NOT_AUTHORIZED
        )
        for index, operation_id in enumerate(required_gated)
    }
    operation_states[_NON_MIGRATION[2]] = OperationExecutionState.FAILED
    decision = evaluate_spec_completion(non_gated, operation_states, manifest)
    expected = all(non_gated_ok) and all(
        operation_states[item] is OperationExecutionState.ACCEPTED
        for item in required_gated
    )
    assert decision.is_complete is expected
    assert (decision.state is SpecCompletionState.COMPLETE) is expected
    assert set(decision.required_work_set) == set(non_gated) | set(required_gated)


@settings(max_examples=100, deadline=None)
@given(fault=st.one_of(st.none(), st.tuples(st.sampled_from(_FLAG_IDS), st.sampled_from(("missing_mode", "wrong_mode", "missing_activation", "wrong_activation", "extra")))))
def test_property_48_production_ready_requires_all_six_active_and_enforce(fault):
    """Property 48: Production-ready requires all six ACTIVE and ENFORCE states."""
    modes = {flag_id: FeatureFlagRuntimeMode.ENFORCE for flag_id in _FLAG_IDS}
    activations = {flag_id: ProductionActivationState.ACTIVE for flag_id in _FLAG_IDS}
    if fault is not None:
        flag_id, kind = fault
        if kind == "missing_mode":
            modes.pop(flag_id)
        elif kind == "wrong_mode":
            modes[flag_id] = FeatureFlagRuntimeMode.SHADOW
        elif kind == "missing_activation":
            activations.pop(flag_id)
        elif kind == "wrong_activation":
            activations[flag_id] = ProductionActivationState.ROLLED_BACK
        else:
            modes["unregistered"] = FeatureFlagRuntimeMode.ENFORCE
            activations["unregistered"] = ProductionActivationState.ACTIVE
    decision = evaluate_production_readiness(modes, activations)
    assert decision.ready is (fault is None)
    assert (decision.blockers == ()) is (fault is None)


@settings(max_examples=100, deadline=None)
@given(unknowns=st.sets(_SAFE_TOKEN, max_size=10), clean_forward_n=st.integers(min_value=0, max_value=10000), reverse=st.booleans())
def test_property_49_final_evidence_report_is_complete_and_deterministic(unknowns, clean_forward_n, reverse):
    """Property 49: Final evidence report is complete and deterministic."""
    manifest = _scope()
    operation_items = []
    for operation_id in _REGISTERED:
        state = (
            OperationExecutionState.ACCEPTED
            if operation_id == MIGRATION_APPLY_OPERATION_ID
            else OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED
        )
        operation_items.append((operation_id, state))
    if reverse:
        operation_items.reverse()
    modes = dict(reversed([(flag_id, FeatureFlagRuntimeMode.SHADOW) for flag_id in _FLAG_IDS]))
    activations = dict(
        reversed([(flag_id, ProductionActivationState.AUTHORIZED_NOT_ACTIVE) for flag_id in _FLAG_IDS])
    )
    snapshot = AppendOnlyDataSnapshot.capture({"audit": {"event-1": "hash-1"}})
    history = HistoryConservation.compare(snapshot, snapshot)
    kwargs = dict(
        scope_manifest=manifest,
        scope_manifest_identity="release-scope:sha256:" + "4" * 64,
        clean_forward_n=clean_forward_n,
        known_unknowns=tuple(reversed(sorted(unknowns))),
        operation_states=dict(operation_items),
        flag_runtime_modes=modes,
        activation_states=activations,
        history_conservation=history,
    )
    first = build_final_evidence_report(**kwargs)
    second = build_final_evidence_report(**{**kwargs, "known_unknowns": tuple(sorted(unknowns))})
    assert first.to_canonical_json() == second.to_canonical_json()
    assert set(first.payload) == {
        "schema_version", "scope", "provenance_clean_true_forward", "known_unknowns",
        "operations", "excluded_terminal_operations", "feature_flags", "unactivated_flags",
        "production_readiness", "history_conservation", "deferred_initiatives",
    }
    assert len(first.payload["feature_flags"]) == 6
    assert len(first.payload["excluded_terminal_operations"]) == len(_NON_MIGRATION)
    assert first.payload["provenance_clean_true_forward"]["positive_expectation_claim"] == "NOT_CLAIMED"


@settings(max_examples=100, deadline=None)
@given(mismatch=st.sampled_from(("none", "schema", "hash", "environment", "revision", "command", "attempt", "assertion", "uri", "required_field")), pre_execution=st.booleans())
def test_property_52_layer2_evidence_matches_every_preregistered_binding(mismatch, pre_execution):
    """Property 52: Layer 2 evidence matches every preregistered binding."""
    with TemporaryDirectory(prefix="s9-property-52-") as root:
        store = GovernanceObjectStore(root)
        manifest = store.put(
            kind=LAYER_2_EVIDENCE_KIND,
            payload=_layer2_payload(),
            created_at=_NOW - timedelta(hours=1),
            created_by="property-author",
            code_revision=_REVISION,
        )
        evidence_payload = {"result": "PASS", "attempt_identity": "attempt-52"}
        if mismatch == "required_field":
            evidence_payload.pop("result")
        evidence = store.put(
            kind="layer2_result",
            payload=evidence_payload,
            created_at=_NOW,
            created_by="layer2-adapter",
            code_revision=_REVISION,
        )
        uri = store.evidence_uri_for(evidence.object_id)
        claim = Layer2EvidenceClaim(
            contract_id="result-contract-v1",
            evidence_uri=uri,
            schema_uri="schema://layer2/result/v1",
            content_sha256=evidence.payload_sha256,
            environment_key="real-env-v1",
            code_revision=_REVISION,
            command_id="acceptance-command-v1",
            attempt_identity="attempt-52",
            assertion_results=(("exit-zero", True), ("schema-valid", True)),
        )
        replacements = {
            "schema": {"schema_uri": "schema://wrong/v1"},
            "hash": {"content_sha256": "0" * 64},
            "environment": {"environment_key": "other-env"},
            "revision": {"code_revision": "b" * 40},
            "command": {"command_id": "other-command"},
            "attempt": {"attempt_identity": "other-attempt"},
            "assertion": {"assertion_results": (("exit-zero", True), ("schema-valid", False))},
            "uri": {"evidence_uri": "evidence://sha256/" + "f" * 64},
        }
        if mismatch in replacements:
            claim = replace(claim, **replacements[mismatch])
        result = admit_acceptance_attempt(
            manifest=manifest,
            context=AdmissionContext(
                trigger_point_at=_NOW + timedelta(minutes=1),
                applies_to_code_revision=_REVISION,
                environment_key="real-env-v1",
                command_id="acceptance-command-v1",
                attempt_identity="attempt-52",
                evidence_uris=(uri,),
                artifact_claims=(claim,),
            ),
            store=store,
            pre_execution=pre_execution,
        )
        if mismatch == "none":
            assert result.verdict is AdmissionVerdict.ADMITTED
            assert result.mismatches == ()
        else:
            assert result.verdict is (
                AdmissionVerdict.BLOCKED if pre_execution else AdmissionVerdict.FAILED
            )
            assert result.mismatches


# Task 9.28 examples: constructed failures and state/report edge cases.


def test_9_28_scope_excluded_included_activation_and_completion_dispositions():
    excluded, activation, critical = _NON_MIGRATION[:3]
    manifest = _scope(
        {
            activation: (ReleaseInclusion.INCLUDED, CompletionImpact.ACTIVATION_ONLY),
            critical: (ReleaseInclusion.INCLUDED, CompletionImpact.COMPLETION_CRITICAL),
        }
    )
    assert manifest.entry_for(excluded).inclusion is ReleaseInclusion.EXCLUDED
    assert manifest.entry_for(activation).completion_impact is CompletionImpact.ACTIVATION_ONLY
    assert critical in manifest.completion_critical_operations()
    assert activation not in manifest.completion_critical_operations()
    assert excluded not in manifest.completion_critical_operations()


def test_9_28_bad_approval_is_not_authorized():
    expected = ApprovalBinding(
        MIGRATION_APPLY_OPERATION_ID, "attempt-1", "env-test", "plan-v1", "artifact-v1", "actor-1"
    )
    wrong = replace(expected, plan_hash="plan-v2")
    result = validate_separate_write_approval(_approval(wrong), expected=expected, at=_NOW)
    assert result.verdict is ApprovalVerdict.MISMATCH
    assert result.mismatches == ("plan_hash_mismatch",)


def test_9_28_destructive_migration_is_rejected_before_execution():
    with TemporaryDirectory(prefix="s9-example-destructive-") as root:
        store, request, gates = _migration_contract(root, operation_kind="drop_table")
        with pytest.raises(GovernanceError) as caught:
            validate_migration_pre_execution_gates(request=request, gates=gates, store=store)
        assert caught.value.code == MIGRATION_NOT_ADDITIVE


@pytest.mark.parametrize("gate", ("pre", "post", "noop"))
def test_9_28_pre_post_and_noop_failure_examples(gate):
    with TemporaryDirectory(prefix="s9-example-gates-") as root:
        store, request, gates = _migration_contract(root)
        if gate == "pre":
            bad = replace(gates, preconditions=(GateAssertion("ddl.operation.1", False),))
            with pytest.raises(GovernanceError):
                validate_migration_pre_execution_gates(request=request, gates=bad, store=store)
            return
        validate_migration_pre_execution_gates(request=request, gates=gates, store=store)
        evidence = _postchecks(request)
        if gate == "post":
            evidence = replace(evidence, postconditions=(GateAssertion("ddl.operation.1", False),))
        else:
            evidence = replace(
                evidence, second_run_no_op_checks=(GateAssertion("ddl.operation.1", False),)
            )
        with pytest.raises(GovernanceError):
            validate_migration_post_execution_gates(
                authorization=_authorized_migration(request, gates.plan), evidence=evidence
            )


def test_9_28_task_1_3_stale_binding_returns_in_progress():
    bound = _task13_binding(b"bound")
    current = _task13_binding(b"current")
    assert bound.verify(current) is RevisionBindingStatus.STALE
    decision = evaluate_task_1_3_completion(
        bound_revision=bound,
        current_revision=current,
        layer1_passed=True,
        correctness_proofs_passed=True,
    )
    assert decision.code_state == "IN_PROGRESS"
    assert decision.environment_state == "BLOCKED"
    assert decision.reason_codes == ("REVISION_BINDING_STALE",)


def test_9_28_six_flag_mismatch_blocks_production_ready():
    modes = {flag_id: "ENFORCE" for flag_id in _FLAG_IDS}
    activations = {flag_id: "ACTIVE" for flag_id in _FLAG_IDS}
    modes[_FLAG_IDS[0]] = "AUDIT"
    decision = evaluate_production_readiness(modes, activations)
    assert decision.ready is False
    assert any(item.startswith("RUNTIME_MODE_NOT_ENFORCE") for item in decision.blockers)


def test_9_28_final_report_schema_example():
    manifest = _scope()
    operation_states = {
        operation_id: (
            "ACCEPTED"
            if operation_id == MIGRATION_APPLY_OPERATION_ID
            else "NOT_AUTHORIZED_NOT_EXECUTED"
        )
        for operation_id in _REGISTERED
    }
    snapshot = AppendOnlyDataSnapshot.capture({"audit": {"event-1": "fingerprint-1"}})
    report = build_final_evidence_report(
        scope_manifest=manifest,
        scope_manifest_identity="release-scope:sha256:" + "9" * 64,
        clean_forward_n=0,
        known_unknowns=("REAL_ENVIRONMENT_PENDING",),
        operation_states=operation_states,
        flag_runtime_modes={flag_id: "SHADOW" for flag_id in _FLAG_IDS},
        activation_states={flag_id: "AUTHORIZED_NOT_ACTIVE" for flag_id in _FLAG_IDS},
        history_conservation=HistoryConservation.compare(snapshot, snapshot),
    )
    assert report.payload["schema_version"] == 1
    assert report.payload["scope"]["release_scope_manifest_identity"].startswith(
        "release-scope:sha256:"
    )
    assert report.payload["production_readiness"]["production_ready"] is False
    assert report.payload["history_conservation"]["preserved"] is True
    assert report.payload["provenance_clean_true_forward"] == {
        "n": 0,
        "positive_expectation_claim": "NOT_CLAIMED",
        "status": "UNVERIFIED_CURRENT_POLICY",
    }

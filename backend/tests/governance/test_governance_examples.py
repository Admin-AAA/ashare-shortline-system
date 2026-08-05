"""Example-driven tests for governance schema, scope, registry, and manifest rules.

Each test encodes one concrete specimen from spec task 0.20: baseline
timestamp ordering, scope manifest membership rules, registry tamper
detection, manifest supersede chains, admission drift verdicts, stale
equivalence assessments, and secret-value refusal.  All stores are tmp_path
sandboxes with constructed inputs; nothing here touches MySQL, the network,
a real governance store, or any brokerage probe.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from app.governance import registries
from app.governance.binding import (
    DependencyDigest,
    DependencyDigestKind,
    RevisionBinding,
)
from app.governance.envelope import GovernanceError, ImmutableEnvelope
from app.governance.freeze import _digest, audit_baseline_artifact
from app.governance.manifests import (
    ACCEPTANCE_COMMAND_KIND,
    LAYER_2_EVIDENCE_KIND,
    SHADOW_THRESHOLD_KIND,
    AdmissionContext,
    AdmissionVerdict,
    admit_acceptance_attempt,
    validate_manifest_payload,
)
from app.governance.obligations import (
    EquivalenceAssessment,
    EquivalenceConclusion,
    ProofRouteStatus,
    evaluate_proof_route,
    invalidate_assessment_on_semantic_change,
    obligation_for_property,
)
from app.governance.registries import (
    FEATURE_FLAG_REGISTRY,
    MIGRATION_APPLY_OPERATION_ID,
    OPERATION_REGISTRY,
)
from app.governance.scope import (
    CompletionImpact,
    ReleaseInclusion,
    ReleaseScopeEntry,
    ReleaseScopeManifest,
)
from app.governance.store import GovernanceObjectStore

_REV = "a" * 40
_NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
_TRIGGER = datetime(2026, 8, 3, 13, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Constructed input builders
# ---------------------------------------------------------------------------


def _write_baseline(tmp_path, captured_at="2026-08-03T12:17:00+00:00"):
    document = {
        "schema_version": 1,
        "task": "0.1 capture baseline",
        "captured_at": captured_at,
        "repository": {
            "git_revision": _REV,
            "branch": "main",
            "worktree_status_digest": "status-digest-1",
        },
        "runs": [{"run_id": "run-1"}],
    }
    document["section_digests"] = {"runs": _digest(document["runs"])}
    path = tmp_path / "baseline-task-0.1.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _audit(path, **overrides):
    kwargs = {
        "expected_task_prefix": "0.1",
        "revision_exists": lambda revision: True,
        "commits_between": lambda start, end: ("commit-1",),
        "current_revision": "b" * 40,
        "current_branch": "main",
        "current_status_digest": "status-digest-1",
        "first_new_behavior_change_at": None,
        "delta_note": "example test",
    }
    kwargs.update(overrides)
    return audit_baseline_artifact(path, **kwargs)


def _scope_entries():
    entries = []
    for operation_id, _kind in OPERATION_REGISTRY:
        if operation_id == MIGRATION_APPLY_OPERATION_ID:
            inclusion, impact = ReleaseInclusion.INCLUDED, CompletionImpact.COMPLETION_CRITICAL
        else:
            inclusion, impact = ReleaseInclusion.EXCLUDED, CompletionImpact.NON_CRITICAL
        entries.append(
            ReleaseScopeEntry(
                operation_id=operation_id,
                inclusion=inclusion,
                completion_impact=impact,
                target_environment_key="env-test",
                operation_plan_key=f"plan.{operation_id}",
            )
        )
    return tuple(entries)


def _scope_manifest(entries):
    return ReleaseScopeManifest(
        registry_snapshot_id="registry-snapshot-test",
        entries=entries,
        behavior_change_barrier_event_id="barrier-event-test",
    )


def _acceptance_command_payload():
    return {
        "manifest_kind": ACCEPTANCE_COMMAND_KIND,
        "stable_manifest_key": "acceptance-command-v1",
        "registry_snapshot_id": "registry-snapshot-test",
        "frozen_at": "2026-08-03T10:00:00Z",
        "applies_to_code_revision": _REV,
        "commands": [
            {
                "command_id": "pytest-governance",
                "executable_entrypoint": "pytest",
                "argument_vector": ["-q", "tests/governance"],
                "expected_exit_codes": [0],
                "timeout_seconds": 600,
                "code_checksum": "checksum-v1",
                "assertion_ids": ["assert-exit-zero"],
            }
        ],
    }


def _layer_2_payload():
    return {
        "manifest_kind": LAYER_2_EVIDENCE_KIND,
        "stable_manifest_key": "l2-evidence-v1",
        "registry_snapshot_id": "registry-snapshot-test",
        "frozen_at": "2026-08-03T10:00:00Z",
        "applies_to_code_revision": _REV,
        "layer_2_evidence_set_id": "L2_FLAG_TEST_V1",
        "real_environment_key": "env-test",
        "acceptance_command_ids": ["pytest-governance"],
        "artifact_contracts": [
            {
                "contract_id": "contract-1",
                "schema_uri": "schema://test/v1",
                "evidence_uri_allowlist": ["evidence://sha256/"],
                "required_fields": ["result"],
            }
        ],
        "assertions": [
            {
                "assertion_id": "assert-exit-zero",
                "required": True,
                "predicate": "exit_code_in_expected",
            }
        ],
        "pass_rule": "ALL_REQUIRED_ASSERTIONS_PASS",
    }


def _shadow_threshold_payload():
    return {
        "manifest_kind": SHADOW_THRESHOLD_KIND,
        "stable_manifest_key": "shadow-threshold-v1",
        "registry_snapshot_id": "registry-snapshot-test",
        "frozen_at": "2026-08-03T10:00:00Z",
        "applies_to_code_revision": _REV,
        "target_operation_id": "projection_cutover.production_read_v1",
        "source_projection_id": "projection-current",
        "candidate_projection_id": "projection-candidate",
        "rules": [
            {
                "metric_id": "pl_ratio",
                "comparator": "<=",
                "threshold_decimal": 0.001,
                "minimum_sample": 100,
                "failure_action": "BLOCK_CUTOVER",
                "identity_dimensions": ["trade_date"],
            }
        ],
    }


def _admission_context(**overrides):
    base = {
        "trigger_point_at": _TRIGGER,
        "applies_to_code_revision": _REV,
        "environment_key": "env-test",
        "command_id": "pytest-governance",
    }
    base.update(overrides)
    return AdmissionContext(**base)


# ---------------------------------------------------------------------------
# Baseline artifact timestamp ordering
# ---------------------------------------------------------------------------


class TestBaselineTimestamps:
    def test_reversed_timestamps_make_order_unprovable(self, tmp_path):
        path = _write_baseline(tmp_path, captured_at="2026-08-03T13:00:00+00:00")
        result, _document = _audit(
            path,
            first_new_behavior_change_at=datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert result.freeze_order_provable is False
        assert result.reusable_as_canonical_baseline is False

    def test_ordered_timestamps_keep_order_provable(self, tmp_path):
        path = _write_baseline(tmp_path, captured_at="2026-08-03T12:17:00+00:00")
        result, _document = _audit(
            path,
            first_new_behavior_change_at=datetime(2026, 8, 3, 13, 0, 0, tzinfo=timezone.utc),
        )
        assert result.freeze_order_provable is True
        assert result.reusable_as_canonical_baseline is True

    @pytest.mark.parametrize("captured_at", ["", "not-a-timestamp"])
    def test_missing_or_malformed_captured_at_rejected(self, tmp_path, captured_at):
        path = _write_baseline(tmp_path, captured_at=captured_at)
        with pytest.raises(GovernanceError) as caught:
            _audit(path)
        assert caught.value.code == "invalid_timestamp"


# ---------------------------------------------------------------------------
# ReleaseScopeManifest membership rules
# ---------------------------------------------------------------------------


class TestReleaseScopeRules:
    def test_unknown_operation_id_rejected(self):
        with pytest.raises(GovernanceError) as caught:
            ReleaseScopeEntry(
                operation_id="migration_apply.bogus",
                inclusion=ReleaseInclusion.INCLUDED,
                completion_impact=CompletionImpact.COMPLETION_CRITICAL,
                target_environment_key="env-test",
                operation_plan_key="plan.bogus",
            )
        assert caught.value.code == "unknown_operation_id"

    def test_unknown_dependency_operation_id_rejected(self):
        with pytest.raises(GovernanceError) as caught:
            ReleaseScopeEntry(
                operation_id="enforcement.release_gate",
                inclusion=ReleaseInclusion.INCLUDED,
                completion_impact=CompletionImpact.NON_CRITICAL,
                target_environment_key="env-test",
                operation_plan_key="plan.release_gate",
                dependency_operation_ids=("enforcement.bogus",),
            )
        assert caught.value.code == "unknown_operation_id"

    def test_missing_operation_rejected(self):
        entries = tuple(
            entry
            for entry in _scope_entries()
            if entry.operation_id != "enforcement.release_gate"
        )
        with pytest.raises(GovernanceError) as caught:
            _scope_manifest(entries)
        assert caught.value.code == "scope_manifest_incomplete"

    def test_duplicate_operation_rejected(self):
        entries = _scope_entries()
        duplicated = entries + (entries[0],)
        with pytest.raises(GovernanceError) as caught:
            _scope_manifest(duplicated)
        assert caught.value.code == "duplicate_scope_entry"

    def test_excluded_completion_critical_rejected(self):
        with pytest.raises(GovernanceError) as caught:
            ReleaseScopeEntry(
                operation_id="enforcement.release_gate",
                inclusion=ReleaseInclusion.EXCLUDED,
                completion_impact=CompletionImpact.COMPLETION_CRITICAL,
                target_environment_key="env-test",
                operation_plan_key="plan.release_gate",
            )
        assert caught.value.code == "excluded_completion_critical"

    @pytest.mark.parametrize(
        "inclusion,impact",
        [
            (ReleaseInclusion.EXCLUDED, CompletionImpact.NON_CRITICAL),
            (ReleaseInclusion.INCLUDED, CompletionImpact.NON_CRITICAL),
            (ReleaseInclusion.INCLUDED, CompletionImpact.ACTIVATION_ONLY),
        ],
    )
    def test_migration_must_be_included_completion_critical(self, inclusion, impact):
        with pytest.raises(GovernanceError) as caught:
            ReleaseScopeEntry(
                operation_id=MIGRATION_APPLY_OPERATION_ID,
                inclusion=inclusion,
                completion_impact=impact,
                target_environment_key="env-test",
                operation_plan_key="plan.migration",
            )
        assert caught.value.code == "migration_scope_forced"


# ---------------------------------------------------------------------------
# Feature flag registry tamper detection
# ---------------------------------------------------------------------------


class TestFeatureFlagRegistryTamper:
    def test_frozen_registry_passes_validation(self):
        registries.validate_feature_flag_registry()

    def test_non_six_row_registry_rejected(self, monkeypatch):
        monkeypatch.setattr(registries, "FEATURE_FLAG_REGISTRY", FEATURE_FLAG_REGISTRY[:5])
        with pytest.raises(GovernanceError) as caught:
            registries.validate_feature_flag_registry()
        assert caught.value.code == "flag_registry_invalid"

    def test_duplicate_layer_2_evidence_set_rejected(self, monkeypatch):
        tampered_row = dataclasses.replace(
            FEATURE_FLAG_REGISTRY[1],
            layer_2_evidence_set_id=FEATURE_FLAG_REGISTRY[0].layer_2_evidence_set_id,
        )
        tampered = (FEATURE_FLAG_REGISTRY[0], tampered_row, *FEATURE_FLAG_REGISTRY[2:])
        monkeypatch.setattr(registries, "FEATURE_FLAG_REGISTRY", tampered)
        with pytest.raises(GovernanceError) as caught:
            registries.validate_feature_flag_registry()
        assert caught.value.code == "flag_registry_invalid"

    def test_unknown_cutover_dependency_rejected(self, monkeypatch):
        tampered_row = dataclasses.replace(
            FEATURE_FLAG_REGISTRY[0],
            cutover_dependency="operation.bogus",
        )
        tampered = (tampered_row, *FEATURE_FLAG_REGISTRY[1:])
        monkeypatch.setattr(registries, "FEATURE_FLAG_REGISTRY", tampered)
        with pytest.raises(GovernanceError) as caught:
            registries.validate_feature_flag_registry()
        assert caught.value.code == "unknown_operation_id"


# ---------------------------------------------------------------------------
# Manifest supersede chains
# ---------------------------------------------------------------------------


class TestManifestSupersedes:
    def test_supersede_chain_keeps_old_manifest_resolvable(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        v1 = store.put(
            kind=ACCEPTANCE_COMMAND_KIND,
            payload=_acceptance_command_payload(),
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_REV,
        )
        v2_payload = _acceptance_command_payload()
        v2_payload["stable_manifest_key"] = "acceptance-command-v2"
        v2_payload["frozen_at"] = "2026-08-03T11:00:00Z"
        v2_payload["supersedes_manifest_id"] = v1.object_id
        v2 = store.put(
            kind=ACCEPTANCE_COMMAND_KIND,
            payload=v2_payload,
            created_at=_TRIGGER,
            created_by="test-actor",
            code_revision=_REV,
            supersedes_id=v1.object_id,
        )

        chain = store.supersede_chain(v2.object_id)
        assert [item.object_id for item in chain] == [v2.object_id, v1.object_id]

        reloaded_v1 = store.get(v1.object_id)
        assert reloaded_v1.to_document() == v1.to_document()
        validate_manifest_payload(ACCEPTANCE_COMMAND_KIND, reloaded_v1.payload)
        validate_manifest_payload(ACCEPTANCE_COMMAND_KIND, store.get(v2.object_id).payload)


# ---------------------------------------------------------------------------
# Admission pipeline drift verdicts
# ---------------------------------------------------------------------------


@pytest.fixture
def layer_2_manifest(tmp_path):
    store = GovernanceObjectStore(tmp_path)
    envelope = store.put(
        kind=LAYER_2_EVIDENCE_KIND,
        payload=_layer_2_payload(),
        created_at=_NOW,
        created_by="test-actor",
        code_revision=_REV,
    )
    return store, envelope


class TestAdmissionDrift:
    def test_matching_attempt_is_admitted(self, layer_2_manifest):
        store, manifest = layer_2_manifest
        evidence = store.put(
            kind="evidence",
            payload={"result": "PASS"},
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_REV,
        )
        result = admit_acceptance_attempt(
            manifest=manifest,
            context=_admission_context(
                evidence_uris=(store.evidence_uri_for(evidence.object_id),),
            ),
            store=store,
            pre_execution=True,
        )
        assert result.verdict is AdmissionVerdict.ADMITTED
        assert result.mismatches == ()

    def test_unresolvable_evidence_uri_blocked(self, layer_2_manifest):
        store, manifest = layer_2_manifest
        result = admit_acceptance_attempt(
            manifest=manifest,
            context=_admission_context(
                evidence_uris=("evidence://sha256/" + "f" * 64,),
            ),
            store=store,
            pre_execution=True,
        )
        assert result.verdict is AdmissionVerdict.BLOCKED
        assert result.mismatches == ("evidence_uri_rejected:evidence_uri_unresolvable",)

    def test_tampered_evidence_hash_fails_post_execution(self, layer_2_manifest):
        store, manifest = layer_2_manifest
        evidence = store.put(
            kind="evidence",
            payload={"result": "PASS"},
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_REV,
        )
        uri = store.evidence_uri_for(evidence.object_id)
        path = store.root / "evidence" / f"{evidence.object_id.rsplit(':', 1)[-1]}.json"
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["payload"]["result"] = "FAIL"
        path.write_text(json.dumps(tampered), encoding="utf-8")

        result = admit_acceptance_attempt(
            manifest=manifest,
            context=_admission_context(evidence_uris=(uri,)),
            store=store,
            pre_execution=False,
        )
        assert result.verdict is AdmissionVerdict.FAILED
        assert len(result.mismatches) == 1
        assert result.mismatches[0].startswith("evidence_uri_rejected:")

    def test_manifest_kind_schema_mismatch_refused(self, layer_2_manifest):
        _store, manifest = layer_2_manifest
        wrong_kind_payload = _layer_2_payload()
        wrong_kind_payload["manifest_kind"] = SHADOW_THRESHOLD_KIND
        forged = ImmutableEnvelope(
            kind=LAYER_2_EVIDENCE_KIND,
            payload=wrong_kind_payload,
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_REV,
        )
        assert forged.object_id != manifest.object_id
        with pytest.raises(GovernanceError) as caught:
            admit_acceptance_attempt(
                manifest=forged,
                context=_admission_context(),
                store=_store,
                pre_execution=True,
            )
        assert caught.value.code == "manifest_kind_mismatch"

    def test_unregistered_command_blocked(self, layer_2_manifest):
        store, manifest = layer_2_manifest
        result = admit_acceptance_attempt(
            manifest=manifest,
            context=_admission_context(command_id="unknown-command"),
            store=store,
            pre_execution=True,
        )
        assert result.verdict is AdmissionVerdict.BLOCKED
        assert result.mismatches == ("command_not_registered",)

    def test_environment_mismatch_fails_post_execution(self, layer_2_manifest):
        store, manifest = layer_2_manifest
        result = admit_acceptance_attempt(
            manifest=manifest,
            context=_admission_context(environment_key="env-other"),
            store=store,
            pre_execution=False,
        )
        assert result.verdict is AdmissionVerdict.FAILED
        assert result.mismatches == ("environment_key_mismatch",)


# ---------------------------------------------------------------------------
# Stale equivalence assessment proof routes
# ---------------------------------------------------------------------------


class TestStaleEquivalenceAssessment:
    def _binding(self, obligation):
        return RevisionBinding(
            scope_id=obligation.revision_binding_scope,
            digests=(
                DependencyDigest(
                    kind=DependencyDigestKind.REPLAY,
                    locator="replay.py",
                    content_sha256="a" * 64,
                ),
            ),
        )

    def _assessment(self, binding):
        return EquivalenceAssessment(
            evidence_uri="evidence://sha256/" + "1" * 64,
            domain_same_or_stronger=True,
            oracle_equivalent=True,
            result="PASS",
            conclusion=EquivalenceConclusion.VALID,
            assessed_at=_NOW,
            revision_binding_hash=binding.binding_hash,
            semantic_fingerprint="fp-v1",
        )

    def test_valid_assessment_proves_route(self):
        obligation = obligation_for_property(1)
        binding = self._binding(obligation)
        status, reasons = evaluate_proof_route(
            obligation,
            binding=binding,
            equivalence_assessments=(self._assessment(binding),),
        )
        assert status is ProofRouteStatus.PROVEN
        assert reasons == ()

    def test_stale_assessment_leaves_obligation_unproven(self):
        obligation = obligation_for_property(1)
        binding = self._binding(obligation)
        stale = invalidate_assessment_on_semantic_change(
            self._assessment(binding),
            current_semantic_fingerprint="fp-v2",
        )
        assert stale.conclusion is EquivalenceConclusion.STALE

        status, reasons = evaluate_proof_route(
            obligation,
            binding=binding,
            equivalence_assessments=(stale,),
        )
        assert status is ProofRouteStatus.UNPROVEN
        assert "equivalent:assessment_not_valid" in reasons


# ---------------------------------------------------------------------------
# Secret values are refused anywhere in a manifest payload
# ---------------------------------------------------------------------------


def _shadow_payload_with_secret(location):
    payload = _shadow_threshold_payload()
    if location == "root_field":
        payload["notes"] = "Bearer abcdef1234567890.token"
    elif location == "list_item":
        payload["rules"][0]["identity_dimensions"] = ["trade_date", "password=hunter2"]
    elif location == "nested_object":
        payload["rules"][0]["connection"] = "mysql://reader:s3cretpw@db.internal:3306/ashare"
    return payload


class TestSecretValuesRefused:
    @pytest.mark.parametrize("location", ["root_field", "list_item", "nested_object"])
    def test_secret_value_anywhere_in_payload_rejected(self, location):
        with pytest.raises(GovernanceError) as caught:
            validate_manifest_payload(
                SHADOW_THRESHOLD_KIND,
                _shadow_payload_with_secret(location),
            )
        assert caught.value.code == "secret_value_in_manifest"

    def test_clean_shadow_threshold_payload_accepted(self):
        validate_manifest_payload(SHADOW_THRESHOLD_KIND, _shadow_threshold_payload())

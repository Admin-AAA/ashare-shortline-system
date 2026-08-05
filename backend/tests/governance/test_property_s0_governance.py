"""S0 property tests (spec tasks 0.11–0.19) for production-evidence-risk-hardening.

Nine hypothesis properties over the governance plane:

- **Property 1** (task 0.11): audit, reuse, and gap admission are total.
- **Property 2** (task 0.12): baseline freeze is ordered, immutable, and
  append-only verified.
- **Property 3** (task 0.13): traceability is complete and referentially valid.
- **Property 4** (task 0.14): the golden harness is deterministic.
- **Property 5** (task 0.15): safe initialization and unlock rules never imply
  completion.
- **Property 50** (task 0.16): deferred and brokerage scope is fail-closed.
- **Property 51** (task 0.17): preregistered manifests are immutable and
  temporally valid.
- **Property 53** (task 0.18): every correctness obligation has a successful
  current proof route.
- **Property 54** (task 0.19): equivalent correctness evidence invalidates on
  semantic change.

Every governance store is a throwaway ``tmp_path`` directory (one unique
subdirectory per hypothesis example); every timestamp, revision, and payload is
a constructed input.  These tests never touch a real MySQL, the network, or the
real governance store (``ASHARE_GOVERNANCE_STORE_ROOT`` is never read here).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import string
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.governance import state as _state_module
from app.governance.binding import (
    DependencyDigest,
    DependencyDigestKind,
    RevisionBinding,
)
from app.governance.envelope import GovernanceError
from app.governance.events import GovernanceEventLog
from app.governance.freeze import (
    BASELINE_VERIFICATION_KIND,
    InventoryEntry,
    audit_baseline_artifact,
    freeze_s0,
)
from app.governance.manifests import (
    ACCEPTANCE_COMMAND_KIND,
    LAYER_2_EVIDENCE_KIND,
    PREREGISTERED_MANIFEST_KINDS,
    REAL_ENVIRONMENT_KIND,
    RESTORE_CAPACITY_KIND,
    SHADOW_THRESHOLD_KIND,
    AdmissionContext,
    AdmissionVerdict,
    CapacityMetric,
    Comparator,
    ShadowFailureAction,
    admit_acceptance_attempt,
    validate_manifest_payload,
)
from app.governance.obligations import (
    CorrectnessObligation,
    EquivalenceAssessment,
    EquivalenceConclusion,
    ProofRouteStatus,
    PropertyTestEvidence,
    evaluate_proof_route,
    invalidate_assessment_on_semantic_change,
    obligation_for_property,
    validate_obligation_registry,
)
from app.governance.registries import MIGRATION_APPLY_OPERATION_ID, operation_ids
from app.governance.scope import (
    CompletionImpact,
    ReleaseInclusion,
    ReleaseScopeEntry,
    ReleaseScopeManifest,
)
from app.governance.scope_guard import (
    ALLOWED_RISK_PORTFOLIO_KINDS,
    BROKERAGE_ACCESS_DENIED,
    DEFERRED_INITIATIVE_BLOCKED,
    HISTORICAL_RECONSTRUCTION_INITIATIVE,
    RISK_PORTFOLIO_KIND_DENIED,
    DeferredInitiative,
    ScopeViolation,
    assert_initiative_allowed,
    assert_no_brokerage_access,
    assert_risk_portfolio_kind,
)
from app.governance.state import (
    CodeState,
    EnvironmentAcceptanceState,
    OperationExecutionState,
    ProductionActivationState,
    apply_layer1_environment_result,
    excluded_operation_terminal_state,
    transition_activation_state,
    transition_code_state,
    transition_environment_state,
    transition_included_operation_state,
)
from app.governance.store import EVIDENCE_URI_SCHEME, GovernanceObjectStore
from testkit import (
    EvidenceEligibilityAdapter,
    MinCapRiskDecisionAdapter,
    OutcomeIdentityAdapter,
    TPlusOneExecutionAdapter,
    external_network_disabled,
    load_golden_fixture,
    load_projection_golden_cases,
)


_ACTOR = "property-test"
_CODE_REVISION = "f" * 40
_NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "production_hardening_golden_v1.json"
)

# tmp_path is function-scoped and therefore shared by all examples of one test;
# the store root is re-isolated per example with a drawn UUID instead.
_FIXTURE_HEALTH = [HealthCheck.function_scoped_fixture]

_SAFE_KEY = st.from_regex(r"[a-z][a-z0-9_]{0,11}", fullmatch=True)
_HEX64 = st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)
_REVISION = st.text(alphabet="0123456789abcdef", min_size=7, max_size=40)
_AWARE_DATETIMES = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(timezone.utc),
)
_JSON_SCALAR = st.one_of(
    st.text(max_size=12),
    st.integers(-1000, 1000),
    st.booleans(),
    st.none(),
    st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
)
_JSON_VALUES = st.recursive(
    _JSON_SCALAR,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4),
    ),
    max_leaves=6,
)
_REQUIREMENT_REF = st.builds(
    lambda major, minor: f"{major}.{minor}", st.integers(1, 13), st.integers(1, 15)
)


def _fresh_store_root(tmp_path: Path, token: str) -> Path:
    """Per-example store root, wiped so hypothesis replays of the same example
    (which redraw the same token under the shared function-scoped tmp_path)
    always start from an empty store."""

    root = tmp_path / "store" / token
    shutil.rmtree(root, ignore_errors=True)
    return root


def _put(store: GovernanceObjectStore, *, kind: str, payload: Mapping[str, Any], **kwargs: Any):
    return store.put(
        kind=kind,
        payload=payload,
        created_at=kwargs.pop("created_at", _NOW),
        created_by=kwargs.pop("created_by", _ACTOR),
        code_revision=kwargs.pop("code_revision", _CODE_REVISION),
        **kwargs,
    )


def _object_path(store: GovernanceObjectStore, object_id: str) -> Path:
    kind, _, digest = object_id.rpartition(":sha256:")
    return store.root / kind / f"{digest}.json"


# ---------------------------------------------------------------------------
# Property 1 (task 0.11) — audit, reuse, and gap admission are total.
# ---------------------------------------------------------------------------

_WORKSPACE_STATUSES = ("CLEAN", "MODIFIED", "UNTRACKED", "STALE")
_LEGAL_DECISIONS = ("REUSE", "RECONCILE", "GAP")
_UNMAPPED_DECISIONS = ("NEW_IMPLEMENTATION", "UNMAPPED", "UNKNOWN", "reuse", "")


@st.composite
def _audited_artifact(draw: Any) -> dict[str, Any]:
    return {
        "path": draw(
            st.from_regex(r"src/[a-z][a-z0-9_/]{0,18}\.py", fullmatch=True)
        ),
        "workspace_status": draw(st.sampled_from(_WORKSPACE_STATUSES)),
        "content_sha256": draw(st.one_of(st.none(), _HEX64)),
        "linked_tests": draw(
            st.lists(
                st.from_regex(r"tests/[a-z][a-z0-9_/]{0,14}\.py", fullmatch=True),
                max_size=3,
            )
        ),
        "requirement_refs": draw(st.lists(_REQUIREMENT_REF, max_size=3)),
        "mapped": draw(st.booleans()),
        "decision": draw(st.sampled_from(_LEGAL_DECISIONS)),
        "unmapped_decision": draw(st.sampled_from(_UNMAPPED_DECISIONS)),
        "rationale": draw(
            st.sampled_from(
                (
                    "",
                    "   ",
                    "reproducible gap: restore drill evidence missing",
                    "reuse the existing baseline capture script",
                )
            )
        ),
        "gap_record_id": draw(st.one_of(st.none(), _SAFE_KEY)),
    }


@given(
    artifacts=st.lists(_audited_artifact(), unique_by=lambda item: item["path"], max_size=12),
    run_id=st.uuids(),
)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=_FIXTURE_HEALTH,
)
def test_property_1_audit_reuse_gap_admission_total(tmp_path, artifacts, run_id) -> None:
    """**Property 1: Audit, reuse, and gap admission are total**

    **Validates: Requirements 1.1, 1.7, 1.8**

    For any artifact set, every artifact yields exactly one audit outcome:
    either one ``InventoryEntry`` whose decision is REUSE/RECONCILE/GAP, or a
    fail-closed refusal.  A GAP without a reproducible rationale is refused; a
    new implementation with no audit mapping (no legal reuse decision) is
    refused.  The frozen inventory carries exactly one entry per admitted
    artifact.
    """

    store = GovernanceObjectStore(_fresh_store_root(tmp_path, run_id.hex))
    admitted: list[tuple[dict[str, Any], InventoryEntry]] = []
    processed = 0
    for artifact in artifacts:
        processed += 1
        if not artifact["mapped"]:
            # 无映射的新实现必拒（1.8）：不存在合法 Reuse_Decision 可记录。
            with pytest.raises(GovernanceError) as caught:
                InventoryEntry(
                    path=artifact["path"],
                    workspace_status=artifact["workspace_status"],
                    content_sha256=artifact["content_sha256"],
                    decision=artifact["unmapped_decision"],
                    rationale=artifact["rationale"],
                    linked_tests=tuple(artifact["linked_tests"]),
                    requirement_refs=tuple(artifact["requirement_refs"]),
                )
            assert caught.value.code == "invalid_reuse_decision"
            continue
        if artifact["decision"] == "GAP" and not artifact["rationale"].strip():
            # GAP 无可复现 rationale 必拒（1.7/1.8）。
            with pytest.raises(GovernanceError) as caught:
                InventoryEntry(
                    path=artifact["path"],
                    workspace_status=artifact["workspace_status"],
                    content_sha256=artifact["content_sha256"],
                    decision="GAP",
                    rationale=artifact["rationale"],
                    linked_tests=tuple(artifact["linked_tests"]),
                    requirement_refs=tuple(artifact["requirement_refs"]),
                    gap_record_id=artifact["gap_record_id"],
                )
            assert caught.value.code == "gap_record_required"
            continue
        entry = InventoryEntry(
            path=artifact["path"],
            workspace_status=artifact["workspace_status"],
            content_sha256=artifact["content_sha256"],
            decision=artifact["decision"],
            rationale=artifact["rationale"],
            linked_tests=tuple(artifact["linked_tests"]),
            requirement_refs=tuple(artifact["requirement_refs"]),
            gap_record_id=artifact["gap_record_id"],
        )
        assert entry.decision in _LEGAL_DECISIONS
        admitted.append((artifact, entry))

    # 完备性：每个 artifact 恰好得到一个审计结果（准入一条或拒绝一次）。
    assert processed == len(artifacts)
    admitted_paths = [artifact["path"] for artifact, _ in admitted]
    assert len(admitted_paths) == len(set(admitted_paths))

    # 冻结的 Audit_Inventory 恰好每个准入 artifact 一条且决策合法（1.1/1.7）。
    payload = {"entries": [entry.canonical_payload() for _, entry in admitted]}
    envelope = _put(store, kind="audit_inventory", payload=payload)
    loaded = store.get(envelope.object_id)
    loaded_entries = loaded.payload["entries"]
    assert [item["path"] for item in loaded_entries] == admitted_paths
    assert all(item["decision"] in _LEGAL_DECISIONS for item in loaded_entries)


# ---------------------------------------------------------------------------
# Property 2 (task 0.12) — baseline freeze ordered, immutable, append-only.
# ---------------------------------------------------------------------------

_BASELINE_SECTIONS = (
    "strategy_identity",
    "execution_identity",
    "active_strategies",
    "champion_identity",
    "projection_counts",
)


def _section_digest(value: Any) -> str:
    """Recompute a section digest with the capture scripts' byte-compatible
    canonical JSON algorithm (sorted keys, no whitespace, ``default=str``)."""

    blob = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@st.composite
def _baseline_scenario(draw: Any) -> dict[str, Any]:
    sections = draw(
        st.lists(st.sampled_from(_BASELINE_SECTIONS), min_size=1, max_size=4, unique=True)
    )
    captured_at = draw(_AWARE_DATETIMES)
    revision = draw(_REVISION)
    status_digest = draw(_HEX64)
    expected_prefix = draw(st.sampled_from(["task-0.1", "task-0.3"]))
    task = (
        f"{expected_prefix}-baseline-capture"
        if draw(st.booleans())
        else f"other-{draw(_SAFE_KEY)}"
    )
    schema_version = draw(st.sampled_from([1, 1, 0, 2, "1"]))
    document: dict[str, Any] = {
        "captured_at": captured_at.isoformat(),
        "schema_version": schema_version,
        "task": task,
        "repository": {
            "git_revision": revision,
            "branch": draw(st.sampled_from(["main", "develop", "feature/s0"])),
            "worktree_status_digest": status_digest,
        },
    }
    for section in sections:
        document[section] = draw(_JSON_VALUES)
    document["section_digests"] = {
        section: _section_digest(document[section]) for section in sections
    }
    return {
        "document": document,
        "sections": sections,
        "captured_at": captured_at,
        "tamper_section": draw(st.sampled_from(sections)),
        "expected_task_prefix": expected_prefix,
        "revision_exists": draw(st.booleans()),
        "current_revision": draw(st.one_of(st.just(revision), _REVISION)),
        "current_status_digest": draw(st.one_of(st.just(status_digest), _HEX64)),
        "first_new_behavior_change_at": draw(st.one_of(st.none(), _AWARE_DATETIMES)),
        "delta_note": draw(st.text(max_size=20)),
    }


def _audit_kwargs(scenario: Mapping[str, Any], *, force_reusable: bool = False) -> dict[str, Any]:
    document = scenario["document"]
    if force_reusable:
        return {
            "expected_task_prefix": str(document["task"]),
            "revision_exists": lambda _revision: True,
            "commits_between": lambda _old, _new: (),
            "current_revision": document["repository"]["git_revision"],
            "current_branch": document["repository"]["branch"],
            "current_status_digest": document["repository"]["worktree_status_digest"],
            "first_new_behavior_change_at": None,
            "delta_note": "forced reusable audit",
        }
    commits = ["c" * 40, "d" * 40]
    return {
        "expected_task_prefix": scenario["expected_task_prefix"],
        "revision_exists": lambda _revision: scenario["revision_exists"],
        "commits_between": lambda _old, _new: commits,
        "current_revision": scenario["current_revision"],
        "current_branch": document["repository"]["branch"],
        "current_status_digest": scenario["current_status_digest"],
        "first_new_behavior_change_at": scenario["first_new_behavior_change_at"],
        "delta_note": scenario["delta_note"],
    }


def _write_artifact(tmp_path: Path, name: str, document: Mapping[str, Any]) -> Path:
    directory = tmp_path / "artifacts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _scope_entries_fixed() -> tuple[ReleaseScopeEntry, ...]:
    entries = []
    for operation_id in sorted(operation_ids()):
        if operation_id == MIGRATION_APPLY_OPERATION_ID:
            inclusion, impact = ReleaseInclusion.INCLUDED, CompletionImpact.COMPLETION_CRITICAL
        else:
            inclusion, impact = ReleaseInclusion.EXCLUDED, CompletionImpact.NON_CRITICAL
        entries.append(
            ReleaseScopeEntry(
                operation_id=operation_id,
                inclusion=inclusion,
                completion_impact=impact,
                target_environment_key="property-env",
                operation_plan_key="property-plan",
            )
        )
    return tuple(entries)


@given(scenario=_baseline_scenario(), run_id=st.uuids(), now=_AWARE_DATETIMES)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=_FIXTURE_HEALTH,
)
def test_property_2_baseline_freeze_ordered_immutable_append_only(
    tmp_path, scenario, run_id, now
) -> None:
    """**Property 2: Baseline freeze is ordered, immutable, and append-only verified**

    **Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6**

    A baseline artifact is reusable only when every recomputed section digest
    matches the frozen declaration (plus revision/schema/task/order facts);
    tampering with any single section makes it non-reusable.  The store is
    idempotent for byte-identical puts and refuses different bytes under an
    existing identity; verification evidence is append-only; ``freeze_s0``
    freezes in order for provable baselines and records
    ``BASELINE_FREEZE_ORDER_UNPROVEN`` without ever reaching the scope freeze
    when the order/hash cannot be proven.
    """

    document = scenario["document"]
    captured_at = scenario["captured_at"]
    first_change = scenario["first_new_behavior_change_at"]
    expected_order_ok = first_change is None or captured_at <= first_change
    expected_task_ok = str(document["task"]).startswith(scenario["expected_task_prefix"])
    expected_schema_ok = document["schema_version"] == 1
    expected_revision_ok = scenario["revision_exists"]
    expected_status_ok = (
        scenario["current_status_digest"] == document["repository"]["worktree_status_digest"]
    )
    expected_reusable = (
        expected_revision_ok
        and expected_schema_ok
        and expected_task_ok
        and expected_order_ok
    )

    honest_path = _write_artifact(tmp_path, f"{run_id.hex}-honest.json", document)
    result, parsed = audit_baseline_artifact(honest_path, **_audit_kwargs(scenario))
    assert result.section_digests_match is True
    assert result.revision_exists_in_history is expected_revision_ok
    assert result.schema_version_ok is expected_schema_ok
    assert result.task_matches is expected_task_ok
    assert result.freeze_order_provable is expected_order_ok
    assert result.worktree_status_matches_capture is expected_status_ok
    assert result.reusable_as_canonical_baseline is expected_reusable
    captured_revision = document["repository"]["git_revision"]
    if expected_revision_ok and captured_revision != scenario["current_revision"]:
        assert result.commits_since_capture == ("c" * 40, "d" * 40)
    else:
        assert result.commits_since_capture == ()

    # 篡改任一 section → digest 不再匹配 → 不可复用（1.3/1.4）。
    tampered = deepcopy(document)
    tampered[scenario["tamper_section"]] = {"__tampered__": run_id.hex}
    tampered_path = _write_artifact(tmp_path, f"{run_id.hex}-tampered.json", tampered)
    tampered_result, tampered_document = audit_baseline_artifact(
        tampered_path, **_audit_kwargs(scenario)
    )
    assert tampered_result.section_digests_match is False
    assert tampered_result.reusable_as_canonical_baseline is False

    # store.put：同 bytes 幂等，异 bytes 拒（1.3/1.6）。
    store_root = _fresh_store_root(tmp_path, run_id.hex)
    primitive_store = GovernanceObjectStore(store_root / "primitives")
    baseline_envelope = _put(
        primitive_store,
        kind="baseline_artifact",
        payload={"artifact": parsed, "artifact_path": result.artifact_path},
    )
    again = _put(
        primitive_store,
        kind="baseline_artifact",
        payload={"artifact": parsed, "artifact_path": result.artifact_path},
    )
    assert again.object_id == baseline_envelope.object_id
    baseline_path = _object_path(primitive_store, baseline_envelope.object_id)
    baseline_path.write_text(json.dumps({"overwritten": True}), encoding="utf-8")
    with pytest.raises(GovernanceError) as caught:
        _put(
            primitive_store,
            kind="baseline_artifact",
            payload={"artifact": parsed, "artifact_path": result.artifact_path},
        )
    assert caught.value.code == "immutable_overwrite_refused"
    with pytest.raises(GovernanceError):
        primitive_store.get(baseline_envelope.object_id)

    # Baseline_Verification_Evidence 只追加（1.4）：新增单调递增，重放幂等。
    evidence_store = GovernanceObjectStore(store_root / "evidence")
    evidence_ids = []
    for attempt in range(3):
        envelope = _put(
            evidence_store,
            kind=BASELINE_VERIFICATION_KIND,
            payload={"attempt": attempt, "artifact_sha256": result.artifact_sha256},
        )
        evidence_ids.append(envelope.object_id)
        assert len(list((evidence_store.root / BASELINE_VERIFICATION_KIND).glob("*.json"))) == attempt + 1
    for attempt in range(3):
        replayed = _put(
            evidence_store,
            kind=BASELINE_VERIFICATION_KIND,
            payload={"attempt": attempt, "artifact_sha256": result.artifact_sha256},
        )
        assert replayed.object_id == evidence_ids[attempt]
    assert len(list((evidence_store.root / BASELINE_VERIFICATION_KIND).glob("*.json"))) == 3
    assert [
        envelope.object_id for envelope in evidence_store.iter_kind(BASELINE_VERIFICATION_KIND)
    ] == sorted(evidence_ids, key=lambda object_id: object_id.rsplit(":", 1)[-1])
    for object_id in evidence_ids:
        assert evidence_store.get(object_id).object_id == object_id

    # 可证明顺序/哈希 → freeze_s0 有序冻结（1.2–1.6）。
    # schema_version 属于文档内容，强制 reusable 时需要文档级变体（不影响 section digests）。
    reusable_path = _write_artifact(
        tmp_path, f"{run_id.hex}-reusable.json", dict(document, schema_version=1)
    )
    reusable_result, reusable_document = audit_baseline_artifact(
        reusable_path, **_audit_kwargs(scenario, force_reusable=True)
    )
    assert reusable_result.reusable_as_canonical_baseline is True
    ok_store = GovernanceObjectStore(store_root / "freeze-ok")
    ok_events = GovernanceEventLog(ok_store)
    receipt = freeze_s0(
        store=ok_store,
        events=ok_events,
        now=now,
        actor=_ACTOR,
        code_revision=_CODE_REVISION,
        baseline_audits=((reusable_result, reusable_document),),
        bootstrap_note={"note": "property-test bootstrap"},
        inventory_entries=(
            InventoryEntry(
                path="src/baseline_capture.py",
                workspace_status="CLEAN",
                content_sha256=None,
                decision="REUSE",
                rationale="reuse the existing capture script",
            ),
        ),
        gap_records=(),
        scope_entries=_scope_entries_fixed(),
    )
    event_types = [event.payload["event_type"] for event in ok_events.iter_events()]
    assert event_types == [
        "GOVERNANCE_BOOTSTRAP",
        "BASELINE_AUDIT_VERIFIED",
        "AUDIT_INVENTORY_FROZEN",
        "REGISTRY_FROZEN",
        "RELEASE_SCOPE_FROZEN",
    ]
    assert ok_store.get(receipt.release_scope_manifest_id).object_id == receipt.release_scope_manifest_id
    assert ok_store.get(receipt.baseline_object_ids[0]).object_id == receipt.baseline_object_ids[0]

    # 重复冻结：同一 payload 复用对象（幂等），事件日志只追加不重写。
    second_receipt = freeze_s0(
        store=ok_store,
        events=ok_events,
        now=now,
        actor=_ACTOR,
        code_revision=_CODE_REVISION,
        baseline_audits=((reusable_result, reusable_document),),
        bootstrap_note={"note": "property-test bootstrap"},
        inventory_entries=(
            InventoryEntry(
                path="src/baseline_capture.py",
                workspace_status="CLEAN",
                content_sha256=None,
                decision="REUSE",
                rationale="reuse the existing capture script",
            ),
        ),
        gap_records=(),
        scope_entries=_scope_entries_fixed(),
    )
    assert second_receipt == receipt
    event_types_after = [event.payload["event_type"] for event in ok_events.iter_events()]
    assert event_types_after[: len(event_types)] == event_types
    assert len(event_types_after) == 2 * len(event_types)

    # 顺序/哈希不可证明 → S0 记录 BASELINE_FREEZE_ORDER_UNPROVEN 且永不进入 scope 冻结（1.5）。
    blocked_store = GovernanceObjectStore(store_root / "freeze-blocked")
    blocked_events = GovernanceEventLog(blocked_store)
    with pytest.raises(GovernanceError) as caught:
        freeze_s0(
            store=blocked_store,
            events=blocked_events,
            now=now,
            actor=_ACTOR,
            code_revision=_CODE_REVISION,
            baseline_audits=((tampered_result, tampered_document),),
            bootstrap_note={"note": "property-test bootstrap"},
            inventory_entries=(),
            gap_records=(),
            scope_entries=_scope_entries_fixed(),
        )
    assert caught.value.code == "baseline_freeze_order_unproven"
    blocked_event_types = [event.payload["event_type"] for event in blocked_events.iter_events()]
    assert blocked_event_types == ["GOVERNANCE_BOOTSTRAP", "BASELINE_FREEZE_ORDER_UNPROVEN"]
    assert blocked_events.first_event_of_type("RELEASE_SCOPE_FROZEN") is None
    assert list(blocked_store.iter_kind("release_scope_manifest")) == []


# ---------------------------------------------------------------------------
# Property 3 (task 0.13) — traceability complete and referentially valid.
# ---------------------------------------------------------------------------

_TRACEABILITY_KINDS = (
    "registry_snapshot",
    "release_scope_manifest",
    "baseline_artifact",
    "audit_inventory",
    "gap_record",
    "layer_2_evidence_manifest",
)


@st.composite
def _traceability_scenario(draw: Any) -> dict[str, Any]:
    object_count = draw(st.integers(min_value=1, max_value=6))
    objects = []
    for index in range(object_count):
        objects.append(
            (
                draw(st.sampled_from(_TRACEABILITY_KINDS)),
                {
                    "label": draw(st.text(min_size=1, max_size=12)),
                    "sequence": index,
                    "detail": draw(_JSON_VALUES),
                },
            )
        )
    requirement_refs = draw(
        st.lists(_REQUIREMENT_REF, min_size=1, max_size=10, unique=True)
    )
    matrix = {
        ref: draw(st.lists(st.integers(0, object_count - 1), min_size=1, max_size=3, unique=True))
        for ref in requirement_refs
    }
    missing_refs = draw(
        st.lists(
            st.builds(
                lambda kind, digest: f"{kind}:sha256:{digest}",
                st.sampled_from(_TRACEABILITY_KINDS),
                _HEX64,
            ),
            min_size=1,
            max_size=3,
        )
    )
    malformed_refs = draw(
        st.lists(
            st.sampled_from(
                ["latest", "file:///tmp/x.json", "evidence://latest", "", "RELEASE:sha256:zz"]
            ),
            min_size=1,
            max_size=2,
        )
    )
    return {
        "objects": objects,
        "matrix": matrix,
        "missing_refs": missing_refs,
        "malformed_refs": malformed_refs,
    }


@given(scenario=_traceability_scenario(), run_id=st.uuids())
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=_FIXTURE_HEALTH,
)
def test_property_3_traceability_complete_referentially_valid(tmp_path, scenario, run_id) -> None:
    """**Property 3: Traceability is complete and referentially valid**

    **Validates: Requirements 1.9**

    Every requirement reference in the generated matrix maps to at least one
    registry object, and every referenced ``object_id`` resolves to exactly the
    stored envelope with a recomputed identity match (also via its immutable
    ``evidence://sha256/`` URI).  References that cannot be resolved fail
    closed with ``GovernanceError`` — never a wrong or partial object.
    """

    store = GovernanceObjectStore(_fresh_store_root(tmp_path, run_id.hex))
    stored_ids: list[str] = []
    for kind, payload in scenario["objects"]:
        envelope = _put(store, kind=kind, payload=payload)
        stored_ids.append(envelope.object_id)

    # 完备且引用有效：矩阵覆盖每个生成的 requirement ref，引用全部可解析。
    matrix_payload: dict[str, list[str]] = {}
    for requirement_ref, indexes in scenario["matrix"].items():
        assert indexes, "each requirement ref must map to at least one object"
        resolved = []
        for index in indexes:
            object_id = stored_ids[index]
            envelope = store.get(object_id)
            assert envelope.object_id == object_id
            uri = store.evidence_uri_for(object_id)
            via_uri = store.resolve_evidence_uri(uri)
            assert via_uri.object_id == envelope.object_id
            assert via_uri.to_document() == envelope.to_document()
            resolved.append(object_id)
        matrix_payload[requirement_ref] = resolved
    assert set(matrix_payload) == set(scenario["matrix"])

    matrix_envelope = _put(
        store,
        kind="traceability_matrix",
        payload={"matrix": matrix_payload},
        referenced_object_ids=tuple(stored_ids),
    )
    loaded = store.get(matrix_envelope.object_id)
    assert set(loaded.referenced_object_ids) == set(stored_ids)
    for reference in loaded.referenced_object_ids:
        assert store.get(reference).object_id == reference

    # 不可解析引用 fail-closed：格式合法但未存储 → object_not_found。
    for missing in scenario["missing_refs"]:
        if missing in stored_ids:
            continue
        with pytest.raises(GovernanceError) as caught:
            store.get(missing)
        assert caught.value.code == "object_not_found"
        digest = missing.rsplit(":", 1)[-1]
        with pytest.raises(GovernanceError) as caught:
            store.resolve_evidence_uri(EVIDENCE_URI_SCHEME + digest)
        assert caught.value.code == "evidence_uri_unresolvable"

    # 畸形/可变引用同样 fail-closed。
    for malformed in scenario["malformed_refs"]:
        with pytest.raises(GovernanceError) as caught:
            store.get(malformed)
        assert caught.value.code in {"invalid_object_id", "object_not_found"}
        with pytest.raises(GovernanceError) as caught:
            store.resolve_evidence_uri(malformed)
        assert caught.value.code in {"invalid_evidence_uri", "evidence_uri_unresolvable"}


# ---------------------------------------------------------------------------
# Property 4 (task 0.14) — golden harness determinism.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _golden_adapter_cases() -> tuple[tuple[str, Any], ...]:
    data = load_golden_fixture(_FIXTURE_PATH)
    section = load_projection_golden_cases(_FIXTURE_PATH)
    cases: list[tuple[str, Any]] = []
    for case in section["evidence_eligibility"]["cases"]:
        cases.append(
            (
                "evidence",
                {
                    **case["input"],
                    "execution_policy_fingerprint": data.execution_policy.fingerprint(),
                },
            )
        )
    for recommendation in data.recommendations():
        cases.append(
            (
                "execution",
                (recommendation, data.bars_for(recommendation), data.execution_policy),
            )
        )
    for case in section["outcome_identity"]["cases"]:
        cases.append(("outcome", case["input"]))
    for case in section["risk_decision"]["cases"]:
        cases.append(("risk", case["input"]))
    return tuple(cases)


def _run_golden_adapter(kind: str, payload: Any):
    if kind == "evidence":
        return EvidenceEligibilityAdapter().run(payload)
    if kind == "execution":
        recommendation, bars, policy = payload
        return TPlusOneExecutionAdapter().run(recommendation, bars, policy)
    if kind == "outcome":
        return OutcomeIdentityAdapter().run(payload)
    return MinCapRiskDecisionAdapter().run(payload)


@given(hdata=st.data())
@settings(max_examples=100, deadline=None)
def test_property_4_golden_harness_deterministic(hdata) -> None:
    """**Property 4: Golden harness is deterministic**

    **Validates: Requirements 1.10**

    For any fixture case fed through the public harness adapters, two runs
    produce byte-identical ``canonical_json`` output.  The first run executes
    inside ``external_network_disabled`` (the adapters carry the same guard
    internally): fixed inputs, no network, no wall clock, no mutable database.
    """

    cases = _golden_adapter_cases()
    order = hdata.draw(st.permutations(range(len(cases))), label="case order")
    count = hdata.draw(st.integers(min_value=1, max_value=3), label="case count")
    for position in order[:count]:
        kind, payload = cases[position]
        with external_network_disabled():
            first = _run_golden_adapter(kind, payload).to_canonical_json()
        second = _run_golden_adapter(kind, payload).to_canonical_json()
        assert first.encode("utf-8") == second.encode("utf-8")


# ---------------------------------------------------------------------------
# Property 5 (task 0.15) — safe initialization and unlock never imply completion.
# ---------------------------------------------------------------------------

_STATE_MACHINES = (
    (
        "code",
        CodeState,
        transition_code_state,
        _state_module._CODE_TRANSITIONS,
        CodeState.NOT_STARTED,
    ),
    (
        "environment",
        EnvironmentAcceptanceState,
        transition_environment_state,
        _state_module._ENVIRONMENT_TRANSITIONS,
        EnvironmentAcceptanceState.NOT_RUN,
    ),
    (
        "operation",
        OperationExecutionState,
        transition_included_operation_state,
        _state_module._INCLUDED_OPERATION_TRANSITIONS,
        OperationExecutionState.PLANNED,
    ),
    (
        "activation",
        ProductionActivationState,
        transition_activation_state,
        _state_module._ACTIVATION_TRANSITIONS,
        ProductionActivationState.NOT_AUTHORIZED,
    ),
)


@given(hdata=st.data())
@settings(max_examples=100, deadline=None)
def test_property_5_safe_initialization_unlock_implies_no_completion(hdata) -> None:
    """**Property 5: Safe initialization and unlock rules never imply completion**

    **Validates: Requirements 1.11, 1.12, 1.13**

    For random state sequences, legal transitions succeed and illegal ones are
    refused with ``illegal_state_transition`` leaving the state unchanged.
    Layer 1 results can never mark an environment ``ACCEPTED``; an EXCLUDED
    operation's terminal state is always ``NOT_AUTHORIZED_NOT_EXECUTED`` and it
    never enters ``completion_critical_operations``.
    """

    for label, enum, transition, table, initial in _STATE_MACHINES:
        current = initial
        steps = hdata.draw(st.integers(min_value=1, max_value=8), label=f"{label} steps")
        for _ in range(steps):
            target = hdata.draw(st.sampled_from(list(enum)), label=f"{label} target")
            legal = target in table.get(current, frozenset())
            if (
                transition is transition_included_operation_state
                and target is OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED
            ):
                legal = False  # reserved for EXCLUDED operations
            if legal:
                transition(current, target)
                current = target
            else:
                with pytest.raises(GovernanceError) as caught:
                    transition(current, target)
                assert caught.value.code == "illegal_state_transition"
                # 非法迁移必拒且状态不变（下一段仍以 current 为基准）。

    # Layer 1 证据永不能把环境标成 ACCEPTED（1.12：只解锁 dependency-safe 工作）。
    with pytest.raises(GovernanceError) as caught:
        apply_layer1_environment_result(EnvironmentAcceptanceState.ACCEPTED)
    assert caught.value.code == "layer1_cannot_accept"
    for recorded in (
        EnvironmentAcceptanceState.NOT_RUN,
        EnvironmentAcceptanceState.BLOCKED,
        EnvironmentAcceptanceState.FAILED,
    ):
        assert apply_layer1_environment_result(recorded) is recorded

    # ACCEPTED 是终态；NOT_STARTED 不能直达 CODE_COMPLETE（解锁不隐含完成）。
    for target in EnvironmentAcceptanceState:
        with pytest.raises(GovernanceError):
            transition_environment_state(EnvironmentAcceptanceState.ACCEPTED, target)
    with pytest.raises(GovernanceError):
        transition_code_state(CodeState.NOT_STARTED, CodeState.CODE_COMPLETE)

    # EXCLUDED operation 终态恒为 NOT_AUTHORIZED_NOT_EXECUTED 且 INCLUDED 迁移不可达。
    assert excluded_operation_terminal_state() is OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED
    for source in OperationExecutionState:
        with pytest.raises(GovernanceError):
            transition_included_operation_state(
                source, OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED
            )

    # 随机 scope：EXCLUDED operation 不进入 completion_critical_operations（1.13）。
    entries = []
    excluded_operations: list[str] = []
    for operation_id in sorted(operation_ids()):
        if operation_id == MIGRATION_APPLY_OPERATION_ID:
            inclusion = ReleaseInclusion.INCLUDED
            impact = CompletionImpact.COMPLETION_CRITICAL
        else:
            inclusion = hdata.draw(
                st.sampled_from(list(ReleaseInclusion)), label=f"inclusion:{operation_id}"
            )
            if inclusion is ReleaseInclusion.INCLUDED:
                impact = hdata.draw(
                    st.sampled_from(list(CompletionImpact)), label=f"impact:{operation_id}"
                )
            else:
                impact = hdata.draw(
                    st.sampled_from(
                        [CompletionImpact.NON_CRITICAL, CompletionImpact.ACTIVATION_ONLY]
                    ),
                    label=f"impact:{operation_id}",
                )
        if inclusion is ReleaseInclusion.EXCLUDED:
            excluded_operations.append(operation_id)
        entries.append(
            ReleaseScopeEntry(
                operation_id=operation_id,
                inclusion=inclusion,
                completion_impact=impact,
                target_environment_key="property-env",
                operation_plan_key="property-plan",
            )
        )
    manifest = ReleaseScopeManifest(
        registry_snapshot_id="property-registry",
        entries=tuple(entries),
        behavior_change_barrier_event_id="property-barrier",
    )
    completion_critical = manifest.completion_critical_operations()
    assert MIGRATION_APPLY_OPERATION_ID in completion_critical
    for operation_id in excluded_operations:
        assert operation_id not in completion_critical
        assert manifest.entry_for(operation_id).inclusion is ReleaseInclusion.EXCLUDED
    for operation_id in completion_critical:
        assert manifest.entry_for(operation_id).inclusion is ReleaseInclusion.INCLUDED
        assert (
            manifest.entry_for(operation_id).completion_impact
            is CompletionImpact.COMPLETION_CRITICAL
        )


# ---------------------------------------------------------------------------
# Property 50 (task 0.16) — deferred and brokerage scope is fail-closed.
# ---------------------------------------------------------------------------

_BROKERAGE_RESOURCE_WORDS = (
    "credential",
    "credentials",
    "account",
    "accounts",
    "position",
    "positions",
    "balance",
    "balances",
    "order",
    "orders",
)
_BENIGN_IDENTIFIERS = (
    "sort_order",
    "position_size",
    "position_range",
    "broker_recommend",
    "top_buy_broker",
    "balanced",
    "recorder",
    "orderly_report",
    "accountant_note",
    "credentialized",
    "password",
    "trade_date",
    "run_mode",
)
_WORD = st.from_regex(r"[a-z]{2,10}", fullmatch=True)


@st.composite
def _deferred_declaration(draw: Any) -> str:
    member = draw(st.sampled_from(list(DeferredInitiative)))
    form = draw(st.sampled_from([member.value, member.name]))
    bits = draw(st.lists(st.booleans(), min_size=len(form), max_size=len(form)))
    return "".join(char.upper() if bit else char.lower() for char, bit in zip(form, bits))


@st.composite
def _brokerage_key(draw: Any) -> str:
    shape = draw(st.sampled_from(["resource", "broker_secret", "brokerage", "chinese"]))
    if shape == "resource":
        parts = [
            part
            for part in (
                draw(st.sampled_from(["", "api", "live", "my", "test"])),
                draw(st.sampled_from(_BROKERAGE_RESOURCE_WORDS)),
                draw(st.sampled_from(["", "id", "token", "ref"])),
            )
            if part
        ]
    elif shape == "broker_secret":
        parts = [
            "broker",
            draw(
                st.sampled_from(
                    ["token", "secret", "key", "password", "passwd", "credential", "credentials"]
                )
            ),
        ]
    elif shape == "brokerage":
        parts = [draw(st.sampled_from(["brokerage", "brokerages"]))]
        if draw(st.booleans()):
            parts.append(draw(st.sampled_from(["api", "config", "client"])))
    else:
        parts = [draw(st.sampled_from(["券商", "券商账户", "券商api"]))]
    joiner = draw(st.sampled_from(["_", "__", ""]))
    if not joiner:
        return "".join(part.capitalize() if index else part for index, part in enumerate(parts))
    cased = [
        draw(st.sampled_from([part.lower(), part.upper(), part.capitalize()]))
        for part in parts
    ]
    return joiner.join(cased)


def _expect_deferred(initiative: Any) -> bool:
    if isinstance(initiative, DeferredInitiative):
        return True
    if isinstance(initiative, str):
        normalized = initiative.strip().upper()
        return any(
            normalized in (member.name.upper(), member.value.upper())
            for member in DeferredInitiative
        )
    return False


@given(hdata=st.data())
@settings(max_examples=100, deadline=None)
def test_property_50_deferred_brokerage_scope_fail_closed(hdata) -> None:
    """**Property 50: Deferred and brokerage scope is fail-closed**

    **Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7**

    For random payloads/CLI tokens/config keys: declaring D1/D2/D3 always
    raises; brokerage credential/account/position/balance/order access always
    raises before any secret resolution; the rejection event never contains the
    raw sensitive value (identity/hash/reason only); benign system vocabulary
    and ``historical_reconstruction`` always pass; risk portfolio kinds outside
    the four-kind whitelist are denied.
    """

    # D1/D2/D3 恒拒（任意大小写变体）；historical_reconstruction 恒放行。
    initiative = hdata.draw(
        st.one_of(
            _deferred_declaration(),
            st.sampled_from(list(DeferredInitiative)),
            st.just(HISTORICAL_RECONSTRUCTION_INITIATIVE),
            st.text(alphabet=string.ascii_letters + string.digits + "_ ", max_size=40),
            st.none(),
            st.integers(),
        ),
        label="initiative",
    )
    if _expect_deferred(initiative):
        with pytest.raises(ScopeViolation) as caught:
            assert_initiative_allowed(initiative, context="property")
        assert caught.value.code == DEFERRED_INITIATIVE_BLOCKED
        assert caught.value.event["code"] == DEFERRED_INITIATIVE_BLOCKED
    else:
        assert assert_initiative_allowed(initiative, context="property") is None
    assert assert_initiative_allowed(
        HISTORICAL_RECONSTRUCTION_INITIATIVE, context="property"
    ) is None

    # 五类 brokerage 资源（payload key / CLI token / config key）恒拒且事件不含原始值。
    key = hdata.draw(_brokerage_key(), label="brokerage key")
    secret = hdata.draw(
        st.text(alphabet=string.ascii_letters + string.digits + "-_+", min_size=8, max_size=20),
        label="secret",
    ) + "Z!"
    depth = hdata.draw(st.integers(min_value=0, max_value=2), label="nesting depth")
    node: dict[str, Any] = {key: secret}
    for level in range(depth):
        node = {f"layer{level}": node}
    payload = {"run_mode": "formal", "note": "通过券商下单的散文消息", **node}
    with pytest.raises(ScopeViolation) as caught:
        assert_no_brokerage_access(payload, context="property")
    exc = caught.value
    assert exc.code == BROKERAGE_ACCESS_DENIED
    assert exc.event["event"] == "scope_guard.brokerage_access_denied"
    assert exc.event["matched"] == "key"
    assert exc.event["resource"] in {
        "credential",
        "account",
        "position",
        "balance",
        "order",
        "brokerage",
    }
    blob = json.dumps(exc.event, ensure_ascii=False)
    assert secret not in blob
    assert secret not in str(exc)
    assert exc.event["value_sha256_12"] == hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]

    token = f"--{key}"
    with pytest.raises(ScopeViolation) as caught:
        assert_no_brokerage_access(["--start", "2026-07-01", token], context="cli")
    assert caught.value.event["matched"] == "token"
    assert token not in json.dumps(caught.value.event, ensure_ascii=False)
    assert caught.value.event["value_sha256_12"] == hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]

    with pytest.raises(ScopeViolation):
        assert_no_brokerage_access(key, context="config")

    # 既有系统词汇与散文值恒放行。
    benign_key = hdata.draw(st.sampled_from(_BENIGN_IDENTIFIERS), label="benign key")
    prose = hdata.draw(
        st.builds(lambda first, second: f"{first} {second}", _WORD, _WORD),
        label="prose value",
    )
    assert_no_brokerage_access(
        {benign_key: prose, "dataset": "broker_recommend", "risk_template": "balanced"},
        context="property",
    )
    assert_no_brokerage_access(
        ["--start", "2026-05-22", "--skip-existing", benign_key], context="cli"
    )

    # Risk_System 组合边界：白名单外恒拒（12.6）。
    kind = hdata.draw(
        st.one_of(
            st.sampled_from(sorted(ALLOWED_RISK_PORTFOLIO_KINDS)),
            st.text(alphabet=string.ascii_letters + "_ ", max_size=20),
            st.none(),
        ),
        label="risk portfolio kind",
    )
    if isinstance(kind, str) and kind.strip().lower() in ALLOWED_RISK_PORTFOLIO_KINDS:
        assert assert_risk_portfolio_kind(kind, context="property") is None
    else:
        with pytest.raises(ScopeViolation) as caught:
            assert_risk_portfolio_kind(kind, context="property")
        assert caught.value.code == RISK_PORTFOLIO_KIND_DENIED


# ---------------------------------------------------------------------------
# Property 51 (task 0.17) — preregistered manifests immutable and temporal.
# ---------------------------------------------------------------------------

_OBJECT_ID_TEXT = st.builds(
    lambda key, digest: f"{key}:sha256:{digest}", _SAFE_KEY, _HEX64
)
_NUMBER = st.one_of(
    st.integers(-1000, 1000),
    st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
)
_EVIDENCE_URI_PREFIX = st.builds(
    lambda digest: EVIDENCE_URI_SCHEME + digest[:8], _HEX64
)


def _manifest_header(draw: Any, kind: str) -> dict[str, Any]:
    return {
        "manifest_kind": kind,
        "stable_manifest_key": draw(_SAFE_KEY),
        "registry_snapshot_id": draw(_SAFE_KEY),
        "frozen_at": draw(_AWARE_DATETIMES),
        "applies_to_code_revision": draw(_REVISION),
        "supersedes_manifest_id": draw(st.one_of(st.none(), _OBJECT_ID_TEXT)),
        "secret_handle_ids": draw(st.lists(_SAFE_KEY, max_size=2)),
    }


@st.composite
def _manifest_payload(draw: Any, kind: str) -> dict[str, Any]:
    payload = _manifest_header(draw, kind)
    if kind == SHADOW_THRESHOLD_KIND:
        payload.update(
            {
                "target_operation_id": draw(_SAFE_KEY),
                "source_projection_id": draw(_SAFE_KEY),
                "candidate_projection_id": draw(_SAFE_KEY),
                "rules": draw(
                    st.lists(
                        st.fixed_dictionaries(
                            {
                                "metric_id": _SAFE_KEY,
                                "comparator": st.sampled_from([item.value for item in Comparator]),
                                "threshold_decimal": _NUMBER,
                                "minimum_sample": st.integers(min_value=1, max_value=500),
                                "failure_action": st.sampled_from(
                                    [item.value for item in ShadowFailureAction]
                                ),
                                "identity_dimensions": st.lists(_SAFE_KEY, max_size=2),
                            }
                        ),
                        min_size=1,
                        max_size=3,
                    )
                ),
            }
        )
    elif kind == RESTORE_CAPACITY_KIND:
        payload.update(
            {
                "target_environment_key": draw(_SAFE_KEY),
                "migration_plan_key": draw(_SAFE_KEY),
                "restore": {
                    "restore_point_kind": draw(_SAFE_KEY),
                    "verification": draw(
                        st.sampled_from(
                            ["REHEARSAL_PASSED", "CHECKSUM_VERIFIED", "SNAPSHOT_CONFIRMED"]
                        )
                    ),
                },
                "capacity_rules": draw(
                    st.lists(
                        st.fixed_dictionaries(
                            {
                                "metric": st.sampled_from([item.value for item in CapacityMetric]),
                                "comparator": st.sampled_from([item.value for item in Comparator]),
                                "threshold": _NUMBER,
                            }
                        ),
                        min_size=1,
                        max_size=3,
                    )
                ),
            }
        )
    elif kind == REAL_ENVIRONMENT_KIND:
        payload.update(
            {
                "environment_id": draw(_SAFE_KEY),
                "mysql": {
                    "server_uuid_hash": draw(_SAFE_KEY),
                    "endpoint_alias": draw(_SAFE_KEY),
                    "database_name": draw(_SAFE_KEY),
                    "engine_version": draw(_SAFE_KEY),
                    "port": draw(st.integers(min_value=1, max_value=65535)),
                },
                "prefect": {
                    "api_identity_hash": draw(_SAFE_KEY),
                    "server_version": draw(_SAFE_KEY),
                    "metadata_database_hash": draw(_SAFE_KEY),
                    "work_pool": draw(_SAFE_KEY),
                    "work_queue": draw(_SAFE_KEY),
                },
                "sanitized_network_target_ids": draw(st.lists(_SAFE_KEY, max_size=2)),
            }
        )
    elif kind == ACCEPTANCE_COMMAND_KIND:
        payload["commands"] = draw(
            st.lists(
                st.fixed_dictionaries(
                    {
                        "command_id": _SAFE_KEY,
                        "executable_entrypoint": _SAFE_KEY,
                        "argument_vector": st.lists(
                            st.from_regex(r"[a-z0-9_./-]{1,10}", fullmatch=True), max_size=4
                        ),
                        "expected_exit_codes": st.lists(
                            st.integers(min_value=0, max_value=8), max_size=3
                        ),
                        "timeout_seconds": st.integers(min_value=1, max_value=7200),
                        "code_checksum": _SAFE_KEY,
                        "assertion_ids": st.lists(_SAFE_KEY, max_size=2),
                    }
                ),
                min_size=1,
                max_size=2,
            )
        )
    else:  # LAYER_2_EVIDENCE_KIND
        payload.update(
            {
                "layer_2_evidence_set_id": draw(_SAFE_KEY),
                "real_environment_key": draw(_SAFE_KEY),
                "acceptance_command_ids": draw(st.lists(_SAFE_KEY, min_size=1, max_size=3)),
                "artifact_contracts": draw(
                    st.lists(
                        st.fixed_dictionaries(
                            {
                                "contract_id": _SAFE_KEY,
                                "schema_uri": _SAFE_KEY,
                                "evidence_uri_allowlist": st.lists(
                                    _EVIDENCE_URI_PREFIX, min_size=1, max_size=2
                                ),
                                "required_fields": st.lists(_SAFE_KEY, min_size=1, max_size=3),
                            }
                        ),
                        min_size=1,
                        max_size=2,
                    )
                ),
                "assertions": draw(
                    st.lists(
                        st.fixed_dictionaries(
                            {
                                "assertion_id": _SAFE_KEY,
                                "required": st.just(True),
                                "predicate": _SAFE_KEY,
                            }
                        ),
                        min_size=1,
                        max_size=2,
                    )
                ),
                "pass_rule": "ALL_REQUIRED_ASSERTIONS_PASS",
            }
        )
    return payload


def _first_rule_mutate(field: str, value: Any):
    def mutate(payload: dict[str, Any]) -> None:
        payload["rules"][0] = {**payload["rules"][0], field: value}

    return mutate


_COMMON_CORRUPTIONS = (
    lambda payload: payload.update(manifest_kind="unknown_manifest_kind"),
    lambda payload: payload.update(stable_manifest_key="bad key!!"),
    lambda payload: payload.update(frozen_at="not-a-timestamp"),
    lambda payload: payload.update(frozen_at=datetime(2026, 1, 1)),  # naive
    lambda payload: payload.update(applies_to_code_revision=""),
    lambda payload: payload.update(supersedes_manifest_id="not-a-manifest-ref"),
    lambda payload: payload.update(secret_handle_ids="not-a-list"),
    lambda payload: payload.update(notes="password=hunter2"),
    lambda payload: payload.update(notes="Bearer abc.def.ghi"),
)

_KIND_CORRUPTIONS = {
    SHADOW_THRESHOLD_KIND: (
        lambda payload: payload.update(rules=[]),
        _first_rule_mutate("comparator", "!="),
        _first_rule_mutate("threshold_decimal", True),
        _first_rule_mutate("minimum_sample", 0),
        _first_rule_mutate("failure_action", "IGNORE"),
        _first_rule_mutate("identity_dimensions", "not-a-list"),
    ),
    RESTORE_CAPACITY_KIND: (
        lambda payload: payload.update(capacity_rules=[]),
        lambda payload: payload["capacity_rules"].__setitem__(
            0, {**payload["capacity_rules"][0], "metric": "CPU_LOAD"}
        ),
        lambda payload: payload["capacity_rules"].__setitem__(
            0, {**payload["capacity_rules"][0], "threshold": "high"}
        ),
        lambda payload: payload["restore"].update(verification="GUESSED"),
    ),
    REAL_ENVIRONMENT_KIND: (
        lambda payload: payload["mysql"].update(port=0),
        lambda payload: payload["mysql"].update(port=70000),
        lambda payload: payload["mysql"].update(port="3306"),
        lambda payload: payload["mysql"].pop("server_uuid_hash"),
        lambda payload: payload.update(sanitized_network_target_ids="not-a-list"),
    ),
    ACCEPTANCE_COMMAND_KIND: (
        lambda payload: payload.update(commands=[]),
        lambda payload: payload["commands"][0].update(argument_vector="pytest -q"),
        lambda payload: payload["commands"][0].update(expected_exit_codes=[True]),
        lambda payload: payload["commands"][0].update(timeout_seconds=0),
        lambda payload: payload["commands"][0].update(assertion_ids="not-a-list"),
    ),
    LAYER_2_EVIDENCE_KIND: (
        lambda payload: payload.update(acceptance_command_ids=[]),
        lambda payload: payload["artifact_contracts"][0].update(
            evidence_uri_allowlist=["https://mutable.example/"]
        ),
        lambda payload: payload["artifact_contracts"][0].update(required_fields=[]),
        lambda payload: payload["assertions"][0].update(required=False),
        lambda payload: payload.update(pass_rule="ANY_ASSERTION_PASS"),
    ),
}


@given(hdata=st.data())
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=_FIXTURE_HEALTH,
)
def test_property_51_preregistered_manifests_immutable_temporal(tmp_path, hdata) -> None:
    """**Property 51: Preregistered manifests are immutable and temporally valid**

    **Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.9**

    For each of the five manifest kinds, a well-formed payload always passes
    ``validate_manifest_payload`` and any single corrupted field is refused.
    Admission is ``ADMITTED`` only when ``frozen_at < trigger_point`` (with the
    revision matching); ``frozen_at >= trigger_point`` keeps the attempt
    ``BLOCKED`` before execution and records ``FAILED`` afterwards.  Manifest
    objects are immutable in the store: byte-identical re-puts are idempotent,
    different bytes under an existing identity are refused, and a superseding
    manifest preserves the old object.
    """

    kind = hdata.draw(st.sampled_from(PREREGISTERED_MANIFEST_KINDS), label="manifest kind")
    payload = hdata.draw(_manifest_payload(kind), label="manifest payload")

    # 合法 payload 必通过（13.1–13.5）。
    validate_manifest_payload(kind, payload)

    # 任一损坏字段必拒。
    corruptions = _COMMON_CORRUPTIONS + _KIND_CORRUPTIONS[kind]
    corrupt = hdata.draw(st.sampled_from(corruptions), label="corruption")
    corrupted = deepcopy(payload)
    corrupt(corrupted)
    with pytest.raises(GovernanceError):
        validate_manifest_payload(kind, corrupted)

    # 时序：frozen_at >= trigger → pre-execution BLOCKED / post-execution FAILED（13.6）。
    store_token = hdata.draw(st.uuids(), label="store id").hex
    store = GovernanceObjectStore(_fresh_store_root(tmp_path, store_token))
    frozen_at = payload["frozen_at"]
    envelope = _put(store, kind=kind, payload=payload, created_at=frozen_at)
    trigger = hdata.draw(_AWARE_DATETIMES, label="trigger point")
    pre_execution = hdata.draw(st.booleans(), label="pre execution")
    result = admit_acceptance_attempt(
        manifest=envelope,
        context=AdmissionContext(
            trigger_point_at=trigger,
            applies_to_code_revision=payload["applies_to_code_revision"],
        ),
        store=store,
        pre_execution=pre_execution,
    )
    if frozen_at < trigger:
        assert result.verdict is AdmissionVerdict.ADMITTED
        assert result.mismatches == ()
    else:
        assert "manifest_frozen_after_trigger_point" in result.mismatches
        expected = AdmissionVerdict.BLOCKED if pre_execution else AdmissionVerdict.FAILED
        assert result.verdict is expected

    # 不可变：同 bytes 幂等；异 bytes 覆写必拒（13.9 前置）。
    again = _put(store, kind=kind, payload=payload, created_at=frozen_at)
    assert again.object_id == envelope.object_id
    _object_path(store, envelope.object_id).write_text(
        json.dumps({"rewritten": True}), encoding="utf-8"
    )
    with pytest.raises(GovernanceError) as caught:
        _put(store, kind=kind, payload=payload, created_at=frozen_at)
    assert caught.value.code == "immutable_overwrite_refused"
    with pytest.raises(GovernanceError):
        store.get(envelope.object_id)
    # 立即写回原始 canonical bytes（put 拒写已存在 identity，无法用它恢复），
    # 同时保持 hypothesis 重放同 example 时环境可复原。
    _object_path(store, envelope.object_id).write_text(
        envelope.to_canonical_json(), encoding="utf-8"
    )

    # 替代：新 manifest identity，旧 manifest 与 supersede 链完整保留（13.9）。
    successor_payload = deepcopy(payload)
    successor_payload["stable_manifest_key"] = f"{payload['stable_manifest_key']}_v2"
    successor_payload["supersedes_manifest_id"] = envelope.object_id
    successor = _put(
        store,
        kind=kind,
        payload=successor_payload,
        created_at=frozen_at,
        supersedes_id=envelope.object_id,
    )
    assert successor.object_id != envelope.object_id
    chain = store.supersede_chain(successor.object_id)
    assert [item.object_id for item in chain] == [successor.object_id, envelope.object_id]
    assert store.get(envelope.object_id).object_id == envelope.object_id


# ---------------------------------------------------------------------------
# Property 53 (task 0.18) — every obligation has a current proof route.
# ---------------------------------------------------------------------------


def _obligation_strategy():
    return st.builds(
        CorrectnessObligation,
        obligation_id=st.builds(
            lambda tens, ones: f"CO-PR-0{tens}{ones}", st.integers(0, 5), st.integers(0, 9)
        ),
        property_number=st.integers(1, 54),
        title=st.text(min_size=1, max_size=24),
        requirement_refs=st.lists(_REQUIREMENT_REF, max_size=3),
        input_domain_key=st.builds(lambda key: f"domain://{key}", _SAFE_KEY),
        oracle_key=st.builds(lambda key: f"oracle://{key}", _SAFE_KEY),
        revision_binding_scope=_SAFE_KEY,
        minimum_iterations=st.integers(100, 200),
        property_test_module=st.sampled_from(
            ["", "backend/tests/governance/test_property_s0_governance.py"]
        ),
    )


@st.composite
def _revision_binding(draw: Any) -> RevisionBinding:
    digests = []
    for index in range(draw(st.integers(min_value=1, max_value=3))):
        digests.append(
            DependencyDigest(
                kind=draw(st.sampled_from(list(DependencyDigestKind))),
                locator=f"dep_{index}.py",
                content_sha256=draw(_HEX64),
            )
        )
    return RevisionBinding(scope_id=draw(_SAFE_KEY), digests=tuple(digests))


def _property_evidence_strategy(obligation: CorrectnessObligation, binding: RevisionBinding):
    return st.builds(
        PropertyTestEvidence,
        obligation_id=st.one_of(
            st.just(obligation.obligation_id), st.just("CO-PR-0XX"), _OBJECT_ID_TEXT
        ),
        result=st.sampled_from(["PASS", "PASS", "FAIL", "ERROR", "SKIP"]),
        iterations=st.integers(1, 250),
        evidence_uri=_EVIDENCE_URI_PREFIX,
        revision_binding_hash=st.one_of(st.just(binding.binding_hash), _HEX64),
    )


def _assessment_strategy(binding: RevisionBinding):
    return st.builds(
        EquivalenceAssessment,
        evidence_uri=_EVIDENCE_URI_PREFIX,
        domain_same_or_stronger=st.booleans(),
        oracle_equivalent=st.booleans(),
        result=st.sampled_from(["PASS", "FAIL"]),
        conclusion=st.sampled_from(list(EquivalenceConclusion)),
        assessed_at=_AWARE_DATETIMES,
        revision_binding_hash=st.one_of(st.just(binding.binding_hash), _HEX64),
        semantic_fingerprint=st.text(alphabet="0123456789abcdef", min_size=8, max_size=24),
    )


def _expected_proof_route(
    obligation: CorrectnessObligation,
    binding: RevisionBinding,
    property_evidence: tuple[PropertyTestEvidence, ...],
    assessments: tuple[EquivalenceAssessment, ...],
) -> ProofRouteStatus:
    for evidence in property_evidence:
        if (
            evidence.obligation_id == obligation.obligation_id
            and evidence.result == "PASS"
            and evidence.iterations >= obligation.minimum_iterations
            and evidence.revision_binding_hash == binding.binding_hash
        ):
            return ProofRouteStatus.PROVEN
    for assessment in assessments:
        if (
            assessment.domain_same_or_stronger
            and assessment.oracle_equivalent
            and assessment.result == "PASS"
            and assessment.conclusion is EquivalenceConclusion.VALID
            and assessment.revision_binding_hash == binding.binding_hash
        ):
            return ProofRouteStatus.PROVEN
    return ProofRouteStatus.UNPROVEN


@given(hdata=st.data())
@settings(max_examples=100, deadline=None)
def test_property_53_every_obligation_has_current_proof_route(hdata) -> None:
    """**Property 53: Every correctness obligation has a successful current proof route**

    **Validates: Requirements 13.10, 13.11, 13.12, 13.13**

    The obligation registry always validates (54 obligations, properties
    1..54 exactly once).  For a random obligation with random evidence
    combinations, ``evaluate_proof_route`` returns ``PROVEN`` exactly when a
    binding-matched PASS property evidence (with enough iterations) or a VALID
    equivalence assessment exists — and ``UNPROVEN`` otherwise, always with at
    least one recorded reason.
    """

    validate_obligation_registry()
    obligation = hdata.draw(_obligation_strategy(), label="obligation")
    assert obligation_for_property(obligation.property_number).property_number == (
        obligation.property_number
    )
    binding = hdata.draw(_revision_binding(), label="revision binding")
    property_evidence = tuple(
        hdata.draw(
            st.lists(_property_evidence_strategy(obligation, binding), max_size=3),
            label="property evidence",
        )
    )
    assessments = tuple(
        hdata.draw(
            st.lists(_assessment_strategy(binding), max_size=3),
            label="equivalence assessments",
        )
    )

    status, reasons = evaluate_proof_route(
        obligation,
        binding=binding,
        property_evidence=property_evidence,
        equivalence_assessments=assessments,
    )
    expected = _expected_proof_route(obligation, binding, property_evidence, assessments)
    assert status is expected
    if status is ProofRouteStatus.PROVEN:
        assert reasons == ()
    else:
        assert reasons, "UNPROVEN must record at least one fail-closed reason"


# ---------------------------------------------------------------------------
# Property 54 (task 0.19) — equivalent evidence invalidates on semantic change.
# ---------------------------------------------------------------------------


@given(hdata=st.data())
@settings(max_examples=100, deadline=None)
def test_property_54_equivalent_evidence_invalidates_on_semantic_change(hdata) -> None:
    """**Property 54: Equivalent correctness evidence invalidates on semantic change**

    **Validates: Requirements 13.14**

    For a random assessment and a random new semantic fingerprint: a changed
    fingerprint always yields ``STALE`` (all other fields preserved), an
    unchanged fingerprint always returns the assessment untouched.  A STALE
    assessment can never make ``evaluate_proof_route`` return ``PROVEN``, even
    when every other equivalence condition holds.
    """

    binding = hdata.draw(_revision_binding(), label="revision binding")
    fingerprint = hdata.draw(
        st.text(alphabet="0123456789abcdef", min_size=8, max_size=24), label="fingerprint"
    )
    changed = hdata.draw(st.booleans(), label="semantic change")
    new_fingerprint = f"{fingerprint}ff" if changed else fingerprint
    base = EquivalenceAssessment(
        evidence_uri=hdata.draw(_EVIDENCE_URI_PREFIX, label="evidence uri"),
        domain_same_or_stronger=hdata.draw(st.booleans(), label="domain flag"),
        oracle_equivalent=hdata.draw(st.booleans(), label="oracle flag"),
        result=hdata.draw(st.sampled_from(["PASS", "FAIL"]), label="result"),
        conclusion=hdata.draw(st.sampled_from(list(EquivalenceConclusion)), label="conclusion"),
        assessed_at=hdata.draw(_AWARE_DATETIMES, label="assessed at"),
        revision_binding_hash=binding.binding_hash,
        semantic_fingerprint=fingerprint,
    )

    invalidated = invalidate_assessment_on_semantic_change(
        base, current_semantic_fingerprint=new_fingerprint
    )
    if changed:
        assert invalidated.conclusion is EquivalenceConclusion.STALE
        assert invalidated.semantic_fingerprint == fingerprint
        assert invalidated.evidence_uri == base.evidence_uri
        assert invalidated.domain_same_or_stronger == base.domain_same_or_stronger
        assert invalidated.oracle_equivalent == base.oracle_equivalent
        assert invalidated.result == base.result
        assert invalidated.assessed_at == base.assessed_at
        assert invalidated.revision_binding_hash == base.revision_binding_hash
    else:
        assert invalidated is base

    # STALE 的评估即使其余条件全部满足也不得 PROVEN。
    obligation = hdata.draw(_obligation_strategy(), label="obligation")
    valid_assessment = EquivalenceAssessment(
        evidence_uri=base.evidence_uri,
        domain_same_or_stronger=True,
        oracle_equivalent=True,
        result="PASS",
        conclusion=EquivalenceConclusion.VALID,
        assessed_at=base.assessed_at,
        revision_binding_hash=binding.binding_hash,
        semantic_fingerprint=fingerprint,
    )
    proven, proven_reasons = evaluate_proof_route(
        obligation, binding=binding, equivalence_assessments=(valid_assessment,)
    )
    assert proven is ProofRouteStatus.PROVEN
    assert proven_reasons == ()

    staled = invalidate_assessment_on_semantic_change(
        valid_assessment, current_semantic_fingerprint=f"{fingerprint}ff"
    )
    assert staled.conclusion is EquivalenceConclusion.STALE
    status, reasons = evaluate_proof_route(
        obligation, binding=binding, equivalence_assessments=(staled,)
    )
    assert status is ProofRouteStatus.UNPROVEN
    assert "equivalent:assessment_not_valid" in reasons

    same = invalidate_assessment_on_semantic_change(
        valid_assessment, current_semantic_fingerprint=fingerprint
    )
    assert same is valid_assessment

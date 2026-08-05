"""S0 freeze sequence unit tests (spec task 0.20 supplement).

Runs ``freeze_s0`` against tmp_path stores with constructed baseline audit
results: success-path event ordering, unprovable-order refusal, ``_put_once``
idempotency, empty-scope refusal, and section-digest tamper detection in
``audit_baseline_artifact``.  No MySQL, network, real governance store, or
brokerage probing — every store is a tmp_path sandbox and every VCS probe is
a fake callable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.governance.envelope import GovernanceError
from app.governance.events import GovernanceEventLog
from app.governance.freeze import (
    BASELINE_ARTIFACT_KIND,
    BASELINE_VERIFICATION_KIND,
    InventoryEntry,
    _digest,
    _put_once,
    audit_baseline_artifact,
    freeze_s0,
)
from app.governance.registries import (
    MIGRATION_APPLY_OPERATION_ID,
    OPERATION_REGISTRY,
)
from app.governance.scope import (
    CompletionImpact,
    ReleaseInclusion,
    ReleaseScopeEntry,
)
from app.governance.store import GovernanceObjectStore

_CAPTURED_REV = "a" * 40
_CURRENT_REV = "b" * 40
_STATUS_DIGEST = "status-digest-1"
_NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Constructed input builders
# ---------------------------------------------------------------------------


def _write_baseline(tmp_path, tamper=None):
    """Write one baseline artifact JSON; ``tamper`` corrupts section digests."""

    document = {
        "schema_version": 1,
        "task": "0.1 capture baseline",
        "captured_at": "2026-08-03T11:00:00+00:00",
        "repository": {
            "git_revision": _CAPTURED_REV,
            "branch": "main",
            "worktree_status_digest": _STATUS_DIGEST,
        },
        "runs": [{"run_id": "run-1"}],
        "recommendations": [{"stock": "000001.SZ"}],
    }
    digests = {section: _digest(document[section]) for section in ("runs", "recommendations")}
    if tamper == "wrong_digest":
        digests["runs"] = "0" * 64
    elif tamper == "empty_declared":
        digests = {}
    document["section_digests"] = digests
    if tamper == "mutated_section":
        # Digest was computed over the original section; content drifted after.
        document["runs"] = [{"run_id": "run-2"}]
    path = tmp_path / "baseline-task-0.1.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _audit_baseline(path, **overrides):
    kwargs = {
        "expected_task_prefix": "0.1",
        "revision_exists": lambda revision: revision == _CAPTURED_REV,
        "commits_between": lambda start, end: ("commit-1",),
        "current_revision": _CURRENT_REV,
        "current_branch": "main",
        "current_status_digest": _STATUS_DIGEST,
        "first_new_behavior_change_at": None,
        "delta_note": "freeze unit test",
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


def _freeze_kwargs(store, events, baseline_audits):
    return {
        "store": store,
        "events": events,
        "now": _NOW,
        "actor": "test-actor",
        "code_revision": _CURRENT_REV,
        "baseline_audits": baseline_audits,
        "bootstrap_note": {"note": "unit-test bootstrap"},
        "inventory_entries": (
            InventoryEntry(
                path="backend/app/engine/canonical.py",
                workspace_status="present",
                content_sha256="c" * 64,
                decision="REUSE",
                rationale="verified in place",
            ),
            InventoryEntry(
                path="backend/tests/missing.py",
                workspace_status="missing",
                content_sha256=None,
                decision="GAP",
                rationale="not authored yet",
                gap_record_id="g1",
            ),
        ),
        "gap_records": (
            {"gap_id": "g1", "summary": "missing test module", "reproduce": "run pytest"},
        ),
        "scope_entries": _scope_entries(),
    }


@pytest.fixture
def governance_env(tmp_path):
    store = GovernanceObjectStore(tmp_path / "store")
    events = GovernanceEventLog(store)
    return store, events


def _event_types(events):
    return [event.payload["event_type"] for event in events.iter_events()]


# ---------------------------------------------------------------------------
# freeze_s0 success path
# ---------------------------------------------------------------------------


class TestFreezeS0Success:
    def test_receipt_and_event_order(self, governance_env, tmp_path):
        store, events = governance_env
        audit = _audit_baseline(_write_baseline(tmp_path))

        receipt = freeze_s0(**_freeze_kwargs(store, events, (audit,)))

        assert _event_types(events) == [
            "GOVERNANCE_BOOTSTRAP",
            "BASELINE_AUDIT_VERIFIED",
            "AUDIT_INVENTORY_FROZEN",
            "REGISTRY_FROZEN",
            "RELEASE_SCOPE_FROZEN",
        ]

        ordered = list(events.iter_events())
        assert receipt.bootstrap_event_id == ordered[0].object_id
        assert receipt.barrier_event_id == ordered[1].object_id

        # Every receipt reference resolves to a stored, re-verified object.
        baseline = store.get(receipt.baseline_object_ids[0])
        assert baseline.kind == BASELINE_ARTIFACT_KIND
        verification = store.get(receipt.baseline_audit_object_ids[0])
        assert verification.kind == BASELINE_VERIFICATION_KIND
        assert verification.payload["reusable_as_canonical_baseline"] is True
        assert receipt.baseline_object_ids[0] in verification.referenced_object_ids

        assert len(receipt.gap_record_object_ids) == 1
        inventory = store.get(receipt.inventory_object_id)
        assert inventory.payload["gap_record_object_ids"] == list(receipt.gap_record_object_ids)
        assert len(inventory.payload["entries"]) == 2

        registry = store.get(receipt.registry_snapshot_id)
        assert len(registry.payload["feature_flags"]) == 6
        assert len(registry.payload["operations"]) == 9

        scope = store.get(receipt.release_scope_manifest_id)
        assert len(scope.payload["entries"]) == 9
        assert scope.payload["registry_snapshot_id"] == receipt.registry_snapshot_id
        assert scope.payload["behavior_change_barrier_event_id"] == receipt.barrier_event_id


# ---------------------------------------------------------------------------
# Unreusable baseline keeps S0 blocked
# ---------------------------------------------------------------------------


class TestFreezeS0UnprovenBaseline:
    def test_unreusable_baseline_raises_and_records_event(self, governance_env, tmp_path):
        store, events = governance_env
        baseline_path = _write_baseline(tmp_path, tamper="wrong_digest")
        result, document = _audit_baseline(baseline_path)
        assert result.reusable_as_canonical_baseline is False

        with pytest.raises(GovernanceError) as caught:
            freeze_s0(**_freeze_kwargs(store, events, ((result, document),)))
        assert caught.value.code == "baseline_freeze_order_unproven"

        assert _event_types(events) == [
            "GOVERNANCE_BOOTSTRAP",
            "BASELINE_FREEZE_ORDER_UNPROVEN",
        ]
        assert events.first_event_of_type("RELEASE_SCOPE_FROZEN") is None
        assert events.first_event_of_type("BASELINE_AUDIT_VERIFIED") is None

        unproven = events.first_event_of_type("BASELINE_FREEZE_ORDER_UNPROVEN")
        assert unproven.payload["payload"]["failed_artifacts"] == [str(baseline_path)]


# ---------------------------------------------------------------------------
# _put_once idempotency
# ---------------------------------------------------------------------------


class TestPutOnce:
    def test_identical_payload_reuses_stored_object(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        first, created_first = _put_once(
            store,
            kind="audit_inventory",
            payload={"entries": []},
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_CURRENT_REV,
        )
        second, created_second = _put_once(
            store,
            kind="audit_inventory",
            payload={"entries": []},
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_CURRENT_REV,
        )
        assert created_first is True
        assert created_second is False
        assert second.object_id == first.object_id
        assert len(list(store.iter_kind("audit_inventory"))) == 1

    def test_different_payload_creates_new_object(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        first, created_first = _put_once(
            store,
            kind="audit_inventory",
            payload={"entries": []},
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_CURRENT_REV,
        )
        second, created_second = _put_once(
            store,
            kind="audit_inventory",
            payload={"entries": [{"path": "x"}]},
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_CURRENT_REV,
        )
        assert created_first is True
        assert created_second is True
        assert second.object_id != first.object_id
        assert len(list(store.iter_kind("audit_inventory"))) == 2


# ---------------------------------------------------------------------------
# Empty scope manifest is refused
# ---------------------------------------------------------------------------


class TestFreezeS0EmptyScope:
    def test_empty_scope_entries_refused(self, governance_env, tmp_path):
        store, events = governance_env
        audit = _audit_baseline(_write_baseline(tmp_path))
        kwargs = _freeze_kwargs(store, events, (audit,))
        kwargs["scope_entries"] = ()

        with pytest.raises(GovernanceError) as caught:
            freeze_s0(**kwargs)
        assert caught.value.code == "scope_manifest_required"
        assert events.first_event_of_type("RELEASE_SCOPE_FROZEN") is None


# ---------------------------------------------------------------------------
# audit_baseline_artifact section digest verification
# ---------------------------------------------------------------------------


class TestSectionDigestVerification:
    @pytest.mark.parametrize("tamper", ["wrong_digest", "empty_declared", "mutated_section"])
    def test_tampered_section_digest_not_reusable(self, tmp_path, tamper):
        result, _document = _audit_baseline(_write_baseline(tmp_path, tamper=tamper))
        assert result.section_digests_match is False
        assert result.reusable_as_canonical_baseline is False

    def test_untampered_baseline_reusable(self, tmp_path):
        result, _document = _audit_baseline(_write_baseline(tmp_path))
        assert result.section_digests_match is True
        assert result.revision_exists_in_history is True
        assert result.schema_version_ok is True
        assert result.task_matches is True
        assert result.freeze_order_provable is True
        assert result.reusable_as_canonical_baseline is True
        assert result.commits_since_capture == ("commit-1",)

    def test_unknown_captured_revision_not_reusable(self, tmp_path):
        result, _document = _audit_baseline(
            _write_baseline(tmp_path),
            revision_exists=lambda revision: False,
        )
        assert result.revision_exists_in_history is False
        assert result.reusable_as_canonical_baseline is False
        # Revision不可证明时不再计算 commits 区间。
        assert result.commits_since_capture == ()

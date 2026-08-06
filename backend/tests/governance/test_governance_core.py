"""Layer 1 contract tests for the governance skeleton (envelope/store/events/state/binding)."""
from __future__ import annotations

from datetime import datetime, timezone
import multiprocessing

import pytest

from app.governance import (
    CodeState,
    DependencyDigest,
    DependencyDigestKind,
    EnvironmentAcceptanceState,
    GovernanceError,
    GovernanceEventLog,
    GovernanceObjectStore,
    ImmutableEnvelope,
    OperationExecutionState,
    ProductionActivationState,
    RevisionBinding,
    RevisionBindingStatus,
    digest_file,
)
from app.governance.state import (
    apply_layer1_environment_result,
    excluded_operation_terminal_state,
    transition_activation_state,
    transition_code_state,
    transition_environment_state,
    transition_included_operation_state,
)

_NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 8, 3, 13, 0, 0, tzinfo=timezone.utc)
_REV = "a" * 40


def _put(store, kind="baseline_artifact", payload=None, **kwargs):
    return store.put(
        kind=kind,
        payload=payload if payload is not None else {"n": 1},
        created_at=kwargs.pop("created_at", _NOW),
        created_by=kwargs.pop("created_by", "test-actor"),
        code_revision=kwargs.pop("code_revision", _REV),
        **kwargs,
    )


class TestEnvelope:
    def test_identity_is_deterministic_and_content_addressed(self):
        first = _put(GovernanceObjectStore(_tmp()))
        second = ImmutableEnvelope(
            kind="baseline_artifact",
            payload={"n": 1},
            created_at=_NOW,
            created_by="test-actor",
            code_revision=_REV,
        )
        assert first.object_id == second.object_id
        assert first.object_id.startswith("baseline_artifact:sha256:")

    def test_payload_change_changes_identity(self):
        store = GovernanceObjectStore(_tmp())
        first = _put(store, payload={"n": 1})
        second = _put(store, payload={"n": 2})
        assert first.object_id != second.object_id

    def test_from_document_rejects_tampered_bytes(self):
        store = GovernanceObjectStore(_tmp())
        envelope = _put(store)
        document = envelope.to_document()
        document["payload"] = {"n": 999}
        with pytest.raises(GovernanceError) as caught:
            ImmutableEnvelope.from_document(document)
        assert caught.value.code in {"object_id_mismatch", "payload_hash_mismatch"}

    def test_naive_timestamp_rejected(self):
        with pytest.raises(GovernanceError):
            ImmutableEnvelope(
                kind="baseline_artifact",
                payload={},
                created_at=datetime(2026, 8, 3),
                created_by="actor",
                code_revision=_REV,
            )


class TestStore:
    def test_put_get_round_trip_and_rewrite_noop(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        envelope = _put(store)
        again = _put(store)
        assert again.object_id == envelope.object_id
        loaded = store.get(envelope.object_id)
        assert loaded.to_document() == envelope.to_document()

    def test_overwrite_with_different_bytes_refused(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        envelope = _put(store)
        path = store.root / "baseline_artifact" / f"{envelope.object_id.rsplit(':', 1)[-1]}.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(GovernanceError) as caught:
            store.get(envelope.object_id)
        assert caught.value.code in {"corrupt_object", "object_id_mismatch", "invalid_document", "invalid_timestamp", "invalid_object_id"}

    def test_evidence_uri_round_trip_and_rehash(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        envelope = _put(store, kind="evidence", payload={"bytes": "deadbeef"})
        uri = store.evidence_uri_for(envelope.object_id)
        resolved = store.resolve_evidence_uri(uri)
        assert resolved.object_id == envelope.object_id

    def test_mutable_evidence_uri_forms_rejected(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        for bad in ("evidence://latest", "file:///tmp/x.json", "https://example.com/log?q=1"):
            with pytest.raises(GovernanceError) as caught:
                store.resolve_evidence_uri(bad)
            assert caught.value.code in {"invalid_evidence_uri", "evidence_uri_unresolvable"}

    def test_supersede_chain_preserves_history(self, tmp_path):
        store = GovernanceObjectStore(tmp_path)
        first = _put(store, payload={"v": 1})
        second = _put(store, payload={"v": 2}, created_at=_LATER, supersedes_id=first.object_id)
        chain = store.supersede_chain(second.object_id)
        assert [item.object_id for item in chain] == [second.object_id, first.object_id]


class TestEvents:
    def test_append_order_is_preserved(self, tmp_path):
        log = GovernanceEventLog(GovernanceObjectStore(tmp_path))
        log.append(
            event_type="BASELINE_CAPTURE_STARTED",
            actor="tester",
            code_revision=_REV,
            recorded_at=_NOW,
            payload={"step": 1},
        )
        log.append(
            event_type="BASELINE_FROZEN",
            actor="tester",
            code_revision=_REV,
            recorded_at=_LATER,
            payload={"step": 2},
        )
        types = [event.payload["event_type"] for event in log.iter_events()]
        assert types == ["BASELINE_CAPTURE_STARTED", "BASELINE_FROZEN"]
        assert log.first_event_of_type("BASELINE_FROZEN") is not None
        assert log.count() == 2

    def test_multiprocess_duplicate_append_keeps_one_object_and_appends_each_occurrence(self, tmp_path):
        # 事件流是 append-only 审计 spine：同一 object_id 的事件被多次 append
        # （并发重试 / 逻辑上的多次合法发生）每次都留痕，不做 object_id 去重——
        # 去重会丢失审计信息（见 property_s0 的 freeze_s0 重复冻结语义）。
        # 不变量：对象层只持久化一个对象（store.put 内容寻址幂等、拒绝覆盖），
        # index 每次发生各记一行、全部可解析、payload 一致。
        processes = [
            multiprocessing.Process(
                target=_append_event_process,
                args=(str(tmp_path), 1),
            )
            for _ in range(8)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0

        log = GovernanceEventLog(GovernanceObjectStore(tmp_path))
        events = list(log.iter_events())
        # 8 次发生各留一行，全部可读且内容一致
        assert len(events) == 8
        assert all(event.payload["payload"] == {"step": 1} for event in events)
        # 对象层只有一个不可变对象（同一 object_id 幂等，未被覆盖）
        assert len({event.object_id for event in events}) == 1
        stored = list((tmp_path / "governance_event").glob("*.json"))
        assert len(stored) == 1

    def test_multiprocess_distinct_appends_are_complete_and_parseable(self, tmp_path):
        processes = [
            multiprocessing.Process(
                target=_append_event_process,
                args=(str(tmp_path), index),
            )
            for index in range(12)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0

        events = list(GovernanceEventLog(GovernanceObjectStore(tmp_path)).iter_events())
        assert len(events) == 12
        assert {event.payload["payload"]["step"] for event in events} == set(range(12))


class TestStateMachines:
    def test_code_state_round_trip_and_stale_invalidation(self):
        transition_code_state(CodeState.NOT_STARTED, CodeState.IN_PROGRESS)
        transition_code_state(CodeState.IN_PROGRESS, CodeState.CODE_COMPLETE)
        transition_code_state(CodeState.CODE_COMPLETE, CodeState.IN_PROGRESS)
        with pytest.raises(GovernanceError):
            transition_code_state(CodeState.NOT_STARTED, CodeState.CODE_COMPLETE)

    def test_environment_accepted_is_terminal(self):
        transition_environment_state(
            EnvironmentAcceptanceState.NOT_RUN, EnvironmentAcceptanceState.BLOCKED
        )
        transition_environment_state(
            EnvironmentAcceptanceState.BLOCKED, EnvironmentAcceptanceState.ACCEPTED
        )
        with pytest.raises(GovernanceError):
            transition_environment_state(
                EnvironmentAcceptanceState.ACCEPTED, EnvironmentAcceptanceState.FAILED
            )

    def test_layer1_can_never_accept_environment(self):
        assert apply_layer1_environment_result(EnvironmentAcceptanceState.BLOCKED) is EnvironmentAcceptanceState.BLOCKED
        with pytest.raises(GovernanceError) as caught:
            apply_layer1_environment_result(EnvironmentAcceptanceState.ACCEPTED)
        assert caught.value.code == "layer1_cannot_accept"

    def test_excluded_operation_terminal_is_reserved(self):
        assert excluded_operation_terminal_state() is OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED
        with pytest.raises(GovernanceError):
            transition_included_operation_state(
                OperationExecutionState.PLANNED,
                OperationExecutionState.NOT_AUTHORIZED_NOT_EXECUTED,
            )
        transition_included_operation_state(
            OperationExecutionState.PLANNED, OperationExecutionState.NOT_AUTHORIZED
        )
        with pytest.raises(GovernanceError):
            transition_included_operation_state(
                OperationExecutionState.PLANNED, OperationExecutionState.EXECUTED
            )

    def test_activation_requires_authorization_first(self):
        with pytest.raises(GovernanceError):
            transition_activation_state(
                ProductionActivationState.NOT_AUTHORIZED, ProductionActivationState.ACTIVE
            )
        transition_activation_state(
            ProductionActivationState.NOT_AUTHORIZED,
            ProductionActivationState.AUTHORIZED_NOT_ACTIVE,
        )


class TestRevisionBinding:
    def _digest(self, kind, locator, char="a"):
        return DependencyDigest(kind=kind, locator=locator, content_sha256=char * 64)

    def test_digests_sort_by_kind_and_locator(self):
        binding = RevisionBinding(
            scope_id="task_1_3_historical_reconstruction",
            digests=(
                self._digest(DependencyDigestKind.REPLAY, "z.py"),
                self._digest(DependencyDigestKind.EVIDENCE_IDENTITY, "a.py"),
            ),
        )
        assert [d.kind for d in binding.digests] == [
            DependencyDigestKind.EVIDENCE_IDENTITY,
            DependencyDigestKind.REPLAY,
        ]

    def test_duplicate_locator_rejected(self):
        with pytest.raises(GovernanceError):
            RevisionBinding(
                scope_id="s",
                digests=(
                    self._digest(DependencyDigestKind.REPLAY, "a.py"),
                    self._digest(DependencyDigestKind.REPLAY, "a.py", "b"),
                ),
            )

    def test_verify_current_and_stale(self):
        original = RevisionBinding(
            scope_id="s",
            digests=(self._digest(DependencyDigestKind.REPLAY, "a.py"),),
        )
        same = RevisionBinding(
            scope_id="s",
            digests=(self._digest(DependencyDigestKind.REPLAY, "a.py"),),
        )
        changed = RevisionBinding(
            scope_id="s",
            digests=(self._digest(DependencyDigestKind.REPLAY, "a.py", "c"),),
        )
        assert original.verify(same) is RevisionBindingStatus.CURRENT
        assert original.verify(changed) is RevisionBindingStatus.STALE
        assert original.stale_members(changed)[0].content_sha256 == "a" * 64

    def test_required_kinds_enforced(self):
        binding = RevisionBinding(
            scope_id="s",
            digests=(self._digest(DependencyDigestKind.REPLAY, "a.py"),),
        )
        with pytest.raises(GovernanceError) as caught:
            binding.require_kinds([DependencyDigestKind.REPLAY, DependencyDigestKind.LIVE_SELECTOR])
        assert caught.value.code == "binding_scope_incomplete"

    def test_digest_file_hashes_bytes(self, tmp_path):
        target = tmp_path / "dep.py"
        target.write_text("x = 1\n", encoding="utf-8")
        digest = digest_file(target)
        assert len(digest) == 64
        with pytest.raises(GovernanceError):
            digest_file(tmp_path / "missing.py")


def _append_event_process(root: str, step: int) -> None:
    GovernanceEventLog(GovernanceObjectStore(root)).append(
        event_type="CONCURRENT_EVENT",
        actor="process-tester",
        code_revision=_REV,
        recorded_at=_NOW,
        payload={"step": step},
    )


def _tmp():
    import tempfile

    return tempfile.mkdtemp(prefix="governance-test-")

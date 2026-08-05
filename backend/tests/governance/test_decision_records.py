"""P2-5 architecture decision record contracts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import multiprocessing

import pytest

from app.governance import GovernanceError, GovernanceObjectStore
from app.services.decision_records import (
    ArchitectureDecisionPayload,
    BehaviorActivationState,
    DecisionRecordService,
    DecisionStatus,
)


_NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 8, 5, 13, 0, tzinfo=timezone.utc)
_REV = "a" * 40


def _payload(key: str = "p2-5") -> dict:
    return {
        "decision_key": key,
        "title": "Version decisions in immutable governance objects",
        "status": "accepted",
        "context": "The repository needs a durable, reviewable decision history.",
        "source_references": [{"reference_type": "external", "value": "repo://report.md"}],
        "options": [
            {"name": "Markdown only", "description": "Easy to edit but not authoritative", "disposition": "rejected"},
            {"name": "Immutable object", "description": "Content addressed and append-only", "disposition": "adopted"},
        ],
        "adopted_scope": ["architecture decisions"],
        "rejected_scope": ["editing Markdown as authority"],
        "rationale": ["An immutable envelope makes tampering detectable."],
        "tradeoffs": ["Queries need a small projection index."],
        "evidence_references": [{"reference_type": "external", "value": "repo://requirements.md"}],
        "validations": [{"command": "pytest -q backend/tests/governance/test_decision_records.py", "status": "passed", "summary": "P2-5 contract tests pass."}],
        "risks": [{"risk": "Index corruption", "mitigation": "Re-verify every object from the immutable store.", "severity": "medium"}],
        "next_state": "Keep behavior activation out of the production recommendation path.",
        "related_requirement_ids": ["R-DECISION-1", "8.5"],
        "behavior_activation_state": "not_activated",
    }


def _service(root):
    return DecisionRecordService(GovernanceObjectStore(root))


def _create_process(root: str, payload: dict) -> None:
    _service(root).create(payload, actor="worker", code_revision=_REV, created_at=_NOW)


def test_accepted_requires_rationale_validation_and_evidence():
    payload = _payload()
    for field in ("rationale", "validations", "evidence_references"):
        invalid = _payload()
        invalid[field] = []
        with pytest.raises(GovernanceError) as caught:
            ArchitectureDecisionPayload.from_value(invalid)
        assert caught.value.code in {
            "decision_rationale_required",
            "decision_evidence_required",
            "decision_validation_required",
        }


def test_create_is_immutable_and_same_payload_idempotent(tmp_path):
    service = _service(tmp_path)
    first = service.create(_payload(), actor="tester", code_revision=_REV, created_at=_NOW)
    again = service.create(_payload(), actor="another", code_revision="b" * 40, created_at=_LATER)
    assert again.object_id == first.object_id
    assert service.show(first.object_id).to_document() == first.to_document()
    assert len(service.list(decision_key="p2-5")) == 1

    different = _payload()
    different["rationale"] = ["A different rationale is a revision, not a retry."]
    with pytest.raises(GovernanceError) as caught:
        service.create(different, actor="tester", code_revision=_REV, created_at=_LATER)
    assert caught.value.code == "decision_identity_conflict"


def test_supersede_requires_same_kind_and_key_and_keeps_history(tmp_path):
    service = _service(tmp_path)
    first = service.create(_payload(), actor="tester", code_revision=_REV, created_at=_NOW)
    revised = _payload()
    revised["title"] = "Versioned architecture decisions"
    second = service.supersede(first.object_id, revised, actor="tester", code_revision=_REV, created_at=_LATER)
    same_second = service.supersede(first.object_id, revised, actor="other", code_revision="b" * 40, created_at=_LATER)
    assert same_second.object_id == second.object_id
    assert second.supersedes_id == first.object_id
    assert [item.object_id for item in service.store.supersede_chain(second.object_id)] == [second.object_id, first.object_id]
    assert len(service.list(decision_key="p2-5")) == 2

    with pytest.raises(GovernanceError) as caught:
        service.create(_payload(), actor="tester", code_revision=_REV, created_at=_LATER)
    assert caught.value.code == "decision_identity_conflict"

    wrong_key = _payload("other")
    with pytest.raises(GovernanceError) as caught:
        service.supersede(first.object_id, wrong_key, actor="tester", code_revision=_REV, created_at=_LATER)
    assert caught.value.code == "decision_key_mismatch"


def test_object_and_evidence_references_are_resolved(tmp_path):
    store = GovernanceObjectStore(tmp_path)
    evidence = store.put(kind="evidence", payload={"fact": "stored"}, created_at=_NOW, created_by="fixture", code_revision=_REV)
    service = DecisionRecordService(store)
    payload = _payload()
    payload["evidence_references"] = [store.evidence_uri_for(evidence.object_id), evidence.object_id]
    created = service.create(payload, actor="tester", code_revision=_REV, created_at=_NOW)
    assert set(created.referenced_object_ids) == {evidence.object_id}
    assert service.verify(created.object_id).object_id == created.object_id

    bad = _payload("bad-reference")
    bad["evidence_references"] = ["evidence://sha256/" + "0" * 64]
    with pytest.raises(GovernanceError) as caught:
        service.create(bad, actor="tester", code_revision=_REV, created_at=_NOW)
    assert caught.value.code == "evidence_uri_unresolvable"


def test_markdown_is_a_projection_and_index_tampering_fails_closed(tmp_path):
    service = _service(tmp_path)
    created = service.create(_payload(), actor="tester", code_revision=_REV, created_at=_NOW)
    markdown = service.render_markdown(created.object_id)
    assert "# Version decisions in immutable governance objects" in markdown
    assert created.object_id in markdown

    index = service.index_path
    entry = json.loads(index.read_text(encoding="utf-8"))
    entry["status"] = "retired"
    index.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    with pytest.raises(GovernanceError) as caught:
        service.list()
    assert caught.value.code == "corrupt_decision_index"


def test_index_append_is_cross_process_safe_for_duplicate_identity(tmp_path):
    payload = _payload("concurrent")
    processes = [multiprocessing.Process(target=_create_process, args=(str(tmp_path), payload)) for _ in range(8)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    service = _service(tmp_path)
    assert len(service.list(decision_key="concurrent")) == 1
    assert len(service.index_path.read_text(encoding="utf-8").splitlines()) == 1

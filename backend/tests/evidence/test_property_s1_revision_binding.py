"""Hypothesis properties 45–46 for Task 1.3 revision binding.

These are Layer 1 model tests.  Generated Layer 2 evidence objects are in-memory
facts only and never claim acceptance for the real environment.
"""
from __future__ import annotations

import hashlib

from hypothesis import given, settings, strategies as st

from app.governance.binding import (
    TASK_1_3_BINDING_SCOPE,
    TASK_1_3_LAYER2_EVIDENCE_KINDS,
    TASK_1_3_REQUIRED_KINDS,
    DependencyDigest,
    RevisionBinding,
    RevisionBindingStatus,
    Task13Layer2Evidence,
    evaluate_task_1_3_completion,
)


def _binding(hashes: tuple[str, ...]) -> RevisionBinding:
    return RevisionBinding(
        scope_id=TASK_1_3_BINDING_SCOPE,
        digests=tuple(
            DependencyDigest(
                kind=kind,
                locator=f"backend/dependency-{index}.py",
                content_sha256=digest,
            )
            for index, (kind, digest) in enumerate(
                zip(TASK_1_3_REQUIRED_KINDS, hashes, strict=True)
            )
        ),
    )


_HASHES = st.tuples(
    *[
        st.binary(min_size=1, max_size=40).map(
            lambda value: hashlib.sha256(value).hexdigest()
        )
        for _ in TASK_1_3_REQUIRED_KINDS
    ]
)


@settings(max_examples=100, deadline=None)
@given(hashes=_HASHES, changed_index=st.integers(min_value=0, max_value=5), salt=st.binary(min_size=1, max_size=20))
def test_property_45_task_1_3_code_completion_is_revision_bound_and_invalidated_exactly(
    hashes, changed_index, salt
):
    """**Property 45: Task 1.3 code completion is revision-bound and invalidated exactly**"""
    original = _binding(hashes)
    same = _binding(tuple(hashes))
    assert original.verify(same) is RevisionBindingStatus.CURRENT
    current_decision = evaluate_task_1_3_completion(
        bound_revision=original,
        current_revision=same,
        layer1_passed=True,
        correctness_proofs_passed=True,
    )
    assert current_decision.code_state == "CODE_COMPLETE"
    assert current_decision.environment_state == "BLOCKED"

    changed_hashes = list(hashes)
    replacement = hashlib.sha256(
        hashes[changed_index].encode("ascii") + salt
    ).hexdigest()
    changed_hashes[changed_index] = replacement
    changed = _binding(tuple(changed_hashes))
    assert original.verify(changed) is RevisionBindingStatus.STALE
    stale = original.stale_members(changed)
    assert len(stale) == 1
    assert stale[0].kind is TASK_1_3_REQUIRED_KINDS[changed_index]
    invalidated = evaluate_task_1_3_completion(
        bound_revision=original,
        current_revision=changed,
        layer1_passed=True,
        correctness_proofs_passed=True,
    )
    assert invalidated.code_state == "IN_PROGRESS"
    assert invalidated.environment_state == "BLOCKED"
    assert invalidated.task_complete is False
    assert invalidated.reason_codes == ("REVISION_BINDING_STALE",)


@settings(max_examples=100, deadline=None)
@given(
    hashes=_HASHES,
    present=st.sets(
        st.sampled_from(TASK_1_3_LAYER2_EVIDENCE_KINDS),
        max_size=len(TASK_1_3_LAYER2_EVIDENCE_KINDS) - 1,
    ),
)
def test_property_46_task_1_3_remains_incomplete_without_all_revision_matched_layer2_evidence(
    hashes, present
):
    """**Property 46: Task 1.3 remains incomplete without all revision-matched Layer 2 evidence**"""
    binding = _binding(hashes)
    evidence = tuple(
        Task13Layer2Evidence(
            evidence_kind=kind,
            result="PASS",
            revision_binding_hash=binding.binding_hash,
            environment_identity="real-env-property-46",
        )
        for kind in sorted(present)
    )
    decision = evaluate_task_1_3_completion(
        bound_revision=binding,
        current_revision=binding,
        layer1_passed=True,
        correctness_proofs_passed=True,
        layer2_evidence=evidence,
    )
    assert decision.code_state == "CODE_COMPLETE"
    assert decision.environment_state == "BLOCKED"
    assert decision.task_complete is False
    missing = set(TASK_1_3_LAYER2_EVIDENCE_KINDS) - set(present)
    assert {
        reason.removeprefix("LAYER_2_EVIDENCE_MISSING:")
        for reason in decision.reason_codes
        if reason.startswith("LAYER_2_EVIDENCE_MISSING:")
    } == missing


def test_all_four_matching_layer2_facts_are_required_by_the_model():
    binding = _binding(tuple(hashlib.sha256(bytes([index])).hexdigest() for index in range(6)))
    evidence = tuple(
        Task13Layer2Evidence(
            evidence_kind=kind,
            result="PASS",
            revision_binding_hash=binding.binding_hash,
            environment_identity="real-env-example-only",
        )
        for kind in TASK_1_3_LAYER2_EVIDENCE_KINDS
    )
    decision = evaluate_task_1_3_completion(
        bound_revision=binding,
        current_revision=binding,
        layer1_passed=True,
        correctness_proofs_passed=True,
        layer2_evidence=evidence,
    )
    assert decision.task_complete is True
    assert decision.environment_state == "ACCEPTED"

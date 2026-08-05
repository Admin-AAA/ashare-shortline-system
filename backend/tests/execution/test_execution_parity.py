"""S2-d execution parity and preregistered shadow thresholds.

**Validates: Requirements 3.4, 3.8, 10.22–10.23, 13.1**
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.governance.manifests import SHADOW_THRESHOLD_KIND, validate_manifest_payload
from app.governance.store import GovernanceObjectStore
from app.services.execution_parity import (
    ShadowParityVerdict,
    compare_execution_payloads,
    evaluate_shadow_threshold_manifest,
)
from scripts.freeze_wave1_shadow_thresholds import (
    STABLE_MANIFEST_KEY,
    freeze_shadow_threshold_manifest,
)
from testkit import TPlusOneExecutionAdapter, load_golden_fixture


FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "production_hardening_golden_v1.json"
)


def _frozen_manifest(tmp_path):
    store = GovernanceObjectStore(tmp_path / "store")
    manifest, created = freeze_shadow_threshold_manifest(
        store,
        now=datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
        code_revision="test-revision-s2d",
    )
    return store, manifest, created


def _golden_observations():
    data = load_golden_fixture(FIXTURE_PATH)
    adapter = TPlusOneExecutionAdapter()
    observations = []
    for recommendation in data.recommendations():
        payload = adapter.run(
            recommendation,
            data.bars_for(recommendation),
            data.execution_policy,
        ).canonical_payload()
        observations.append(
            compare_execution_payloads(
                recommendation_identity=recommendation.recommendation_id or recommendation.code,
                source_snapshot_id=data.snapshot.snapshot_id,
                execution_policy_fingerprint=data.execution_policy.fingerprint(),
                code_revision="test-revision-s2d",
                source_adapter_role="legacy_authoritative",
                candidate_adapter_role="canonical_shadow",
                source_payload=payload,
                candidate_payload=payload,
                candidate_t_plus_one=data.execution_policy.t_plus_one,
            )
        )
    return observations


def test_shadow_threshold_manifest_is_frozen_idempotently_before_sampling(tmp_path):
    store, first, created = _frozen_manifest(tmp_path)
    second, created_again = freeze_shadow_threshold_manifest(
        store,
        now=datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc),
        code_revision="different-later-revision",
    )

    assert created is True
    assert created_again is False
    assert first.object_id == second.object_id
    assert first.payload["stable_manifest_key"] == STABLE_MANIFEST_KEY
    assert len(first.payload["rules"]) == 9
    assert all(rule["minimum_sample"] == 100 for rule in first.payload["rules"])
    assert all(rule["failure_action"] == "BLOCK_CUTOVER" for rule in first.payload["rules"])
    validate_manifest_payload(SHADOW_THRESHOLD_KIND, first.payload)
    assert store.resolve_evidence_uri(store.evidence_uri_for(first.object_id)).object_id == first.object_id


def test_exact_trade_parity_passes_all_zero_difference_thresholds(tmp_path):
    _store, manifest, _created = _frozen_manifest(tmp_path)
    base = _golden_observations()
    observations = tuple(base * 20)  # 5 fixture trades × 20 = minimum sample 100

    decision = evaluate_shadow_threshold_manifest(
        manifest_id=manifest.object_id,
        manifest_payload=manifest.payload,
        observations=observations,
    )

    assert decision.sample_count == 100
    assert decision.verdict is ShadowParityVerdict.PASS
    assert decision.blocker_reasons == ()
    assert all(value == 0 for _metric, value in decision.metric_values)
    assert decision.old_projection_remains_authoritative is True
    assert decision.environment_acceptance_state == "BLOCKED"


def test_structured_difference_exceeds_threshold_and_blocks_cutover(tmp_path):
    _store, manifest, _created = _frozen_manifest(tmp_path)
    data = load_golden_fixture(FIXTURE_PATH)
    recommendation = data.recommendations()[0]
    source = TPlusOneExecutionAdapter().run(
        recommendation,
        data.bars_for(recommendation),
        data.execution_policy,
    ).canonical_payload()
    candidate = {**source, "net_return_pct": "99.00000000"}
    mismatch = compare_execution_payloads(
        recommendation_identity=recommendation.recommendation_id or recommendation.code,
        source_snapshot_id=data.snapshot.snapshot_id,
        execution_policy_fingerprint=data.execution_policy.fingerprint(),
        code_revision="test-revision-s2d",
        source_adapter_role="legacy_authoritative",
        candidate_adapter_role="canonical_shadow",
        source_payload=source,
        candidate_payload=candidate,
        candidate_t_plus_one=True,
    )
    matching = _golden_observations()[1]

    assert [difference.field_path for difference in mismatch.differences] == [
        "$.net_return_pct"
    ]
    assert mismatch.differences[0].metric_id == "net_return_mismatch_count"

    decision = evaluate_shadow_threshold_manifest(
        manifest_id=manifest.object_id,
        manifest_payload=manifest.payload,
        observations=(mismatch, *([matching] * 99)),
    )
    assert decision.verdict is ShadowParityVerdict.BLOCKED
    assert any(
        reason.startswith("SHADOW_THRESHOLD_FAILED:net_return_mismatch_count")
        for reason in decision.blocker_reasons
    )
    assert decision.old_projection_remains_authoritative is True


def test_insufficient_sample_and_t1_same_day_exit_both_block(tmp_path):
    _store, manifest, _created = _frozen_manifest(tmp_path)
    base = _golden_observations()
    insufficient = evaluate_shadow_threshold_manifest(
        manifest_id=manifest.object_id,


        manifest_payload=manifest.payload,
        observations=base,
    )
    assert insufficient.verdict is ShadowParityVerdict.BLOCKED
    assert all(reason.startswith("INSUFFICIENT_SAMPLE:") for reason in insufficient.blocker_reasons)

    payload = {
        "entry_triggered": True,
        "entry_date": "2026-06-02",
        "entry_price": "10.0",
        "exit_date": "2026-06-02",
        "exit_price": "10.1",
        "exit_reason": "same_day_forbidden",
        "gross_return_pct": "1.0",
        "cost_pct": "0.07",
        "position_size": "0.25",
        "net_return_pct": "0.93",
    }
    same_day = compare_execution_payloads(
        recommendation_identity="same-day-fixture",
        source_snapshot_id="snapshot-s2d",
        execution_policy_fingerprint="a" * 64,
        code_revision="test-revision-s2d",
        source_adapter_role="legacy_authoritative",
        candidate_adapter_role="canonical_shadow",
        source_payload=payload,
        candidate_payload=payload,
        candidate_t_plus_one=True,
    )
    assert same_day.candidate_t_plus_one_same_day_exit is True
    decision = evaluate_shadow_threshold_manifest(
        manifest_id=manifest.object_id,
        manifest_payload=manifest.payload,
        observations=tuple([same_day] * 100),
    )
    assert decision.verdict is ShadowParityVerdict.BLOCKED
    assert any(
        reason.startswith("SHADOW_THRESHOLD_FAILED:t_plus_one_same_day_exit_count")
        for reason in decision.blocker_reasons
    )


def test_parity_normalizes_legacy_reason_aliases_and_float_representation():
    base = {
        "entry_triggered": True,
        "entry_date": "2026-01-02",
        "entry_price": 10.0,
        "exit_date": "2026-01-03",
        "exit_price": 10.5,
        "exit_reason": "stop_loss",
        "gross_return_pct": 5.0,
        "cost_pct": 0.07,
        "position_size": 0.25,
        "net_return_pct": 4.93,
    }
    candidate = {**base, "exit_reason": "stop_loss_hit", "cost_pct": 0.06999999999999999}

    observation = compare_execution_payloads(
        recommendation_identity="parity-normalization",
        source_snapshot_id="snapshot-normalization",
        execution_policy_fingerprint="a" * 64,
        code_revision="revision-normalization",
        source_adapter_role="legacy",
        candidate_adapter_role="canonical",
        source_payload=base,
        candidate_payload=candidate,
        candidate_t_plus_one=True,
    )

    assert observation.differences == ()
def test_repository_manifest_frozen_before_this_parity_suite_is_valid():
    store = GovernanceObjectStore()
    manifests = [
        item
        for item in store.iter_kind(SHADOW_THRESHOLD_KIND)
        if item.payload.get("stable_manifest_key") == STABLE_MANIFEST_KEY
    ]
    assert len(manifests) == 1
    validate_manifest_payload(SHADOW_THRESHOLD_KIND, manifests[0].payload)

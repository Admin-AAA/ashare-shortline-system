from __future__ import annotations

from dataclasses import replace
import json

import pytest

from app.domain.factor_analysis import FactorAnalysisRequest
from app.engine.canonical import canonical_fingerprint, canonical_json
from app.services.analysis.factor_observation_repository import (
    FactorObservationRecord,
    FactorObservationRepositoryError,
    map_factor_observation,
)
from app.services.quality_outcome import (
    OUTCOME_CALCULATION_SCHEMA_VERSION,
    OutcomeCalculationIdentity,
)


def _request(**overrides) -> FactorAnalysisRequest:
    values = {
        "factor_name": "avg_cost",
        "start_date": "2026-07-01",
        "end_date": "2026-07-31",
        "horizon_days": 5,
        "quantiles": 2,
        "min_cross_section_size": 2,
    }
    values.update(overrides)
    return FactorAnalysisRequest(**values)


def _record(**overrides) -> FactorObservationRecord:
    result = {"net_return_pct": 2.5, "outcome_label": "success"}
    identity = OutcomeCalculationIdentity(
        recommendation_id="rec_1",
        recommendation_identity_hash="1" * 64,
        market_snapshot_id="snapshot_1",
        market_artifact_hash="2" * 64,
        execution_policy_fingerprint="3" * 64,
        calculation_code_revision="revision_1",
        label_policy_fingerprint="4" * 64,
    )
    values = {
        "trade_date": "2026-07-10",
        "security_code": "000001",
        "recommendation_id": "rec_1",
        "feature_snapshot_json": json.dumps({"avg_cost": 10.5}),
        "run_evidence_identity_hash": "5" * 64,
        "outcome_label": "success",
        "outcome_horizon_days": 5,
        "calculation_version_id": f"outv_{identity.calculation_fingerprint}",
        "current_calculation_fingerprint": identity.calculation_fingerprint,
        "projection_revision": 1,
        "projected_at_present": True,
        "version_id": f"outv_{identity.calculation_fingerprint}",
        "version_recommendation_id": "rec_1",
        "version_horizon_days": 5,
        "result_json": canonical_json(result),
        "calculation_fingerprint": identity.calculation_fingerprint,
        "result_hash": canonical_fingerprint(result),
        "recommendation_identity_hash": identity.recommendation_identity_hash,
        "market_snapshot_id": identity.market_snapshot_id,
        "market_artifact_hash": identity.market_artifact_hash,
        "execution_policy_fingerprint": identity.execution_policy_fingerprint,
        "calculation_code_revision": identity.calculation_code_revision,
        "label_policy_fingerprint": identity.label_policy_fingerprint,
        "calculation_schema_version": OUTCOME_CALCULATION_SCHEMA_VERSION,
        "validity_status": "VALID",
    }
    values.update(overrides)
    return FactorObservationRecord(**values)


def test_maps_only_explicit_canonical_net_return_and_provenance():
    observation = map_factor_observation(_request(), _record())

    assert observation is not None
    assert observation.factor_value == pytest.approx(10.5)
    assert observation.future_return_pct == pytest.approx(2.5)
    assert observation.outcome_identity is not None
    assert len(observation.source_artifact_ids) == 3


def test_missing_factor_is_preserved_for_coverage_without_derivation():
    observation = map_factor_observation(
        _request(),
        _record(feature_snapshot_json=json.dumps({"price": 10.5})),
    )

    assert observation is not None
    assert observation.factor_value is None


@pytest.mark.parametrize("label", [None, "pending", "provisional", "missed", "invalid"])
def test_unsettled_or_invalid_outcomes_are_excluded(label):
    assert map_factor_observation(_request(), _record(outcome_label=label)) is None


def test_never_falls_back_to_executed_return():
    payload = {"executed_return": 2.5, "outcome_label": "success"}
    row = _record(
        result_json=canonical_json(payload),
        result_hash=canonical_fingerprint(payload),
    )

    with pytest.raises(FactorObservationRepositoryError, match="net_return_pct"):
        map_factor_observation(_request(), row)


@pytest.mark.parametrize(
    "override",
    [
        {"validity_status": "INVALID"},
        {"calculation_schema_version": None},
        {"projection_revision": None},
        {"projected_at_present": False},
    ],
)
def test_invalid_or_identity_incomplete_closed_outcome_is_excluded(override):
    assert map_factor_observation(_request(), replace(_record(), **override)) is None


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"current_calculation_fingerprint": "f" * 64}, "projection"),
        ({"result_hash": "e" * 64}, "result hash"),
    ],
)
def test_conflicting_closed_outcome_fails_closed(override, message):
    with pytest.raises(FactorObservationRepositoryError, match=message):
        map_factor_observation(_request(), replace(_record(), **override))


def test_malformed_or_non_numeric_factor_fails_closed():
    with pytest.raises(FactorObservationRepositoryError, match="feature snapshot"):
        map_factor_observation(_request(), _record(feature_snapshot_json="not-json"))

    with pytest.raises(FactorObservationRepositoryError, match="numeric"):
        map_factor_observation(
            _request(),
            _record(feature_snapshot_json=json.dumps({"avg_cost": "10.5"})),
        )


def test_unknown_factor_field_is_rejected_by_allowlist():
    with pytest.raises(FactorObservationRepositoryError, match="allowlisted"):
        map_factor_observation(_request(factor_name="private_field"), _record())

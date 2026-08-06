from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.calculation import CalculationIdentity, CalculationIdentityError
from app.domain.factor_analysis import (
    CrossSectionalObservation,
    FactorAnalysisRequest,
    evaluate_factor_cross_section,
)
from app.governance import GovernanceObjectStore
from app.services.analysis.factor_analysis_service import (
    FACTOR_ANALYSIS_COMPLETED_EVENT,
    FACTOR_ANALYSIS_KIND,
    FactorAnalysisService,
    FactorAnalysisServiceError,
)


_NOW = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)


def _request() -> FactorAnalysisRequest:
    return FactorAnalysisRequest(
        factor_name="avg_cost",
        start_date="2026-07-01",
        end_date="2026-07-31",
        horizon_days=5,
        quantiles=2,
        min_cross_section_size=2,
    )


def _observations() -> tuple[CrossSectionalObservation, ...]:
    return tuple(
        CrossSectionalObservation(
            trade_date="2026-07-10",
            security_code=f"00000{index}",
            factor_name="avg_cost",
            factor_value=float(index),
            future_return_pct=float(index),
            universe_scope="selected",
            evaluation_scope="live_forward",
            recommendation_id=f"rec_{index}",
            outcome_identity=str(index) * 64,
            source_artifact_ids=(str(index + 4) * 64,),
        )
        for index in range(1, 5)
    )


def test_calculation_identity_sorts_inputs_and_excludes_envelope_metadata():
    first = CalculationIdentity(
        calculation_kind="factor_analysis",
        schema_version=1,
        request_fingerprint="a" * 64,
        input_artifact_fingerprints=("c" * 64, "b" * 64),
        code_revision="revision",
    )
    second = CalculationIdentity(
        calculation_kind="factor_analysis",
        schema_version=1,
        request_fingerprint="a" * 64,
        input_artifact_fingerprints=("b" * 64, "c" * 64),
        code_revision="revision",
    )

    assert first == second
    assert first.calculation_fingerprint == second.calculation_fingerprint


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_fingerprint": "A" * 64},
        {"input_artifact_fingerprints": ()},
        {"input_artifact_fingerprints": ("b" * 64, "b" * 64)},
        {"schema_version": 0},
        {"code_revision": ""},
    ],
)
def test_calculation_identity_rejects_incomplete_or_ambiguous_inputs(overrides):
    values = {
        "calculation_kind": "factor_analysis",
        "schema_version": 1,
        "request_fingerprint": "a" * 64,
        "input_artifact_fingerprints": ("b" * 64,),
        "code_revision": "revision",
    }
    values.update(overrides)
    with pytest.raises(CalculationIdentityError):
        CalculationIdentity(**values)


def test_factor_analysis_service_persists_and_idempotently_reuses_object_and_event(tmp_path):
    store = GovernanceObjectStore(tmp_path)
    service = FactorAnalysisService(store)

    first = service.analyze(
        _request(),
        _observations(),
        actor="actor-a",
        code_revision="revision-1",
        recorded_at=_NOW,
    )
    second = service.analyze(
        _request(),
        tuple(reversed(_observations())),
        actor="actor-b",
        code_revision="revision-1",
        recorded_at=_NOW + timedelta(hours=1),
    )

    assert first.reused is False
    assert second.reused is True
    assert second.object_id == first.object_id
    assert second.event_object_id == first.event_object_id
    assert store.resolve_evidence_uri(first.evidence_uri).object_id == first.object_id
    assert len(list(store.iter_kind(FACTOR_ANALYSIS_KIND))) == 1
    events = list(service._events.iter_events())
    assert len(events) == 1
    assert events[0].payload["event_type"] == FACTOR_ANALYSIS_COMPLETED_EVENT


def test_same_calculation_identity_with_conflicting_result_fails_closed(tmp_path):
    store = GovernanceObjectStore(tmp_path)
    service = FactorAnalysisService(store)
    request = _request()
    observations = _observations()
    result = evaluate_factor_cross_section(request, observations)
    identity = CalculationIdentity(
        calculation_kind=FACTOR_ANALYSIS_KIND,
        schema_version=result.schema_version,
        request_fingerprint=result.request_fingerprint,
        input_artifact_fingerprints=(result.observation_fingerprint,),
        code_revision="revision-1",
    )
    conflicting_result = replace(result, result_hash="")
    conflicting_payload = {
        "calculation_fingerprint": identity.calculation_fingerprint,
        "calculation_identity": identity.canonical_payload(),
        "request": request.canonical_payload(),
        "result": {
            **conflicting_result.canonical_payload(),
            "result_hash": "f" * 64,
        },
        "source_artifact_ids": sorted(
            {
                source_id
                for observation in observations
                for source_id in observation.source_artifact_ids
            }
        ),
    }
    store.put(
        kind=FACTOR_ANALYSIS_KIND,
        payload=conflicting_payload,
        created_at=_NOW,
        created_by="conflicting-writer",
        code_revision="revision-1",
    )

    with pytest.raises(FactorAnalysisServiceError, match="conflicts"):
        service.analyze(
            request,
            observations,
            actor="actor",
            code_revision="revision-1",
            recorded_at=_NOW,
        )

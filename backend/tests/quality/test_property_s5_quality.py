"""S5 Layer 1 properties for data quality and outcome provenance.

All state is immutable in-memory test data.  The concurrency example uses an
isolated lock-backed model of the existing unique-fingerprint append contract;
no real database, migration, network, or production projection is touched.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from hypothesis import given, settings, strategies as st
import pytest

from app.engine.canonical import canonical_fingerprint
from app.services.quality_outcome import (
    AuxiliaryImpact,
    CurrentProjection,
    DataQualityStatus,
    DatasetObservation,
    DatasetRequirement,
    OutcomeAuditKind,
    OutcomeCalculationIdentity,
    OutcomeFingerprintConflict,
    OutcomeRecomputeCandidate,
    OutcomeVersionRecord,
    StaleProjectionWrite,
    StrategyDataContract,
    audit_outcome_history,
    cas_current,
    decide_quality,
    select_stale_outcomes,
    upsert_outcome_version,
)


_NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
_HEX = tuple(character * 64 for character in "abcdef0123456789")
_JSON_SCALAR = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-1000, 1000),
    st.text(max_size=20),
    st.floats(
        min_value=-1000,
        max_value=1000,
        allow_nan=False,
        allow_infinity=False,
    ),
)
_JSON_VALUE = st.recursive(
    _JSON_SCALAR,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4),
    ),
    max_leaves=8,
)


def _quality_contract() -> StrategyDataContract:
    return StrategyDataContract(
        strategy_id="quality-property",
        requirements=(
            DatasetRequirement(
                "daily_kbar",
                required=True,
                minimum_coverage=0.95,
                max_age_seconds=3600,
            ),
            DatasetRequirement(
                "capital_flow",
                required=False,
                minimum_coverage=0.5,
                max_age_seconds=7200,
                auxiliary=True,
            ),
        ),
        minimum_auxiliary_counterfactual_samples=20,
    )


def _identity(
    *,
    recommendation_id: str = "rec-s5",
    recommendation_hash: str = _HEX[0],
    snapshot_id: str = "snapshot-s5",
    artifact_hash: str = _HEX[1],
    execution_hash: str = _HEX[2],
    code_revision: str = "1" * 40,
    label_hash: str = _HEX[3],
) -> OutcomeCalculationIdentity:
    return OutcomeCalculationIdentity(
        recommendation_id=recommendation_id,
        recommendation_identity_hash=recommendation_hash,
        market_snapshot_id=snapshot_id,
        market_artifact_hash=artifact_hash,
        execution_policy_fingerprint=execution_hash,
        calculation_code_revision=code_revision,
        label_policy_fingerprint=label_hash,
    )


def _append(
    identity: OutcomeCalculationIdentity,
    result: Any = None,
    versions: tuple[OutcomeVersionRecord, ...] = (),
):
    return upsert_outcome_version(
        versions,
        identity,
        {"label": "success", "return": 1.25} if result is None else result,
        computed_at=_NOW,
    )


@settings(max_examples=100, deadline=None)
@given(
    required_missing=st.booleans(),
    required_stale=st.booleans(),
    optional_missing=st.booleans(),
    synthetic=st.booleans(),
    counterfactual_samples=st.integers(min_value=0, max_value=40),
)
def test_property_23_data_quality_decisions_follow_declared_contracts(
    required_missing,
    required_stale,
    optional_missing,
    synthetic,
    counterfactual_samples,
):
    """**Property 23: Data quality decisions follow declared contracts**"""

    observations = [
        DatasetObservation(
            "daily_kbar",
            present=not required_missing,
            coverage=1.0,
            age_seconds=3601 if required_stale else 0,
        )
    ]
    if not optional_missing:
        observations.append(
            DatasetObservation(
                "capital_flow",
                present=True,
                coverage=1.0,
                age_seconds=0,
                synthetic_fields=("net_main_amount",) if synthetic else (),
            )
        )
    decision = decide_quality(
        _quality_contract(),
        observations,
        auxiliary_counterfactual_samples=counterfactual_samples,
    )

    if required_missing or required_stale:
        expected = DataQualityStatus.BLOCKED
    elif optional_missing or synthetic:
        expected = DataQualityStatus.DEGRADED
    else:
        expected = DataQualityStatus.READY
    assert decision.status is expected
    assert decision.evaluated_before_formal_execution
    assert decision.can_execute_formally is (expected is not DataQualityStatus.BLOCKED)
    assert decision.auxiliary_impact is (
        AuxiliaryImpact.UNKNOWN
        if counterfactual_samples < 20
        else AuxiliaryImpact.SUPPORTED
    )
    reason_codes = {reason.code for reason in decision.reasons}
    assert ("REQUIRED_DATASET_MISSING" in reason_codes) is required_missing
    assert ("OPTIONAL_DATASET_MISSING" in reason_codes) is optional_missing
    assert ("SYNTHETIC_FIELDS_USED" in reason_codes) is (
        synthetic and not optional_missing
    )


@settings(max_examples=100, deadline=None)
@given(
    result=_JSON_VALUE,
    component=st.sampled_from(
        (
            "recommendation",
            "snapshot",
            "artifact",
            "execution",
            "revision",
            "label",
        )
    ),
)
def test_property_24_outcome_identity_is_idempotent_and_append_only(result, component):
    """**Property 24: Outcome identity is idempotent and append-only**"""

    identity = _identity()
    first = _append(identity, result)
    replay = _append(identity, result, first.versions)
    assert first.appended
    assert not replay.appended
    assert replay.versions == first.versions
    assert replay.version == first.version

    changes: dict[str, Any] = {}
    if component == "recommendation":
        changes = {
            "recommendation_id": "rec-s5-changed",
            "recommendation_hash": _HEX[4],
        }
    elif component == "snapshot":
        changes = {"snapshot_id": "snapshot-s5-changed"}
    elif component == "artifact":
        changes = {"artifact_hash": _HEX[5]}
    elif component == "execution":
        changes = {"execution_hash": _HEX[6]}
    elif component == "revision":
        changes = {"code_revision": "2" * 40}
    elif component == "label":
        changes = {"label_hash": _HEX[7]}
    changed = _append(_identity(**changes), result, first.versions)

    assert changed.appended
    assert len(changed.versions) == 2
    assert changed.versions[0] == first.version
    assert changed.version.calculation_fingerprint != first.version.calculation_fingerprint

    with pytest.raises(OutcomeFingerprintConflict):
        _append(identity, {"conflicting": result}, first.versions)


@settings(max_examples=100, deadline=None)
@given(
    stale_writer=st.booleans(),
    duplicate=st.booleans(),
    orphan=st.booleans(),
    fingerprint_missing=st.booleans(),
    missing_current=st.booleans(),
)
def test_property_25_current_projection_is_atomic_and_preserves_audit_history(
    stale_writer,
    duplicate,
    orphan,
    fingerprint_missing,
    missing_current,
):
    """**Property 25: Current projection is atomic and preserves audit history**"""

    v1 = _append(_identity(), {"version": 1}).version
    v2 = _append(
        _identity(code_revision="2" * 40),
        {"version": 2},
        (v1,),
    ).version
    first = cas_current(None, v1, expected_revision=None, projected_at=_NOW).projection
    second = cas_current(
        first,
        v2,
        expected_revision=first.projection_revision,
        projected_at=_NOW,
    ).projection
    assert second.calculation_version_id == v2.version_id
    assert second.projection_revision == first.projection_revision + 1

    if stale_writer:
        with pytest.raises(StaleProjectionWrite):
            cas_current(
                second,
                v1,
                expected_revision=first.projection_revision,
                projected_at=_NOW,
            )
        assert second.calculation_version_id == v2.version_id

    versions = [v1, v2]
    recommendation_ids = {"rec-s5"}
    if duplicate:
        versions.append(replace(v2, version_id="outv-duplicate"))
    if orphan:
        orphan_version = _append(
            _identity(
                recommendation_id="rec-orphan",
                recommendation_hash=_HEX[8],
            ),
            {"orphan": True},
        ).version
        versions.append(orphan_version)
    if fingerprint_missing:
        versions.append(
            replace(
                v1,
                version_id="outv-fingerprint-missing",
                calculation_fingerprint=None,
            )
        )
    if missing_current:
        extra = _append(
            _identity(
                recommendation_id="rec-no-current",
                recommendation_hash=_HEX[9],
            ),
            {"missing_current": True},
        ).version
        versions.append(extra)
        recommendation_ids.add("rec-no-current")

    audit = audit_outcome_history(recommendation_ids, versions, (second,))
    assert audit.preserved_version_ids == tuple(item.version_id for item in versions)
    kinds = {finding.kind for finding in audit.findings}
    assert (OutcomeAuditKind.DUPLICATE in kinds) is duplicate
    assert (OutcomeAuditKind.ORPHAN in kinds) is orphan
    assert (OutcomeAuditKind.FINGERPRINT_MISSING in kinds) is fingerprint_missing
    assert (OutcomeAuditKind.MISSING_CURRENT in kinds) is missing_current
    assert (v2.version_id in audit.current_kpi_version_ids) is (not duplicate)


@settings(max_examples=100, deadline=None)
@given(
    condition=st.sampled_from(
        (
            "fresh",
            "expected_missing",
            "projection_missing",
            "projection_fingerprint_missing",
            "projection_mismatch",
            "version_missing",
            "version_fingerprint_missing",
            "version_mismatch",
            "version_invalid",
        )
    )
)
def test_property_26_stale_recomputation_is_exact(condition):
    """**Property 26: Stale recomputation is exact**"""

    version = _append(_identity(), {"fresh": True}).version
    expected = version.calculation_fingerprint
    projection = cas_current(
        None, version, expected_revision=None, projected_at=_NOW
    ).projection
    versions = [version]

    if condition == "expected_missing":
        expected = None
    elif condition == "projection_missing":
        projection = None
    elif condition == "projection_fingerprint_missing":
        projection = replace(projection, current_calculation_fingerprint=None)
    elif condition == "projection_mismatch":
        projection = replace(projection, current_calculation_fingerprint=_HEX[10])
    elif condition == "version_missing":
        versions = []
    elif condition == "version_fingerprint_missing":
        versions = [replace(version, calculation_fingerprint=None)]
    elif condition == "version_mismatch":
        versions = [replace(version, calculation_fingerprint=_HEX[11])]
    elif condition == "version_invalid":
        versions = [replace(version, validity_status="INVALID")]

    selection = select_stale_outcomes(
        (OutcomeRecomputeCandidate("rec-s5", expected, projection),),
        versions,
    )
    assert (selection.recommendation_ids == ("rec-s5",)) is (
        condition != "fresh"
    )
    assert bool(selection.reasons) is (condition != "fresh")


# Task 5.8 examples ---------------------------------------------------------


def test_required_missing_and_stale_are_blockers() -> None:
    missing = decide_quality(
        _quality_contract(),
        (DatasetObservation("daily_kbar", present=False),),
    )
    stale = decide_quality(
        _quality_contract(),
        (
            DatasetObservation(
                "daily_kbar", present=True, coverage=1.0, age_seconds=3601
            ),
        ),
    )
    assert missing.status is DataQualityStatus.BLOCKED
    assert stale.status is DataQualityStatus.BLOCKED


def test_optional_missing_and_synthetic_are_degraded_with_unknown_impact() -> None:
    optional_missing = decide_quality(
        _quality_contract(),
        (
            DatasetObservation(
                "daily_kbar", present=True, coverage=1.0, age_seconds=0
            ),
        ),
        auxiliary_counterfactual_samples=0,
    )
    synthetic = decide_quality(
        _quality_contract(),
        (
            DatasetObservation(
                "daily_kbar", present=True, coverage=1.0, age_seconds=0
            ),
            DatasetObservation(
                "capital_flow",
                present=True,
                coverage=1.0,
                age_seconds=0,
                synthetic_fields=("net_main_amount",),
            ),
        ),
        auxiliary_counterfactual_samples=0,
    )
    assert optional_missing.status is DataQualityStatus.DEGRADED
    assert synthetic.status is DataQualityStatus.DEGRADED
    assert optional_missing.auxiliary_impact is AuxiliaryImpact.UNKNOWN
    assert synthetic.auxiliary_impact is AuxiliaryImpact.UNKNOWN


class _IsolatedConcurrentVersionModel:
    """Lock-backed model of one DB transaction + unique fingerprint constraint."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.versions: tuple[OutcomeVersionRecord, ...] = ()

    def append(self, identity: OutcomeCalculationIdentity, result: Any):
        with self._lock:
            decision = _append(identity, result, self.versions)
            self.versions = decision.versions
            return decision


def test_same_fingerprint_concurrency_has_one_logical_version() -> None:
    model = _IsolatedConcurrentVersionModel()
    identity = _identity()
    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = list(
            executor.map(
                lambda _: model.append(identity, {"return": 2.5}),
                range(32),
            )
        )
    assert len(model.versions) == 1
    assert sum(decision.appended for decision in decisions) == 1
    assert {decision.version.version_id for decision in decisions} == {
        model.versions[0].version_id
    }


def test_conflicting_same_fingerprint_fails_closed() -> None:
    first = _append(_identity(), {"return": 1.0})
    with pytest.raises(OutcomeFingerprintConflict):
        _append(_identity(), {"return": -1.0}, first.versions)
    assert len(first.versions) == 1


def test_stale_selection_and_duplicate_orphan_audit_preserve_history() -> None:
    current_version = _append(_identity(), {"return": 1.0}).version
    current = cas_current(
        None, current_version, expected_revision=None, projected_at=_NOW
    ).projection
    stale = select_stale_outcomes(
        (
            OutcomeRecomputeCandidate(
                "rec-s5", canonical_fingerprint({"new": "inputs"}), current
            ),
        ),
        (current_version,),
    )
    assert stale.recommendation_ids == ("rec-s5",)

    duplicate = replace(current_version, version_id="outv-duplicate-example")
    orphan = _append(
        _identity(
            recommendation_id="rec-orphan-example",
            recommendation_hash=_HEX[12],
        ),
        {"return": 0.0},
    ).version
    audit = audit_outcome_history(
        ("rec-s5",),
        (current_version, duplicate, orphan),
        (current,),
    )
    assert audit.preserved_version_ids == (
        current_version.version_id,
        duplicate.version_id,
        orphan.version_id,
    )
    assert audit.current_kpi_version_ids == ()
    assert {finding.kind for finding in audit.findings} >= {
        OutcomeAuditKind.DUPLICATE,
        OutcomeAuditKind.ORPHAN,
    }


class _IsolatedProjectionModel:
    """Lock-backed model of the current row's atomic revision predicate."""

    def __init__(self, projection: CurrentProjection) -> None:
        self._lock = Lock()
        self.projection = projection

    def compare_and_set(
        self, target: OutcomeVersionRecord, expected_revision: int
    ) -> CurrentProjection:
        with self._lock:
            decision = cas_current(
                self.projection,
                target,
                expected_revision=expected_revision,
                projected_at=_NOW,
            )
            self.projection = decision.projection
            return self.projection


def test_isolated_projection_cas_rejects_concurrent_stale_writer() -> None:
    v1 = _append(_identity(), {"version": 1}).version
    v2 = _append(
        _identity(code_revision="2" * 40), {"version": 2}, (v1,)
    ).version
    v3 = _append(
        _identity(code_revision="3" * 40), {"version": 3}, (v1, v2)
    ).version
    initial = cas_current(
        None, v1, expected_revision=None, projected_at=_NOW
    ).projection
    model = _IsolatedProjectionModel(initial)

    def compete(target: OutcomeVersionRecord) -> str:
        try:
            return model.compare_and_set(target, initial.projection_revision).calculation_version_id
        except StaleProjectionWrite:
            return "STALE"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(compete, (v2, v3)))
    assert outcomes.count("STALE") == 1
    assert model.projection.calculation_version_id in {v2.version_id, v3.version_id}
    assert model.projection.projection_revision == initial.projection_revision + 1

"""S4 holdout/release Layer-1 properties (tasks 4.4--4.9).

All five properties run 100 generated examples and use only pure contracts.
No test opens a database, executes a migration, touches the network, or writes
to the governance object store.  Real MySQL concurrency belongs to deferred
Layer-2 task 4.10.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib

import pytest
from hypothesis import given, settings, strategies as st

from app.governance.holdout import (
    AuthorityRole,
    EvidencePackageSnapshot,
    EvidenceScope,
    HoldoutClaimRequest,
    HoldoutContractError,
    HoldoutPopulationIdentity,
    PrincipalAuthority,
    ReleaseAction,
    ReleaseEligibilityInput,
    ReleaseThresholds,
    ResearchFamilyDefinition,
    ResearchPathClassification,
    apply_holdout_claim_cas,
    authorize_release_action,
    claim_holdout,
    classify_evidence,
    decide_release_eligibility,
    freeze_research_family,
    prepare_holdout_claim,
)


S4_SETTINGS = settings(max_examples=100, deadline=None)
_TEXT = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122, blacklist_categories=("Cs",)),
    min_size=1,
    max_size=20,
).filter(lambda value: bool(value.strip()))
_SHA = st.binary(min_size=1, max_size=32).map(lambda value: hashlib.sha256(value).hexdigest())
_DATES = st.lists(
    st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)),
    min_size=1,
    max_size=8,
)
_UNIVERSE = st.lists(_TEXT, min_size=1, max_size=8)


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity(
    *,
    snapshot_version: int = 1,
    actual_dates: tuple[str, ...] = ("2026-07-01", "2026-07-02"),
    universe: tuple[str, ...] = ("000001.SZ", "600000.SH"),
    label_version: int = 1,
    execution_version: int = 1,
) -> HoldoutPopulationIdentity:
    return HoldoutPopulationIdentity.build(
        snapshot={"snapshot_id": "snapshot-a", "version": snapshot_version},
        actual_dates=actual_dates,
        universe=universe,
        label_policy={"policy_id": "t-plus-one", "version": label_version},
        execution_policy={"policy_id": "canonical-execution", "version": execution_version},
    )


def _claim_request(
    identity: HoldoutPopulationIdentity,
    *,
    token: str = "token-a",
    experiment: str = "experiment-a",
    hypothesis: str = "hypothesis-a",
) -> HoldoutClaimRequest:
    return HoldoutClaimRequest(
        identity=identity,
        experiment_id=experiment,
        hypothesis_id=hypothesis,
        research_family_fingerprint=_h("family-a"),
        principal_id="researcher-a",
        caller_token=token,
    )


def _family(
    identity: HoldoutPopulationIdentity | None = None,
    *,
    candidates: tuple[str, ...] = (_h("candidate-a"), _h("candidate-b")),
    allowed_scopes: tuple[EvidenceScope, ...] = (EvidenceScope.UNTOUCHED_HOLDOUT,),
) -> ResearchFamilyDefinition:
    population = (identity or _identity()).population_fingerprint
    return ResearchFamilyDefinition.build(
        research_family_id="family-a",
        holdout_population_fingerprint=population,
        baseline_policy_fingerprint=_h("baseline"),
        hypothesis_family="leader-continuation",
        candidate_fingerprints=candidates,
        metrics={"primary": "net_return", "secondary": ["mdd"]},
        thresholds={"minimum_trades": 20, "coverage": 0.9},
        allowed_evidence_scopes=allowed_scopes,
    )


def _release_case(
    *,
    path: str = ResearchPathClassification.GOVERNED_RELEASE.value,
    production_eligibility: bool = True,
    provenance_valid: bool = True,
    package_id: str = "package-a",
    request_package_id: str | None = None,
    policy_id: str = "policy-a",
    request_policy_id: str | None = None,
    state_id: str = "state-a",
    request_state_id: str | None = None,
    deployment_id: str = "deployment-a",
    request_deployment_id: str | None = None,
    gate_manifest_id: str = "manifest-a",
    request_gate_manifest_id: str | None = None,
    sample_count: int = 30,
    distinct_days: int = 10,
    coverage: float = 0.95,
    ci_low: float = 0.01,
    ci_high: float = 0.08,
    confidence_valid: bool = True,
    mdd_regression: float = 0.01,
    multiple_testing_passed: bool = True,
) -> tuple[ReleaseEligibilityInput, ResearchFamilyDefinition, EvidencePackageSnapshot, ReleaseThresholds]:
    family = _family()
    request = ReleaseEligibilityInput(
        evidence_scope=EvidenceScope.UNTOUCHED_HOLDOUT.value,
        research_path=path,
        production_eligibility=production_eligibility,
        research_family_id=family.research_family_id,
        complete_provenance_id="provenance-a",
        complete_provenance_valid=provenance_valid,
        evidence_package_id=request_package_id or package_id,
        current_policy_id=request_policy_id or policy_id,
        current_state_id=request_state_id or state_id,
        current_deployment_id=request_deployment_id or deployment_id,
        preregistered_gate_manifest_id=request_gate_manifest_id or gate_manifest_id,
    )
    package = EvidencePackageSnapshot(
        evidence_package_id=package_id,
        research_family_id=family.research_family_id,
        complete_provenance_id="provenance-a",
        policy_id=policy_id,
        state_id=state_id,
        deployment_id=deployment_id,
        gate_manifest_id=gate_manifest_id,
        sample_count=sample_count,
        distinct_signal_days=distinct_days,
        coverage=coverage,
        confidence_interval_low=ci_low,
        confidence_interval_high=ci_high,
        confidence_valid=confidence_valid,
        mdd_regression=mdd_regression,
        multiple_testing_passed=multiple_testing_passed,
    )
    thresholds = ReleaseThresholds(20, 5, 0.9, 0.0, 0.02)
    return request, family, package, thresholds


@S4_SETTINGS
@given(
    snapshot_version=st.integers(min_value=1, max_value=10_000),
    actual_dates=_DATES,
    universe=_UNIVERSE,
    label_version=st.integers(min_value=1, max_value=10_000),
    execution_version=st.integers(min_value=1, max_value=10_000),
    token_a=_TEXT,
    token_b=_TEXT,
    experiment_b=_TEXT,
    hypothesis_b=_TEXT,
)
def test_property_18_holdout_identity_is_canonical_and_single_use(
    snapshot_version,
    actual_dates,
    universe,
    label_version,
    execution_version,
    token_a,
    token_b,
    experiment_b,
    hypothesis_b,
):
    """**Property 18: Validates Requirements 5.1--5.3.**"""

    base = HoldoutPopulationIdentity.build(
        snapshot={"id": "snapshot", "version": snapshot_version},
        actual_dates=actual_dates,
        universe=universe,
        label_policy={"id": "label", "version": label_version},
        execution_policy={"id": "execution", "version": execution_version},
    )
    reordered = HoldoutPopulationIdentity.build(
        snapshot={"version": snapshot_version, "id": "snapshot"},
        actual_dates=list(reversed(actual_dates)) + actual_dates[:1],
        universe=list(reversed(universe)) + universe[:1],
        label_policy={"version": label_version, "id": "label"},
        execution_policy={"version": execution_version, "id": "execution"},
    )
    assert base.population_fingerprint == reordered.population_fingerprint
    changed = {
        _identity(snapshot_version=snapshot_version + 1).population_fingerprint,
        HoldoutPopulationIdentity.build(
            snapshot={"id": "snapshot", "version": snapshot_version},
            actual_dates=[*actual_dates, date(2099, 1, 1)],
            universe=universe,
            label_policy={"id": "label", "version": label_version},
            execution_policy={"id": "execution", "version": execution_version},
        ).population_fingerprint,
        HoldoutPopulationIdentity.build(
            snapshot={"id": "snapshot", "version": snapshot_version},
            actual_dates=actual_dates,
            universe=[*universe, "__new_universe_member__"],
            label_policy={"id": "label", "version": label_version},
            execution_policy={"id": "execution", "version": execution_version},
        ).population_fingerprint,
        HoldoutPopulationIdentity.build(
            snapshot={"id": "snapshot", "version": snapshot_version},
            actual_dates=actual_dates,
            universe=universe,
            label_policy={"id": "label", "version": label_version + 1},
            execution_policy={"id": "execution", "version": execution_version},
        ).population_fingerprint,
        HoldoutPopulationIdentity.build(
            snapshot={"id": "snapshot", "version": snapshot_version},
            actual_dates=actual_dates,
            universe=universe,
            label_policy={"id": "label", "version": label_version},
            execution_policy={"id": "execution", "version": execution_version + 1},
        ).population_fingerprint,
    }
    assert base.population_fingerprint not in changed

    first = claim_holdout(None, _claim_request(base, token=token_a))
    assert first.accepted and first.record is not None
    retry = claim_holdout(
        first.record,
        _claim_request(base, token=token_b, experiment=experiment_b, hypothesis=hypothesis_b),
    )
    assert not retry.accepted
    assert retry.reason_codes == ("HOLDOUT_POPULATION_ALREADY_CONSUMED",)
    assert prepare_holdout_claim(_claim_request(base, token=token_a)).population_fingerprint == base.population_fingerprint


@S4_SETTINGS
@given(
    candidates=st.lists(_SHA, min_size=1, max_size=8, unique=True),
    metric_name=_TEXT,
    minimum_trades=st.integers(min_value=1, max_value=10_000),
    scopes=st.lists(st.sampled_from(list(EvidenceScope)), min_size=1, max_size=6, unique=True),
)
def test_property_19_research_family_freeze_is_immutable(candidates, metric_name, minimum_trades, scopes):
    """**Property 19: Validates Requirement 5.4.**"""

    proposed = ResearchFamilyDefinition.build(
        research_family_id="family-property",
        holdout_population_fingerprint=_identity().population_fingerprint,
        baseline_policy_fingerprint=_h("baseline-property"),
        hypothesis_family="family-hypothesis",
        candidate_fingerprints=candidates,
        metrics={"primary": metric_name},
        thresholds={"minimum_trades": minimum_trades},
        allowed_evidence_scopes=scopes,
    )
    frozen = freeze_research_family(None, proposed, frozen_at="2026-08-04T10:00:00Z")
    assert freeze_research_family(frozen, proposed, frozen_at="later") is frozen

    mutation = ResearchFamilyDefinition.build(
        research_family_id=proposed.research_family_id,
        holdout_population_fingerprint=proposed.holdout_population_fingerprint,
        baseline_policy_fingerprint=proposed.baseline_policy_fingerprint,
        hypothesis_family=proposed.hypothesis_family,
        candidate_fingerprints=[*proposed.candidate_fingerprints, _h("post-unseal-candidate")],
        metrics={"primary": metric_name},
        thresholds={"minimum_trades": minimum_trades},
        allowed_evidence_scopes=scopes,
    )
    with pytest.raises(HoldoutContractError, match="immutable") as raised:
        freeze_research_family(frozen, mutation, frozen_at="2026-08-05T10:00:00Z")
    assert raised.value.code == "RESEARCH_FAMILY_FROZEN"
    assert frozen.definition == proposed


@S4_SETTINGS
@given(
    scope=st.sampled_from(list(EvidenceScope)),
    path=st.sampled_from(list(ResearchPathClassification)),
    requested=st.booleans(),
)
def test_property_20_scope_and_path_are_orthogonal_and_valid(scope, path, requested):
    """**Property 20: Validates Requirements 5.5--5.8.**"""

    classified = classify_evidence(scope, path, requested_production_eligibility=requested)
    assert classified.evidence_scope is scope
    assert classified.research_path is path
    assert classified.production_eligibility is (
        requested and path is ResearchPathClassification.GOVERNED_RELEASE
    )


@S4_SETTINGS
@given(
    path=st.sampled_from(list(ResearchPathClassification)),
    production_eligibility=st.booleans(),
    provenance_valid=st.booleans(),
    sample_count=st.integers(min_value=0, max_value=50),
    distinct_days=st.integers(min_value=0, max_value=20),
    coverage=st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False),
    ci_low=st.floats(min_value=-0.2, max_value=0.2, allow_nan=False, allow_infinity=False),
    ci_width=st.floats(min_value=0, max_value=0.2, allow_nan=False, allow_infinity=False),
    confidence_valid=st.booleans(),
    mdd_regression=st.floats(min_value=-0.1, max_value=0.1, allow_nan=False, allow_infinity=False),
    multiple_testing_passed=st.booleans(),
    package_matches=st.booleans(),
    policy_matches=st.booleans(),
    state_matches=st.booleans(),
    deployment_matches=st.booleans(),
    manifest_matches=st.booleans(),
)
def test_property_21_release_is_deterministic_explicit_conjunction(
    path,
    production_eligibility,
    provenance_valid,
    sample_count,
    distinct_days,
    coverage,
    ci_low,
    ci_width,
    confidence_valid,
    mdd_regression,
    multiple_testing_passed,
    package_matches,
    policy_matches,
    state_matches,
    deployment_matches,
    manifest_matches,
):
    """**Property 21: Validates Requirements 5.9--5.12.**"""

    args = _release_case(
        path=path.value,
        production_eligibility=production_eligibility,
        provenance_valid=provenance_valid,
        request_package_id=None if package_matches else "other-package",
        request_policy_id=None if policy_matches else "other-policy",
        request_state_id=None if state_matches else "other-state",
        request_deployment_id=None if deployment_matches else "other-deployment",
        request_gate_manifest_id=None if manifest_matches else "other-manifest",
        sample_count=sample_count,
        distinct_days=distinct_days,
        coverage=coverage,
        ci_low=ci_low,
        ci_high=ci_low + ci_width,
        confidence_valid=confidence_valid,
        mdd_regression=mdd_regression,
        multiple_testing_passed=multiple_testing_passed,
    )
    first = decide_release_eligibility(*args)
    second = decide_release_eligibility(*args)
    assert first == second
    assert first.eligible is all(passed for _, passed in first.gate_results)
    assert first.reason_codes == tuple(code for code, passed in first.gate_results if not passed)
    assert first.decision_id == second.decision_id
    assert first.canonical_payload()["input_snapshot"]["evidence_scope"] == EvidenceScope.UNTOUCHED_HOLDOUT.value


@S4_SETTINGS
@given(
    extra_roles=st.sets(st.sampled_from(list(AuthorityRole)), max_size=2),
    eligible=st.booleans(),
)
def test_property_22_research_authority_cannot_publish(extra_roles, eligible):
    """**Property 22: Validates Requirement 5.13.**"""

    request, family, package, thresholds = _release_case()
    decision = decide_release_eligibility(request, family, package, thresholds)
    if not eligible:
        decision = replace(decision, eligible=False)
    research_only = PrincipalAuthority.build("researcher", [AuthorityRole.RESEARCH_PROPOSER])
    denied = authorize_release_action(decision, research_only, ReleaseAction.FORMAL_PUBLISH)
    assert not denied.authorized
    assert "FORMAL_PUBLISH_AUTHORITY_REQUIRED" in denied.reason_codes

    principal = PrincipalAuthority.build("generated", list(extra_roles))
    result = authorize_release_action(decision, principal, ReleaseAction.FORMAL_PUBLISH)
    assert result.authorized is (
        decision.eligible and AuthorityRole.FORMAL_PUBLISHER in principal.roles
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("token", "other-token"), ("experiment", "other-experiment"), ("hypothesis", "other-hypothesis")],
)
def test_4_9_changed_audit_or_research_metadata_cannot_reconsume(field, value):
    identity = _identity()
    first = claim_holdout(None, _claim_request(identity))
    kwargs = {field: value}
    second = claim_holdout(first.record, _claim_request(identity, **kwargs))
    assert not second.accepted
    assert second.reason_codes == ("HOLDOUT_POPULATION_ALREADY_CONSUMED",)


def test_4_9_concurrent_claim_commands_have_exactly_one_cas_winner():
    identity = _identity()
    commands = [
        prepare_holdout_claim(_claim_request(identity, token=f"token-{index}", experiment=f"exp-{index}"))
        for index in range(8)
    ]
    current = None
    outcomes = []
    for command in reversed(commands):
        outcome = apply_holdout_claim_cas(current, command)
        outcomes.append(outcome)
        if outcome.accepted:
            current = outcome.record
    assert sum(outcome.accepted for outcome in outcomes) == 1
    assert current is not None and current.population_fingerprint == identity.population_fingerprint


def test_4_9_unseal_rejects_added_candidate_and_caller_identity_token():
    family = _family()
    frozen = freeze_research_family(None, family, frozen_at="2026-08-04T10:00:00Z")
    changed = _family(candidates=(*family.candidate_fingerprints, _h("late")))
    with pytest.raises(HoldoutContractError) as family_error:
        freeze_research_family(frozen, changed, frozen_at="2026-08-05T10:00:00Z")
    assert family_error.value.code == "RESEARCH_FAMILY_FROZEN"

    with pytest.raises(HoldoutContractError) as identity_error:
        replace(
            _claim_request(_identity()),
            supplied_population_fingerprint=_h("caller-token-used-as-identity"),
        )
    assert identity_error.value.code == "HOLDOUT_CALLER_IDENTITY_REJECTED"


@pytest.mark.parametrize("scope,path", [("EXPLORATORY", "GOVERNED_RELEASE"), ("TRAIN", "UNKNOWN"), (None, "PATH_A")])
def test_4_9_invalid_classification_fails_closed(scope, path):
    with pytest.raises(HoldoutContractError) as raised:
        classify_evidence(scope, path, requested_production_eligibility=True)
    assert raised.value.code == "RELEASE_CLASSIFICATION_INVALID"

    request, family, package, thresholds = _release_case()
    invalid = replace(request, evidence_scope=scope, research_path=path)
    decision = decide_release_eligibility(invalid, family, package, thresholds)
    assert not decision.eligible
    assert decision.reason_codes == ("RELEASE_CLASSIFICATION_INVALID",)


def test_4_9_ci_crossing_zero_multiple_testing_and_path_a_fail_stably():
    request, family, package, thresholds = _release_case(
        path=ResearchPathClassification.PATH_A.value,
        ci_low=-0.01,
        ci_high=0.02,
        multiple_testing_passed=False,
    )
    decision = decide_release_eligibility(request, family, package, thresholds)
    assert not decision.eligible
    assert decision.input_snapshot.evidence_scope == EvidenceScope.UNTOUCHED_HOLDOUT.value
    assert decision.input_snapshot.production_eligibility is False
    assert decision.reason_codes == (
        "GOVERNED_PATH",
        "PRODUCTION_ELIGIBLE",
        "CONFIDENCE",
        "MULTIPLE_TESTING",
    )


def test_4_9_eligible_research_proposal_still_cannot_formally_publish():
    decision = decide_release_eligibility(*_release_case())
    assert decision.eligible
    researcher = PrincipalAuthority.build("researcher", [AuthorityRole.RESEARCH_PROPOSER])
    publisher = PrincipalAuthority.build("publisher", [AuthorityRole.FORMAL_PUBLISHER])
    assert authorize_release_action(decision, researcher, ReleaseAction.PROPOSE).authorized
    assert not authorize_release_action(decision, researcher, ReleaseAction.FORMAL_PUBLISH).authorized
    assert authorize_release_action(decision, publisher, ReleaseAction.FORMAL_PUBLISH).authorized

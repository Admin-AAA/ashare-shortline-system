"""Task 0.4 cross-module golden harness verification.

The adapters below execute the real pure simulation core.  They differ only in
which application boundary they represent and in their evidence identity, so
outcome/position/risk parity can be checked independently from the intentionally
different evidence projection.

Task 0.9 extends the harness with four canonical projection adapters (evidence
eligibility, T+1 execution, outcome identity, and min-cap risk decision) pinned
against additive golden expectations in the same fixture.  All four emit Layer 1
evidence records only; nothing here marks an environment ``ACCEPTED``.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field, replace
from itertools import permutations
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.engine.backtest import ArtifactScope, SnapshotIntegrityError
from app.engine.simulation import simulate
from testkit import (
    AdapterRole,
    EvidenceEligibilityAdapter,
    EvidenceProjection,
    ExternalNetworkAccessError,
    GoldenAdapterContractError,
    GoldenEvidenceClass,
    GoldenHarness,
    GoldenHarnessRun,
    GoldenMismatchError,
    GoldenProjectionSet,
    GoldenRiskDecision,
    LAYER_1_EVIDENCE_LAYER,
    MinCapRiskDecisionAdapter,
    MySQLSandboxMode,
    OutcomeIdentityAdapter,
    OutcomeProjection,
    PositionCapProjection,
    PositionProjection,
    RiskProjection,
    TPlusOneExecutionAdapter,
    UnsafeMySQLTestEnvironment,
    attest_real_mysql_sandbox,
    load_golden_fixture,
    load_projection_golden_cases,
    validate_mysql_sandbox,
)


FIXTURE_PATH = Path(__file__).resolve().parent / "data" / "production_hardening_golden_v1.json"
_EVIDENCE_CLASS_BY_ROLE = {
    AdapterRole.HISTORICAL_RECONSTRUCTION: GoldenEvidenceClass.HISTORICAL_RECONSTRUCTION,
    AdapterRole.BACKTEST: GoldenEvidenceClass.RESEARCH_BACKTEST,
    AdapterRole.FORMAL: GoldenEvidenceClass.LIVE_FORWARD,
}


@dataclass
class _SimulationAdapter:
    """Minimal application-boundary adapter over the authoritative pure core."""

    role: AdapterRole
    seen_input_ids: list[int] = field(default_factory=list)

    def run(self, data: Any) -> GoldenProjectionSet:
        self.seen_input_ids.append(id(data))
        signal_payload = data.artifact_payload(data.signal_dates[0], ArtifactScope.SIGNAL_INPUT)
        risk_context = _mapping(signal_payload.get("risk_context"), "risk_context")
        evidence_context = _mapping(signal_payload.get("evidence_context"), "evidence_context")
        generated_by_role = _mapping(
            evidence_context.get("actual_generated_at_by_role"),
            "actual_generated_at_by_role",
        )

        outcomes: list[OutcomeProjection] = []
        positions: list[PositionProjection] = []
        risks: list[RiskProjection] = []
        evidence: list[EvidenceProjection] = []
        for recommendation in data.recommendations():
            context = data.context_for(recommendation.recommendation_id)
            result = simulate(
                recommendation,
                data.bars_for(recommendation),
                data.execution_policy,
            )
            trade = result.trade
            outcomes.append(
                OutcomeProjection(
                    recommendation_id=recommendation.recommendation_id,
                    code=recommendation.code,
                    signal_date=recommendation.signal_date,
                    entry_triggered=result.entry_triggered,
                    entry_date=trade.entry_date,
                    entry_price=trade.entry_price,
                    exit_date=trade.exit_date,
                    exit_price=trade.exit_price,
                    outcome_label=trade.outcome_label.value,
                    exit_reason=trade.exit_reason or "unspecified",
                    gross_return_pct=trade.gross_return_pct,
                    net_return_pct=trade.net_return_pct,
                    cost_pct=data.execution_policy.fee_pct if trade.executed else 0,
                )
            )

            cap_values = _mapping(context.get("position_caps"), "position_caps")
            caps = tuple(
                PositionCapProjection(name=str(name), value=value)
                for name, value in cap_values.items()
            )
            binding_cap = min(caps, key=lambda item: float(item.value)).name
            positions.append(
                PositionProjection(
                    recommendation_id=recommendation.recommendation_id,
                    requested_size=context.get("requested_position_size"),
                    approved_size=risk_context.get("approved_size"),
                    final_size=trade.position_size,
                    binding_cap=binding_cap,
                    caps=caps,
                )
            )
            reason_codes = tuple(risk_context.get("reason_codes") or ())
            if self.role is AdapterRole.FORMAL:
                reason_codes = tuple(reversed(reason_codes))
            risks.append(
                RiskProjection(
                    recommendation_id=recommendation.recommendation_id,
                    decision=GoldenRiskDecision(str(risk_context.get("decision"))),
                    requested_size=risk_context.get("requested_size"),
                    approved_size=risk_context.get("approved_size"),
                    reason_codes=reason_codes,
                    policy_fingerprint=str(risk_context.get("policy_fingerprint")),
                    state_fingerprint=str(risk_context.get("state_fingerprint")),
                )
            )
            evidence_class = _EVIDENCE_CLASS_BY_ROLE[self.role]
            evidence.append(
                EvidenceProjection(
                    recommendation_id=recommendation.recommendation_id,
                    evidence_class=evidence_class,
                    signal_date=recommendation.signal_date,
                    actual_generated_at=str(generated_by_role.get(self.role.value)),
                    data_as_of=str(evidence_context.get("data_as_of")),
                    source_snapshot_id=data.snapshot.snapshot_id,
                    strategy_policy_fingerprint=str(
                        evidence_context.get("strategy_policy_fingerprint")
                    ),
                    execution_policy_fingerprint=data.execution_policy.fingerprint(),
                    code_revision=str(evidence_context.get("code_revision")),
                    reconstruction_batch_id=(
                        str(evidence_context.get("reconstruction_batch_id"))
                        if self.role is AdapterRole.HISTORICAL_RECONSTRUCTION
                        else None
                    ),
                    is_true_forward=self.role is AdapterRole.FORMAL,
                )
            )
        return GoldenProjectionSet(
            outcomes=tuple(outcomes),
            positions=tuple(positions),
            risks=tuple(risks),
            evidence=tuple(evidence),
        )


@dataclass
class _NetworkAdapter:
    role: AdapterRole

    def run(self, data: Any) -> GoldenProjectionSet:
        del data
        socket.create_connection(("example.invalid", 443), timeout=0.01)
        raise AssertionError("network guard did not fail closed")


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    assert isinstance(value, Mapping), field_name
    return value


def _adapters() -> tuple[_SimulationAdapter, ...]:
    return tuple(_SimulationAdapter(role) for role in AdapterRole)


def test_fixed_snapshot_artifacts_are_reproducible_and_cover_required_execution_cases() -> None:
    """**Validates: Requirements 2.10, 10.2**"""

    first = load_golden_fixture(FIXTURE_PATH)
    second = load_golden_fixture(FIXTURE_PATH)

    assert first.fingerprint == second.fingerprint
    assert first.snapshot.input_manifest_hash == second.snapshot.input_manifest_hash
    assert first.signal_dates == ("2026-06-01",)
    assert {
        context["scenario_id"] for context in first.recommendation_contexts()
    } == {
        "cost_horizon",
        "t_plus_one_same_day_both_hit",
        "gap_entry_rejection",
        "one_word_limit_up",
        "limit_down_gap",
    }
    refs = first.snapshot.artifacts_for("2026-06-01")
    assert {ref.artifact_scope for ref in refs} == {
        ArtifactScope.SIGNAL_INPUT,
        ArtifactScope.OUTCOME_INPUT,
    }
    for ref in refs:
        ref.validate_content(first.payloads_by_artifact_id[ref.artifact_id])
        with pytest.raises(SnapshotIntegrityError):
            ref.validate_content(first.payloads_by_artifact_id[ref.artifact_id] + b"changed")
    with pytest.raises(TypeError):
        first.payloads_by_artifact_id["another"] = b"{}"  # type: ignore[index]


def test_historical_backtest_and_formal_adapters_share_input_and_compare_deterministically() -> None:
    """**Validates: Requirements 2.4, 10.2, 10.4**"""

    data = load_golden_fixture(FIXTURE_PATH)
    adapters = _adapters()
    run = GoldenHarness(data).run((adapters[2], adapters[0], adapters[1]))

    assert {adapter.seen_input_ids[0] for adapter in adapters} == {id(data)}
    for actual_role in (
        AdapterRole.HISTORICAL_RECONSTRUCTION,
        AdapterRole.FORMAL,
    ):
        comparison = run.compare(
            AdapterRole.BACKTEST,
            actual_role,
            projections=("outcome", "position", "risk"),
        )
        assert comparison.matches
        comparison.require_match()

    evidence_comparison = run.compare(
        AdapterRole.BACKTEST,
        AdapterRole.FORMAL,
        projections=("evidence",),
    )
    assert not evidence_comparison.matches
    assert any(
        difference.path.endswith(".evidence_class")
        for difference in evidence_comparison.differences
    )

    cost_case = next(
        outcome
        for outcome in run.results[AdapterRole.BACKTEST].outcomes
        if outcome.recommendation_id == "golden-cost-horizon"
    )
    assert cost_case.entry_date == "2026-06-02"
    assert cost_case.exit_date == "2026-06-03"
    assert cost_case.gross_return_pct == "5.00000000"
    assert cost_case.cost_pct == "0.07000000"
    assert cost_case.net_return_pct == "4.93000000"


def test_mismatch_attribution_has_stable_field_paths_and_can_fail_closed() -> None:
    """**Validates: Requirements 2.4, 10.2**"""

    base_run = GoldenHarness(load_golden_fixture(FIXTURE_PATH)).run(_adapters())
    formal = base_run.results[AdapterRole.FORMAL]
    changed_outcome = replace(formal.outcomes[0], net_return_pct="99")
    changed_formal = replace(
        formal,
        outcomes=(changed_outcome, *formal.outcomes[1:]),
    )
    changed_run = GoldenHarnessRun(
        input_fingerprint=base_run.input_fingerprint,
        results={**base_run.results, AdapterRole.FORMAL: changed_formal},
    )

    comparison = changed_run.compare(
        AdapterRole.BACKTEST,
        AdapterRole.FORMAL,
        projections=("outcome",),
    )
    assert [difference.path for difference in comparison.differences] == [
        "$.outcomes[0].net_return_pct"
    ]
    with pytest.raises(GoldenMismatchError, match="net_return_pct"):
        comparison.require_match()


def test_golden_harness_prohibits_external_network_and_restores_socket_api() -> None:
    """**Validates: Requirements 10.2, 10.4**"""

    original_socket = socket.socket
    adapters: list[Any] = list(_adapters())
    adapters[0] = _NetworkAdapter(AdapterRole.HISTORICAL_RECONSTRUCTION)

    with pytest.raises(ExternalNetworkAccessError, match="fixed snapshot artifacts"):
        GoldenHarness(load_golden_fixture(FIXTURE_PATH)).run(adapters)
    assert socket.socket is original_socket


def test_projection_canonicalization_property_is_order_invariant() -> None:
    """**Validates: Requirements 2.4, 10.2**"""

    caps = (
        PositionCapProjection("score", 0.5),
        PositionCapProjection("market_regime", 0.35),
        PositionCapProjection("risk_template", 0.3),
        PositionCapProjection("portfolio_single_name", 0.25),
    )
    canonical_positions = {
        PositionProjection(
            recommendation_id="property-case",
            requested_size=0.6,
            approved_size=0.25,
            final_size=0.25,
            binding_cap="portfolio_single_name",
            caps=ordered,
        ).to_canonical_json()
        for ordered in permutations(caps)
    }
    assert len(canonical_positions) == 1

    canonical_risks = {
        RiskProjection(
            recommendation_id="property-case",
            decision="reduce",
            requested_size=0.6,
            approved_size=0.25,
            reason_codes=ordered,
            policy_fingerprint="a" * 64,
            state_fingerprint="b" * 64,
        ).to_canonical_json()
        for ordered in permutations(
            ("portfolio_single_name_cap", "gross_exposure_cap", "market_regime_cap")
        )
    }
    assert len(canonical_risks) == 1

    data = load_golden_fixture(FIXTURE_PATH)
    adapters = _adapters()
    selected_payloads = {
        GoldenHarness(data)
        .run(ordered)
        .results[AdapterRole.BACKTEST]
        .selected_payload(("outcome", "position", "risk"))
        .__repr__()
        for ordered in permutations(adapters)
    }
    assert len(selected_payloads) == 1
    assert {adapter.seen_input_ids[-1] for adapter in adapters} == {id(data)}


def test_mysql_sandbox_policy_accepts_only_rollback_or_dedicated_schema() -> None:
    """**Validates: Requirements 10.3**"""

    rollback = validate_mysql_sandbox(
        dialect_name="mysql",
        schema_name="ashare_workbench",
        external_transaction=True,
    )
    assert rollback.mode is MySQLSandboxMode.ROLLBACK_TRANSACTION

    dedicated = validate_mysql_sandbox(
        dialect_name="mysql",
        schema_name="ashare_golden_test_parity",
        external_transaction=False,
    )
    assert dedicated.mode is MySQLSandboxMode.DEDICATED_SCHEMA

    with pytest.raises(UnsafeMySQLTestEnvironment, match="rollback transaction"):
        validate_mysql_sandbox(
            dialect_name="mysql",
            schema_name="ashare_workbench",
            external_transaction=False,
        )
    with pytest.raises(UnsafeMySQLTestEnvironment, match="mysql dialect"):
        validate_mysql_sandbox(
            dialect_name="sqlite",
            schema_name=None,
            external_transaction=True,
        )


def test_real_mysql_check_is_attested_by_outer_rollback_transaction(db: Any) -> None:
    """**Validates: Requirements 10.3, 10.4**"""

    attestation = attest_real_mysql_sandbox(db)
    assert attestation.mode is MySQLSandboxMode.ROLLBACK_TRANSACTION
    assert attestation.external_transaction is True


# ---------------------------------------------------------------------------
# Task 0.9: cross-module canonical projection adapters (Layer 1 evidence only).
# ---------------------------------------------------------------------------


def _projection_cases() -> Mapping[str, Any]:
    return load_projection_golden_cases(FIXTURE_PATH)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _assert_payload_matches_golden(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    context: str,
) -> None:
    plain = _thaw(expected)
    assert set(actual) == set(plain), (
        f"{context}: golden field set drift: {sorted(set(plain) ^ set(actual))}"
    )
    for key in sorted(plain):
        assert actual[key] == plain[key], (
            f"{context}.{key}: expected {plain[key]!r}, got {actual[key]!r}"
        )


def test_evidence_eligibility_adapter_matches_fixed_golden_fields() -> None:
    """**Validates: Requirements 1.10**"""

    data = load_golden_fixture(FIXTURE_PATH)
    cases = _projection_cases()["evidence_eligibility"]["cases"]
    assert {case["case_id"] for case in cases} == {
        "formal-live-eligible",
        "formal-late-generation",
        "formal-future-data-as-of",
        "formal-unverified-snapshot-id",
        "formal-unverified-code-revision",
        "historical-replay-reconstruction",
        "backtest-research",
    }
    adapter = EvidenceEligibilityAdapter()
    for case in cases:
        facts = {
            **case["input"],
            "execution_policy_fingerprint": data.execution_policy.fingerprint(),
        }
        projection = adapter.run(facts)
        assert projection.layer == LAYER_1_EVIDENCE_LAYER
        _assert_payload_matches_golden(
            case["expected"],
            projection.canonical_payload(),
            f"evidence:{case['case_id']}",
        )


def test_execution_adapter_matches_fixed_golden_fields_under_t_plus_one() -> None:
    """**Validates: Requirements 1.10, 3.8**"""

    data = load_golden_fixture(FIXTURE_PATH)
    expected_by_id = _projection_cases()["execution"]["expected_by_recommendation_id"]
    adapter = TPlusOneExecutionAdapter()
    for recommendation in data.recommendations():
        projection = adapter.run(
            recommendation,
            data.bars_for(recommendation),
            data.execution_policy,
        )
        assert projection.layer == LAYER_1_EVIDENCE_LAYER
        _assert_payload_matches_golden(
            expected_by_id[recommendation.recommendation_id],
            projection.canonical_payload(),
            f"execution:{recommendation.recommendation_id}",
        )


def test_execution_adapter_has_zero_same_day_entry_exit_samples() -> None:
    """**Validates: Requirements 3.3**"""

    data = load_golden_fixture(FIXTURE_PATH)
    adapter = TPlusOneExecutionAdapter()
    projections = [
        adapter.run(recommendation, data.bars_for(recommendation), data.execution_policy)
        for recommendation in data.recommendations()
    ]
    executed = [projection for projection in projections if projection.entry_triggered]
    assert executed, "golden fixture must contain executed samples"
    same_day = [
        projection.recommendation_id
        for projection in executed
        if projection.entry_date == projection.exit_date
    ]
    assert same_day == []
    assert all(projection.exit_date > projection.entry_date for projection in executed)


def test_outcome_identity_adapter_matches_fixed_golden_fields() -> None:
    """**Validates: Requirements 1.10**"""

    adapter = OutcomeIdentityAdapter()
    for case in _projection_cases()["outcome_identity"]["cases"]:
        projection = adapter.run(case["input"])
        assert projection.layer == LAYER_1_EVIDENCE_LAYER
        _assert_payload_matches_golden(
            case["expected"],
            projection.canonical_payload(),
            f"outcome:{case['case_id']}",
        )


def test_outcome_identity_adapter_fails_closed_on_version_rewrite() -> None:
    """**Validates: Requirements 1.10**"""

    case = _projection_cases()["outcome_identity"]["cases"][0]
    original = case["input"]["versions"][0]
    tampered = {**original, "result_fingerprint": "9" * 64}
    rewritten = {**case["input"], "versions": [original, tampered]}
    with pytest.raises(GoldenAdapterContractError, match="conflicts with the appended immutable version"):
        OutcomeIdentityAdapter().run(rewritten)


def test_risk_decision_adapter_matches_fixed_golden_fields() -> None:
    """**Validates: Requirements 1.10, 7.8**"""

    adapter = MinCapRiskDecisionAdapter()
    decisions: dict[str, str] = {}
    for case in _projection_cases()["risk_decision"]["cases"]:
        projection = adapter.run(case["input"])
        assert projection.layer == LAYER_1_EVIDENCE_LAYER
        _assert_payload_matches_golden(
            case["expected"],
            projection.canonical_payload(),
            f"risk:{case['case_id']}",
        )
        decisions[case["case_id"]] = projection.decision.value
    assert decisions == {
        "risk-allow-full-request": "allow",
        "risk-reduce-single-name": "reduce",
        "risk-reduce-gross-headroom": "reduce",
        "risk-reject-zero-headroom": "reject",
        "risk-halt-daily-loss": "halt",
        "risk-halt-drawdown": "halt",
        "risk-halt-state-halted": "halt",
    }


def test_cross_module_projection_adapters_are_byte_deterministic_across_reruns() -> None:
    """**Validates: Requirements 1.10**"""

    data = load_golden_fixture(FIXTURE_PATH)
    section = _projection_cases()

    def run_all() -> dict[str, str]:
        payloads: dict[str, str] = {}
        evidence = EvidenceEligibilityAdapter()
        for case in section["evidence_eligibility"]["cases"]:
            projection = evidence.run(
                {
                    **case["input"],
                    "execution_policy_fingerprint": data.execution_policy.fingerprint(),
                }
            )
            payloads[f"evidence:{case['case_id']}"] = projection.to_canonical_json()
        execution = TPlusOneExecutionAdapter()
        for recommendation in data.recommendations():
            projection = execution.run(
                recommendation,
                data.bars_for(recommendation),
                data.execution_policy,
            )
            payloads[f"execution:{recommendation.recommendation_id}"] = projection.to_canonical_json()
        outcome = OutcomeIdentityAdapter()
        for case in section["outcome_identity"]["cases"]:
            projection = outcome.run(case["input"])
            payloads[f"outcome:{case['case_id']}"] = projection.to_canonical_json()
        risk = MinCapRiskDecisionAdapter()
        for case in section["risk_decision"]["cases"]:
            projection = risk.run(case["input"])
            payloads[f"risk:{case['case_id']}"] = projection.to_canonical_json()
        return payloads

    first = run_all()
    second = run_all()
    assert first == second
    assert len(first) == 7 + 5 + 2 + 7
    for payload in first.values():
        assert '"layer":"layer_1"' in payload


def test_projection_layer_marker_rejects_non_layer_1_values() -> None:
    """**Validates: Requirements 1.10**"""

    data = load_golden_fixture(FIXTURE_PATH)
    recommendation = data.recommendations()[0]
    projection = TPlusOneExecutionAdapter().run(
        recommendation,
        data.bars_for(recommendation),
        data.execution_policy,
    )
    assert projection.layer == LAYER_1_EVIDENCE_LAYER == "layer_1"
    with pytest.raises(GoldenAdapterContractError, match="layer_1"):
        replace(projection, layer="accepted")

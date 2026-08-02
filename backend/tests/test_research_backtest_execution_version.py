"""P0 research runner canonical execution-version regression tests."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.backtest.research_runner as research_runner_module
from app.engine.backtest import BacktestRequest, BacktestValidationError, ExecutionPolicy, SingleDate
from app.engine.skill_loader import StrategySkill
from app.engine.strategy import build_registry_from_skills, build_strategy_descriptor, run_strategy
from app.services.backtest.research_runner import ResearchBacktestRunner, _research_execution_version
from app.services.factors import FactorRegistry
from app.services.runs.contracts import (
    CanonicalExecutionVersionBuilder,
    ExecutionPolicyIdentity,
    ExecutionVersionInput,
    StrategyExecutionFingerprint,
    application_identity_from_environment,
)


class _PlanFactor:
    def __init__(self, factor_id: str) -> None:
        self.id = factor_id
        self.output_fields = (f"{factor_id}_output",)
        self.depends_on = ()
        self.metadata = {}

    def compute(self, stock, ctx):
        return {self.output_fields[0]: stock.get("code")}


def _factor_plan(factor_id: str):
    registry = FactorRegistry()
    registry.register(_PlanFactor(factor_id))
    return registry.bootstrap()


def _skill(*, capabilities: tuple[str, ...] = ("signal_evaluation",)) -> StrategySkill:
    return StrategySkill(
        skill_id="p0-execution-version-strategy",
        name="P0 Execution Version 测试策略",
        version="1.0.0",
        path=Path("/nonexistent/p0-execution-version-strategy"),
        manifest={
            "id": "p0-execution-version-strategy",
            "name": "P0 Execution Version 测试策略",
            "version": "1.0.0",
            "mode": "declarative",
            "status": "active",
            "requirements": ["stock_feature_context"],
            "capabilities": list(capabilities),
            "enabled": True,
        },
        skill_frontmatter={},
        skill_body="p0 execution version descriptor fixture",
        skill_hash="p0-execution-version-skill-hash",
        reference_files=[],
        contract={
            "id": "p0-execution-version-strategy",
            "name": "P0 Execution Version 测试策略",
            "scoring": {"base": 50, "rules": []},
        },
        contract_hash="p0-execution-version-contract-hash",
        enabled=True,
    )


def _implementation_one(*_args, **_kwargs):
    return None


def _implementation_two(*_args, **_kwargs):
    return None


def _request(policy: ExecutionPolicy | None = None) -> BacktestRequest:
    return BacktestRequest(
        strategy_id="p0-execution-version-strategy",
        date_selector=SingleDate("2026-07-15"),
        execution_policy=policy or ExecutionPolicy(
            entry_lookahead_days=2,
            horizon_days=5,
            commission_rate=0.0001,
            stamp_tax_rate=0.0005,
            entry_slippage_bps=10.0,
            exit_slippage_bps=10.0,
            stop_loss_pct=5.0,
            take_profit_pct=10.0,
            max_entry_gap_pct=3.0,
        ),
    )


def test_p0_execution_version_is_canonical_and_tracks_real_runtime_inputs(monkeypatch):
    request = _request()
    descriptor = build_strategy_descriptor(_skill(), implementation=_implementation_one)
    manifest_changed = build_strategy_descriptor(
        _skill(capabilities=("signal_evaluation", "research_backtest")),
        implementation=_implementation_one,
    )
    implementation_changed = build_strategy_descriptor(_skill(), implementation=run_strategy)
    factor_plan = _factor_plan("first-factor")
    changed_factor_plan = _factor_plan("second-factor")
    monkeypatch.setattr(research_runner_module, "bootstrap_default_factor_registry", lambda: factor_plan)

    version = _research_execution_version(request, descriptor)
    expected = CanonicalExecutionVersionBuilder().build(
        ExecutionVersionInput(
            application=application_identity_from_environment(),
            request_schema_version=request.schema_version,
            execution_policy=ExecutionPolicyIdentity(
                policy_id="research-backtest-execution",
                policy_version=f"v{request.execution_policy.schema_version}",
                settings={
                    **request.execution_policy.canonical_payload(),
                    "minimum_signal_score": 70.0,
                    "candidate_evidence_scope": "all",
                },
            ),
            strategy_fingerprints=(StrategyExecutionFingerprint.from_descriptor(descriptor),),
            factor_execution_plan_fingerprint=factor_plan.fingerprint,
        )
    )

    assert version == expected
    assert version.startswith("execution-v1:")
    assert "research-backtest-runner" not in version
    assert _research_execution_version(request, manifest_changed) != version
    assert _research_execution_version(request, implementation_changed) != version

    monkeypatch.setattr(research_runner_module, "bootstrap_default_factor_registry", lambda: changed_factor_plan)
    assert _research_execution_version(request, descriptor) != version

    monkeypatch.setattr(research_runner_module, "bootstrap_default_factor_registry", lambda: factor_plan)
    changed_policy_request = replace(request, execution_policy=replace(request.execution_policy, horizon_days=6))
    minimum_score_changed_request = replace(request, parameter_set={"minimum_signal_score": 80.0})
    assert _research_execution_version(changed_policy_request, descriptor) != version
    assert _research_execution_version(minimum_score_changed_request, descriptor) != version
    assert (
        _research_execution_version(
            request,
            descriptor,
            candidate_evidence_scope="actionable",
        )
        != version
    )


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _Repository:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.claim_payload: dict[str, object] | None = None

    def claim_request(self, **payload):
        self.claim_payload = payload
        return SimpleNamespace(
            backtest_id="btr_existing",
            reused=True,
            reuse_reason="duplicate_request",
        )

    def get_run(self, _backtest_id: str):
        return SimpleNamespace()


def test_p0_runner_claim_uses_the_canonical_execution_version(monkeypatch):
    request = _request()
    descriptor = build_strategy_descriptor(_skill(), implementation=_implementation_one)
    factor_plan = _factor_plan("runner-factor")
    monkeypatch.setattr(research_runner_module, "bootstrap_default_factor_registry", lambda: factor_plan)
    monkeypatch.setattr(research_runner_module, "_project_run", lambda *_args, **_kwargs: {"status": "reused"})

    session = _Session()
    repository = _Repository(session)
    runner = ResearchBacktestRunner(repository, snapshot_builder=object())
    monkeypatch.setattr(runner, "_persist_bundle", lambda _bundle: None)
    bundle = SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="snapshot-canonical-version"))

    result = runner._execute(request, descriptor, object(), bundle)

    assert result == {"status": "reused"}
    assert session.commits == 1
    assert repository.claim_payload is not None
    assert repository.claim_payload["execution_version"] == _research_execution_version(request, descriptor)
    assert repository.claim_payload["execution_version"].startswith("execution-v1:")
    assert repository.claim_payload["execution_policy_fingerprint"] == request.execution_policy.fingerprint()


class _CaptureGuardSnapshotBuilder:
    def __init__(self) -> None:
        self.called = False

    def capture(self, _request: BacktestRequest, **_kwargs):
        self.called = True
        return SimpleNamespace(
            snapshot=SimpleNamespace(
                snapshot_id="snapshot-should-not-be-captured",
                artifacts_by_signal_date={},
                coverage_by_signal_date={},
                missing_requirements=(),
            ),
            signal_dates=(),
        )


def test_p0_runner_rejects_undeclared_parameters_before_snapshot_capture() -> None:
    session = _Session()
    snapshot_builder = _CaptureGuardSnapshotBuilder()
    runner = ResearchBacktestRunner(
        _Repository(session),
        strategy_registry=build_registry_from_skills([_skill()]),
        snapshot_builder=snapshot_builder,
    )

    with pytest.raises(BacktestValidationError, match="undeclared"):
        runner.preflight(replace(_request(), parameter_set={"unsafe": True}))

    assert snapshot_builder.called is False

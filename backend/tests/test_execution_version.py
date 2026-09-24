"""ExecutionVersionBuilder contract and recommendation-run persistence tests."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.engine.skill_loader import StrategySkill
from app.engine.strategy import build_strategy_descriptor, run_strategy
from app.models.tables import RecommendationRun
from app.services import workbench_service
from app.services.factors import FactorRegistry
from app.services.runs.contracts import (
    ApplicationIdentity,
    CanonicalExecutionVersionBuilder,
    ExecutionPolicyIdentity,
    ExecutionVersionInput,
    RunContractError,
    RunErrorCode,
    StrategyExecutionFingerprint,
    application_identity_from_environment,
    parse_run_request,
    selected_recommendation_execution_policy,
)
from app.services.runs.repository import CreateQueuedRun
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository


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


def _strategy_skill(
    *,
    capabilities: tuple[str, ...] = ("signal_evaluation",),
) -> StrategySkill:
    return StrategySkill(
        skill_id="execution-version-strategy",
        name="Execution Version 测试策略",
        version="1.0.0",
        path=Path("/nonexistent/execution-version-strategy"),
        manifest={
            "id": "execution-version-strategy",
            "name": "Execution Version 测试策略",
            "version": "1.0.0",
            "mode": "declarative",
            "status": "active",
            "requirements": ["stock_feature_context"],
            "capabilities": list(capabilities),
            "enabled": True,
        },
        skill_frontmatter={},
        skill_body="execution version descriptor fixture",
        skill_hash="execution-version-skill-hash",
        reference_files=[],
        contract={
            "id": "execution-version-strategy",
            "name": "Execution Version 测试策略",
            "scoring": {"base": 50, "rules": []},
        },
        contract_hash="execution-version-contract-hash",
        enabled=True,
    )


def _implementation_one(*_args, **_kwargs):
    return None


def _implementation_two(*_args, **_kwargs):
    return None


def _version_input(
    *,
    revision: str = "a1b2c3d4",
    factor_plan: str = "factor-plan-a",
    policy_settings: dict[str, object] | None = None,
) -> ExecutionVersionInput:
    return ExecutionVersionInput(
        application=ApplicationIdentity(version="2026.07.15", revision=revision),
        request_schema_version=1,
        execution_policy=ExecutionPolicyIdentity(
            policy_id="recommendation-run",
            policy_version="v1",
            settings=policy_settings
            or {
                "data_source": "local_pool",
                "disable_agent_review": True,
                "risk_template": "balanced",
                "skip_aux_refresh": False,
            },
        ),
        # Deliberately reverse order: the builder must canonicalize Descriptor
        # contributions by strategy_id rather than caller insertion order.
        strategy_fingerprints=(
            StrategyExecutionFingerprint(
                strategy_id="zeta",
                effective_version="strategy-z-v1",
                manifest_fingerprint="manifest-z",
                implementation_fingerprint="implementation-z",
            ),
            StrategyExecutionFingerprint(
                strategy_id="alpha",
                effective_version="strategy-a-v1",
                manifest_fingerprint="manifest-a",
                implementation_fingerprint="implementation-a",
            ),
        ),
        factor_execution_plan_fingerprint=factor_plan,
    )


def _version_for_descriptor(descriptor, factor_plan, *, policy_settings: dict[str, object] | None = None) -> str:
    return CanonicalExecutionVersionBuilder().build(
        ExecutionVersionInput(
            application=ApplicationIdentity(version="2026.07.15", revision="a1b2c3d4"),
            request_schema_version=1,
            execution_policy=ExecutionPolicyIdentity(
                policy_id="recommendation-run",
                policy_version="v1",
                settings=policy_settings or {"data_source": "local_pool", "skip_aux_refresh": False},
            ),
            strategy_fingerprints=(StrategyExecutionFingerprint.from_descriptor(descriptor),),
            factor_execution_plan_fingerprint=factor_plan.fingerprint,
        )
    )


def test_execution_version_canonicalization_is_deterministic_and_tracks_all_inputs():
    builder = CanonicalExecutionVersionBuilder()
    first = _version_input(
        policy_settings={
            "risk_template": "balanced",
            "skip_aux_refresh": False,
            "disable_agent_review": True,
            "data_source": "local_pool",
        }
    )
    second = _version_input(
        policy_settings={
            "data_source": "local_pool",
            "disable_agent_review": True,
            "skip_aux_refresh": False,
            "risk_template": "balanced",
        }
    )

    assert first.to_canonical_json() == second.to_canonical_json()
    assert builder.build(first) == builder.build(second)
    assert builder.build(first).startswith("execution-v1:")
    assert first.to_canonical_payload()["strategy_fingerprints"][0]["strategy_id"] == "alpha"

    assert builder.build(_version_input(revision="new-revision")) != builder.build(first)
    assert builder.build(_version_input(factor_plan="factor-plan-b")) != builder.build(first)
    assert builder.build(
        _version_input(policy_settings={"data_source": "realtime", "risk_template": "balanced"})
    ) != builder.build(first)


def test_real_descriptor_factor_plan_and_policy_changes_change_execution_version():
    skill = _strategy_skill()
    descriptor = build_strategy_descriptor(skill, implementation=_implementation_one)
    manifest_changed = build_strategy_descriptor(
        _strategy_skill(capabilities=("signal_evaluation", "research_backtest")),
        implementation=_implementation_one,
    )
    implementation_changed = build_strategy_descriptor(skill, implementation=run_strategy)
    factor_plan = _factor_plan("first-factor")
    changed_factor_plan = _factor_plan("second-factor")

    baseline = _version_for_descriptor(descriptor, factor_plan)

    assert StrategyExecutionFingerprint.from_descriptor(descriptor).to_canonical_payload() == {
        "strategy_id": descriptor.strategy_id,
        "effective_version": descriptor.effective_version,
        "manifest_fingerprint": descriptor.manifest_hash,
        "implementation_fingerprint": descriptor.implementation_hash,
    }
    assert _version_for_descriptor(manifest_changed, factor_plan) != baseline
    assert _version_for_descriptor(implementation_changed, factor_plan) != baseline
    assert _version_for_descriptor(descriptor, changed_factor_plan) != baseline
    assert _version_for_descriptor(
        descriptor,
        factor_plan,
        policy_settings={"data_source": "local_pool", "skip_aux_refresh": True},
    ) != baseline


def test_workbench_identity_uses_selected_registry_descriptors_and_audits_them():
    skill = _strategy_skill()
    request = parse_run_request(
        {
            "run_name": "descriptor-backed queued execution",
            "run_mode": "experiment",
            "skills": [{"skill_id": skill.skill_id}],
        }
    )
    selected_skills = [{"skill_id": skill.skill_id, "skill": skill, "weight": 1.0}]

    version, audit = workbench_service._execution_version_identity(request, selected_skills)
    descriptor = build_strategy_descriptor(skill)
    expected = CanonicalExecutionVersionBuilder().build(
        ExecutionVersionInput(
            application=application_identity_from_environment(),
            request_schema_version=request.schema_version,
            execution_policy=selected_recommendation_execution_policy(request),
            strategy_fingerprints=(StrategyExecutionFingerprint.from_descriptor(descriptor),),
            factor_execution_plan_fingerprint=audit["factor_plan"]["fingerprint"],
        )
    )

    assert version == expected
    assert audit["strategy_fingerprints"] == [
        StrategyExecutionFingerprint.from_descriptor(descriptor).to_canonical_payload()
    ]


def test_execution_version_rejects_credential_shaped_policy_and_revision_values():
    with pytest.raises(RunContractError) as policy_error:
        ExecutionPolicyIdentity(
            policy_id="recommendation-run",
            policy_version="v1",
            settings={"routing": {"api-token": "must-never-be-hashed-or-persisted"}},
        )
    assert policy_error.value.code == RunErrorCode.CREDENTIAL_IN_REQUEST_FORBIDDEN.value

    with pytest.raises(RunContractError) as revision_error:
        ApplicationIdentity(version="2026.07.15", revision="secret-build-value")
    assert revision_error.value.code == RunErrorCode.CREDENTIAL_IN_REQUEST_FORBIDDEN.value


@pytest.mark.parametrize("execution_version", (None, "", " \t "))
def test_create_queued_run_rejects_blank_execution_version(execution_version):
    with pytest.raises(ValueError, match="execution_version"):
        CreateQueuedRun(
            run_id="run-empty-execution-version",
            request=parse_run_request({"run_name": "blank execution version", "run_mode": "experiment"}),
            execution_version=execution_version,
            created_at=datetime(2026, 7, 15, 9, 0, 0),
        )


def test_execution_version_is_persisted_with_the_canonical_queued_run():
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    session = Session(engine)
    try:
        request = parse_run_request({"run_name": "execution version persistence", "run_mode": "experiment"})
        execution_version = CanonicalExecutionVersionBuilder().build(_version_input())
        repository = SQLAlchemyRunRepository(session)

        created = repository.create_queued_run(
            CreateQueuedRun(
                run_id="run-execution-version",
                request=request,
                execution_version=execution_version,
                created_at=datetime(2026, 7, 15, 9, 0, 0),
            )
        )
        assert created.applied
        session.commit()

        persisted = session.get(RecommendationRun, "run-execution-version")
        record = repository.get_run("run-execution-version")
        assert persisted is not None
        assert persisted.execution_version == execution_version
        assert record is not None
        assert record.execution_version == execution_version
    finally:
        session.close()
        engine.dispose()


def test_historic_nullable_execution_version_remains_readable():
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    session = Session(engine)
    try:
        repository = SQLAlchemyRunRepository(session)
        created = repository.create_queued_run(
            CreateQueuedRun(
                run_id="historic-null-execution-version",
                request=parse_run_request({"run_name": "historic queued run", "run_mode": "experiment"}),
                execution_version="execution-v1:historic-placeholder",
                created_at=datetime(2026, 7, 15, 9, 0, 0),
            )
        )
        assert created.applied
        session.commit()

        historic_row = session.get(RecommendationRun, "historic-null-execution-version")
        assert historic_row is not None
        historic_row.execution_version = None
        session.commit()
        session.expire_all()

        record = repository.get_run("historic-null-execution-version")
        assert record is not None
        assert record.execution_version is None
    finally:
        session.close()
        engine.dispose()


def _execution_audit(
    *,
    revision: str,
    policy_fingerprint: str = "policy-fingerprint",
    factor_fingerprint: str = "factor-fingerprint",
    strategy_version: str = "strategy-a-v1",
) -> dict[str, object]:
    """Shape one persisted execution audit exactly as ``_execution_version_identity`` does."""
    return {
        "execution_version_schema_version": 1,
        "application": {"revision": revision, "version": "development"},
        "request_schema_version": 1,
        "strategy_fingerprints": [
            {
                "strategy_id": "alpha",
                "effective_version": strategy_version,
                "manifest_fingerprint": "manifest-a",
                "implementation_fingerprint": "implementation-a",
            }
        ],
        "factor_plan": {
            "contract_version": "factor-execution-plan/v1",
            "fingerprint": factor_fingerprint,
            "factor_ids": ["pos20"],
        },
        "execution_policy": {
            "policy_id": "recommendation-run",
            "policy_version": "v1",
            "fingerprint": policy_fingerprint,
        },
    }


def _run_with_enqueue_audit(audit: dict[str, object], execution_version: str) -> SimpleNamespace:
    return SimpleNamespace(
        execution_version=execution_version,
        result_summary_json=json.dumps(
            {"agent_runtime": "queued", "execution": {**audit, "execution_version": execution_version}}
        ),
    )


def test_persist_execution_version_accepts_a_deployment_revision_that_moved_mid_flight():
    """The API and the worker pin git HEAD at their own startup; a commit landing
    between enqueue and execution must not brick the run (L-068 follow-up)."""
    run = _run_with_enqueue_audit(_execution_audit(revision="a" * 40), "execution-v1:enqueued")

    workbench_service._persist_execution_version(
        run,
        "execution-v1:executing",
        _execution_audit(revision="b" * 40),
    )

    # The executing process is authoritative for what actually ran.
    assert run.execution_version == "execution-v1:executing"


@pytest.mark.parametrize(
    "drift",
    (
        {"policy_fingerprint": "policy-changed"},
        {"factor_fingerprint": "factor-changed"},
        {"strategy_version": "strategy-a-v2"},
    ),
)
def test_persist_execution_version_rejects_behaviour_drift_and_prints_both_sides(drift):
    run = _run_with_enqueue_audit(_execution_audit(revision="a" * 40), "execution-v1:enqueued")

    with pytest.raises(RuntimeError) as error:
        workbench_service._persist_execution_version(
            run,
            "execution-v1:executing",
            _execution_audit(revision="a" * 40, **drift),
        )

    message = str(error.value)
    assert "入队=" in message and "执行=" in message
    assert run.execution_version == "execution-v1:enqueued"


def test_persist_execution_version_keeps_strict_compare_without_a_diffable_audit():
    run = SimpleNamespace(execution_version=None, result_summary_json=None)

    workbench_service._persist_execution_version(run, "execution-v1:first")

    assert run.execution_version == "execution-v1:first"
    with pytest.raises(RuntimeError, match="execution_version"):
        workbench_service._persist_execution_version(run, "execution-v1:changed")
    assert run.execution_version == "execution-v1:first"

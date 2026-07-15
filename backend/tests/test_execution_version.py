"""ExecutionVersionBuilder contract and recommendation-run persistence tests."""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.runs.contracts import (
    ApplicationIdentity,
    CanonicalExecutionVersionBuilder,
    ExecutionPolicyIdentity,
    ExecutionVersionInput,
    RunContractError,
    RunErrorCode,
    StrategyExecutionFingerprint,
    parse_run_request,
)
from app.services.runs.repository import CreateQueuedRun
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository


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

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import RecommendationRun
from app.services.runs.contracts import (
    RunActor,
    RunTrigger,
    parse_run_request,
)
from app.services.runs.coordinator import (
    RunCoordinator,
    RunCoordinatorDependencies,
    RunContext,
    StageData,
    WorkerLease,
)
from app.services.runs.pipeline import _commit_stage_progress
from app.services.runs.repository import (
    CreateQueuedRun,
    LeaseRequest,
    TransitionCommit,
)
from app.services.runs.skill_gate import resolve_skill_review_concurrency
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from app.services.runs.stages.adapters import ProductionReviewExecutor
from app.services.runs.stages.review import ReviewStage
from app.services.runs.state_machine import RunStateMachine


NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _repository() -> tuple[Session, SQLAlchemyRunRepository]:
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    session = Session(engine)
    return session, SQLAlchemyRunRepository(session)


def _start_and_lease(repo: SQLAlchemyRunRepository, session: Session, run_id: str):
    created = repo.create_queued_run(
        CreateQueuedRun(
            run_id=run_id,
            request=parse_run_request({"run_mode": "experiment", "run_name": run_id}),
            execution_version="exec-test-v1",
            created_at=NOW,
        )
    )
    assert created.applied
    session.commit()
    record = repo.get_run(run_id)
    assert record is not None
    started = RunStateMachine.transition(
        current_status=record.status,
        trigger=RunTrigger.START,
        actor=RunActor.DISPATCHER,
    )
    assert repo.compare_and_set_transition(
        run_id,
        TransitionCommit(expected_revision=record.revision, transition=started, now=NOW),
    ).applied
    session.commit()
    record = repo.get_run(run_id)
    assert record is not None
    acquired = repo.acquire_lease(
        run_id,
        LeaseRequest(
            owner_id="worker-test",
            now=NOW + timedelta(seconds=1),
            ttl=timedelta(seconds=1200),
            expected_revision=record.revision,
        ),
    )
    assert acquired.applied and acquired.record is not None and acquired.record.lease is not None
    session.commit()
    return acquired.record


def _worker_lease(record) -> WorkerLease:
    assert record is not None
    assert record.lease is not None
    return WorkerLease(
        run_id=record.run_id,
        token=record.lease.token,
        revision=record.revision,
    )


def test_zero_review_concurrency_uses_configured_default():
    settings = SimpleNamespace(market_skill_review_concurrency=10)

    assert resolve_skill_review_concurrency({"agent_review_concurrency": 0}, settings) == 10
    assert resolve_skill_review_concurrency({"agent_review_concurrency": 3}, settings) == 3


def test_review_stage_passes_configured_default_to_executor(monkeypatch):
    seen = {}

    class Executor:
        async def review_candidates(self, candidates, **kwargs):
            seen["concurrency"] = kwargs["concurrency"]
            return tuple(candidates)

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(market_skill_review_concurrency=10),
    )
    context = RunContext(
        run_id="run-review-default",
        request=parse_run_request({"run_mode": "formal", "agent_review_concurrency": 0}),
        worker_lease=WorkerLease("run-review-default", "lease", 1),
    )
    stage = ReviewStage(review_executor=Executor())

    asyncio.run(stage.execute(context, StageData(params={"agent_review_concurrency": 0})))

    assert seen["concurrency"] == 10


def test_formal_review_stage_keeps_rule_fallback_when_every_model_review_fails(monkeypatch):
    class Executor:
        async def review_candidates(self, candidates, **_kwargs):
            return tuple(
                {
                    **candidate,
                    "skill_reviews": [
                        {
                            "skill_id": "fake-skill",
                            "review": None,
                            "error": "模型熔断: circuit open",
                        }
                    ],
                }
                for candidate in candidates
            )

    class Costs:
        def record_costs(self, _run_id):
            return (
                {
                    "status": "circuit_open",
                    "error_message": "模型熔断: circuit open",
                },
            )

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(market_skill_review_concurrency=2),
    )
    context = RunContext(
        run_id="run-formal-review-unavailable",
        request=parse_run_request({"run_mode": "formal"}),
        worker_lease=WorkerLease("run-formal-review-unavailable", "lease", 1),
    )
    data = StageData(
        params={"run_mode": "formal"},
        skill_candidates=(
            {
                "stock": {"code": "600000"},
                "review_skill_ids": ["fake-skill"],
            },
        ),
    )

    output = asyncio.run(
        ReviewStage(review_executor=Executor(), cost_recording=Costs()).execute(
            context,
            data,
        )
    )

    assert output.data.candidate_results[0]["skill_reviews"][0]["review"] is None
    assert output.data.skill_review_failure_audit["requested_count"] == 1
    assert output.data.skill_review_failure_audit["success_count"] == 0
    assert output.data.skill_review_failure_audit["reason_counts"] == {
        "model_circuit_open": 1,
    }
    assert output.data.pipeline_audit["rule_only_fallback_count"] == 1


def test_experiment_review_stage_keeps_degraded_result_when_models_fail(monkeypatch):
    class Executor:
        async def review_candidates(self, candidates, **_kwargs):
            return tuple(
                {
                    **candidate,
                    "skill_reviews": [
                        {
                            "skill_id": "fake-skill",
                            "review": None,
                            "error": "余额不足",
                        }
                    ],
                }
                for candidate in candidates
            )

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(market_skill_review_concurrency=2),
    )
    context = RunContext(
        run_id="run-experiment-review-unavailable",
        request=parse_run_request({"run_mode": "experiment"}),
        worker_lease=WorkerLease("run-experiment-review-unavailable", "lease", 1),
    )
    data = StageData(
        params={"run_mode": "experiment"},
        skill_candidates=(
            {
                "stock": {"code": "600000"},
                "review_skill_ids": ["fake-skill"],
            },
        ),
    )

    output = asyncio.run(ReviewStage(review_executor=Executor()).execute(context, data))

    assert output.data.skill_review_failure_audit["success_count"] == 0
    assert output.data.skill_review_failure_audit["failed_count"] == 1


def test_production_review_executor_bounds_fake_provider_concurrency(monkeypatch):
    """L1 acceptance: exercise production semaphore with no real model call."""
    active = 0
    peak = 0

    async def fake_run_skill_reviews(*, request_limiter, **_kwargs):
        nonlocal active, peak
        async with request_limiter:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
        return []

    monkeypatch.setattr("app.agents.review_runner.run_skill_reviews", fake_run_skill_reviews)
    candidates = tuple(
        {
            "stock": {"code": f"{index:06d}"},
            "review_skill_ids": ["fake-skill"],
            "rule_signals": {},
        }
        for index in range(12)
    )
    selected_skills = ({"skill_id": "fake-skill", "skill": SimpleNamespace()},)

    results = asyncio.run(
        ProductionReviewExecutor().review_candidates(
            candidates,
            selected_skills=selected_skills,
            snapshot={},
            session_id="fake-session",
            concurrency=10,
            agent_review_disabled=False,
        )
    )

    assert len(results) == 12
    assert peak == 10


def test_stage_checkpoint_is_included_before_progress_commit(monkeypatch):
    """Lease is renewed and the checkpoint projection lands before progress."""
    session, repo = _repository()
    try:
        record = _start_and_lease(repo, session, "run-stage")
        run = db_model = SimpleNamespace(checkpoint_phase=None)
        calls = []

        class FakeDb:
            def get(self, _model, _run_id):
                return run

        def fake_update(_db, run_id, *, phase, current, total, message, status, renew_lease):
            assert run.checkpoint_phase == phase
            calls.append((run_id, phase, current, total, message, status))

        monkeypatch.setattr("app.services.workbench_service._update_run_progress", fake_update)

        renewed = _commit_stage_progress(
            FakeDb(),
            coordinator=RunCoordinator.from_dependencies(
                RunCoordinatorDependencies(repository=repo)
            ),
            worker_lease=_worker_lease(record),
            phase="strategy_evaluation",
        )

        assert calls == [
            ("run-stage", "strategy_evaluation", 1, 1, "阶段 strategy_evaluation 完成", "running")
        ]
        assert renewed.run_id == "run-stage"
        assert db_model.checkpoint_phase == "strategy_evaluation"
    finally:
        session.close()

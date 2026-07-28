from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.runs.contracts import parse_run_request
from app.services.runs.coordinator import RunContext, StageData, WorkerLease
from app.services.runs.pipeline import _commit_stage_progress
from app.services.runs.skill_gate import resolve_skill_review_concurrency
from app.services.runs.stages.adapters import ProductionReviewExecutor
from app.services.runs.stages.review import ReviewStage


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
    run = SimpleNamespace(checkpoint_phase=None)
    calls = []

    class FakeDb:
        def get(self, _model, _run_id):
            return run

    def fake_update(_db, run_id, *, phase, current, total, message, status):
        assert run.checkpoint_phase == phase
        calls.append((run_id, phase, current, total, message, status))

    monkeypatch.setattr("app.services.workbench_service._update_run_progress", fake_update)

    _commit_stage_progress(FakeDb(), "run-stage", "strategy_evaluation")

    assert calls == [
        ("run-stage", "strategy_evaluation", 1, 1, "阶段 strategy_evaluation 完成", "running")
    ]

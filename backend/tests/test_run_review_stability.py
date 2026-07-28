from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.runs.contracts import parse_run_request
from app.services.runs.coordinator import RunContext, StageData, WorkerLease
from app.services.runs.pipeline import _commit_stage_progress
from app.services.runs.skill_gate import resolve_skill_review_concurrency
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

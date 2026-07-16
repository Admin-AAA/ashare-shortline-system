"""Run-mode regression tests for durable recommendation run creation."""
from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.engine.skill_loader import load_skills
from app.models.tables import RecommendationRun
from app.services import workbench_service
from app.services.backtest_service import _persist_backtest_run
from app.services.workbench_service import enqueue_workbench_run, get_run_detail


@pytest.mark.parametrize("run_mode", ("formal", "experiment", "backtest"))
def test_enqueue_persists_each_explicit_run_mode_in_all_projections(monkeypatch, db, run_mode):
    """The real MySQL writer commits a canonical queued row before handoff."""
    assert db.get_bind().dialect.name == "mysql"
    dispatched: list[str] = []

    def capture_handoff(run_id: str) -> None:
        # enqueue_workbench_run commits before this callback.  Expiring the session
        # prevents its identity map from making this assertion pass on stale data.
        db.expire_all()
        persisted = db.get(RecommendationRun, run_id)
        assert persisted is not None
        assert persisted.status == "queued"
        assert persisted.request_schema_version == 1
        assert persisted.request_json is not None
        assert json.loads(persisted.request_json)["run_mode"] == run_mode
        dispatched.append(run_id)

    monkeypatch.setattr(workbench_service._run_dispatcher, "enqueue", capture_handoff)
    payload = {
        "single_skill_id": "chip-single-peak-breakout",
        "run_mode": run_mode,
        "run_name": f"{run_mode} 入队测试",
        "skip_aux_refresh": True,
        "disable_agent_review": True,
    }

    response = enqueue_workbench_run(db, payload)
    run_id = response["run"]["run_id"]
    run = db.get(RecommendationRun, run_id)
    detail = get_run_detail(db, run_id)

    assert run is not None
    assert detail is not None
    assert dispatched == [run_id]
    assert run.run_mode == run_mode
    assert run.execution_version is not None
    assert run.execution_version.startswith("execution-v1:")
    assert json.loads(run.request_json or "{}")["run_mode"] == run_mode
    assert json.loads(run.params_json)["run_mode"] == run_mode
    assert detail["run_mode"] == run_mode
    assert detail["params"]["run_mode"] == run_mode


def test_enqueue_defaults_to_formal_only_in_run_request_parser(monkeypatch, db):
    """An omitted mode receives the parser default and stays consistent everywhere."""
    monkeypatch.setattr(workbench_service._run_dispatcher, "enqueue", lambda _run_id: None)
    response = enqueue_workbench_run(
        db,
        {
            "single_skill_id": "chip-single-peak-breakout",
            "run_name": "默认 formal 测试",
            "skip_aux_refresh": True,
            "disable_agent_review": True,
        },
    )
    run = db.get(RecommendationRun, response["run"]["run_id"])

    assert run is not None
    assert run.run_mode == "formal"
    assert json.loads(run.request_json or "{}")["run_mode"] == "formal"
    assert json.loads(run.params_json)["run_mode"] == "formal"


def test_legacy_backtest_entry_persists_canonical_backtest_mode(db):
    """The legacy recommendation backtest writer cannot diverge from its request."""

    skill = next(skill for skill in load_skills(get_settings().skills_dir) if skill.executable)
    run_id = _persist_backtest_run(
        db,
        "2026-07-14",
        universe_count=10,
        recommended_count=1,
        skills=[skill],
        score_line=72.0,
    )
    run = db.get(RecommendationRun, run_id)

    assert run is not None
    assert run.run_mode == "backtest"
    assert run.execution_version is not None
    assert run.execution_version.startswith("execution-v1:")
    assert json.loads(run.request_json or "{}")["run_mode"] == "backtest"

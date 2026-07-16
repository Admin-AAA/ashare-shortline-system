"""Task 1.4 regression tests for the durable run enqueue/worker boundary."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api import runs as runs_api
from app.models.tables import RecommendationRun
from app.services import scheduler_service, workbench_service
from app.services.runs.contracts import (
    RunMode,
    RunPhase,
    RunStatus,
    parse_run_request,
)
from app.services.runs.coordinator import LegacyWorkbenchRunCoordinator
from app.services.runs.queue import BackgroundRunDispatcher
from app.services.runs.repository import (
    CheckpointCommit,
    CreateQueuedRun,
    LeaseRequest,
    RunCheckpoint,
)
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from scripts import run_workbench_subprocess


def _sqlite_session() -> tuple[object, Session]:
    engine = create_engine("sqlite:///:memory:")
    RecommendationRun.__table__.create(engine)
    return engine, Session(engine)


def _create_queued_run(
    session: Session,
    run_id: str,
    *,
    run_mode: str = "experiment",
) -> SQLAlchemyRunRepository:
    repository = SQLAlchemyRunRepository(session)
    created = repository.create_queued_run(
        CreateQueuedRun(
            run_id=run_id,
            request=parse_run_request(
                {
                    "run_name": f"queued-{run_id}",
                    "run_mode": run_mode,
                }
            ),
            execution_version="test-execution-v1",
            created_at=datetime(2026, 7, 14, 9, 0, 0),
        )
    )
    assert created.applied
    session.commit()
    return repository


def _make_interrupted_run(
    session: Session,
    run_id: str,
    *,
    run_mode: str = "experiment",
) -> SQLAlchemyRunRepository:
    repository = _create_queued_run(session, run_id, run_mode=run_mode)
    coordinator = LegacyWorkbenchRunCoordinator(repository)
    start = datetime(2026, 7, 14, 9, 0, 0)
    assert coordinator.start_dispatch(run_id, now=start)
    session.commit()

    record = repository.get_run(run_id)
    assert record is not None
    acquired = repository.acquire_lease(
        run_id,
        LeaseRequest(
            owner_id="boundary-test-worker",
            now=start + timedelta(seconds=1),
            ttl=timedelta(seconds=1),
            expected_revision=record.revision,
        ),
    )
    assert acquired.applied and acquired.record is not None
    assert acquired.record.lease is not None
    session.commit()

    checkpointed = repository.commit_checkpoint(
        run_id,
        CheckpointCommit(
            expected_revision=acquired.record.revision,
            lease_token=acquired.record.lease.token,
            checkpoint=RunCheckpoint(
                phase=RunPhase.PREPARE,
                side_effect_key=f"prepare:{run_id}",
                heartbeat_at=start + timedelta(seconds=1),
            ),
        ),
    )
    assert checkpointed.applied
    session.commit()
    assert coordinator.heartbeat_timeout(run_id, now=start + timedelta(seconds=3))
    session.commit()
    return repository


def test_enqueue_commits_canonical_queued_run_before_dispatch(monkeypatch):
    """The API facade must persist canonical state before it releases ``run_id``."""
    engine, session = _sqlite_session()
    events: list[str] = []
    original_commit = session.commit

    def commit() -> None:
        events.append("commit")
        original_commit()

    def enqueue(run_id: str) -> None:
        events.append("enqueue")
        with Session(engine) as reader:
            row = reader.get(RecommendationRun, run_id)
            assert row is not None
            assert row.status == RunStatus.QUEUED.value
            assert row.request_schema_version == 1
            assert row.request_json is not None
            assert parse_run_request(row.request_json).run_mode is RunMode.EXPERIMENT

    monkeypatch.setattr(session, "commit", commit)
    monkeypatch.setattr(workbench_service, "sync_skills", lambda _db: [])
    monkeypatch.setattr(
        workbench_service,
        "_merge_run_params",
        lambda _db, _preset, _payload: {"run_name": "API queue boundary", "run_mode": "experiment", "skills": []},
    )
    monkeypatch.setattr(workbench_service, "_select_skills_for_run", lambda *_args: [object()])
    monkeypatch.setattr(workbench_service, "_selected_skill_rows", lambda _skills: [])
    monkeypatch.setattr(
        workbench_service,
        "_execution_version_identity",
        lambda _request, _selected_skills: ("test-execution-v1", {}),
    )
    monkeypatch.setattr(workbench_service, "_new_run_id", lambda: "run-api-boundary")
    monkeypatch.setattr(
        workbench_service,
        "_serialize_run_summary",
        lambda _db, run: {"run_id": run.run_id, "status": run.status},
    )
    monkeypatch.setattr(workbench_service, "invalidate_bootstrap_cache", lambda: None)
    monkeypatch.setattr(workbench_service._run_dispatcher, "enqueue", enqueue)

    try:
        result = workbench_service.enqueue_workbench_run(
            session,
            {"run_name": "ignored-by-merge", "run_mode": "experiment"},
        )
        assert result["queued"] is True
        assert events == ["commit", "enqueue"]
    finally:
        session.close()
        engine.dispose()


def test_dispatcher_commits_running_transition_before_worker_receives_run_id():
    """The child can read a committed running row and canonical request immediately."""
    engine, setup = _sqlite_session()
    run_id = "run-dispatch-commit"
    _create_queued_run(setup, run_id)
    setup.close()
    received: list[str] = []

    def spawn(dispatched_run_id: str) -> None:
        received.append(dispatched_run_id)
        with Session(engine) as reader:
            record = SQLAlchemyRunRepository(reader).get_run(dispatched_run_id)
            assert record is not None
            assert record.status is RunStatus.RUNNING
            assert record.request.run_mode is RunMode.EXPERIMENT

    dispatcher = BackgroundRunDispatcher(
        session_factory=lambda: Session(engine),
        mark_running=workbench_service._mark_run_dispatched,
        mark_failed=workbench_service._mark_run_failed,
        spawn=spawn,
    )
    try:
        dispatcher.dispatch_one(run_id)
        assert received == [run_id]
    finally:
        engine.dispose()


def test_dispatcher_spawn_failure_is_persisted_as_stable_state_machine_failure():
    """A failed Popen leaves a durable terminal ``worker_spawn_failed`` run."""
    engine, setup = _sqlite_session()
    run_id = "run-dispatch-failure"
    _create_queued_run(setup, run_id)
    setup.close()

    def fail_spawn(_run_id: str) -> None:
        raise OSError("Popen unavailable")

    dispatcher = BackgroundRunDispatcher(
        session_factory=lambda: Session(engine),
        mark_running=workbench_service._mark_run_dispatched,
        mark_failed=workbench_service._mark_run_failed,
        spawn=fail_spawn,
    )
    try:
        dispatcher.dispatch_one(run_id)
        with Session(engine) as reader:
            record = SQLAlchemyRunRepository(reader).get_run(run_id)
            assert record is not None
            assert record.status is RunStatus.FAILED
            assert record.failure is not None
            assert record.failure.code == "worker_spawn_failed"
    finally:
        engine.dispose()


@pytest.mark.parametrize("run_mode", ("formal", "experiment", "backtest"))
def test_api_create_run_delegates_to_the_single_enqueue_application_entry(monkeypatch, run_mode):
    """Every API mode delegates untouched to shared canonical enqueue logic."""
    captured: list[dict[str, object]] = []

    def enqueue(_db, payload):
        captured.append(dict(payload))
        return {"run": {"run_id": "run-api"}, "recommendations": [], "queued": True}

    monkeypatch.setattr(runs_api, "enqueue_workbench_run", enqueue)

    result = asyncio.run(
        runs_api.create_run(
            {
                "preset_id": "preset-api",
                "run_name": "API enqueue",
                "run_mode": run_mode,
            },
            db=object(),
        )
    )

    assert result["queued"] is True
    assert captured == [
        {
            "preset_id": "preset-api",
            "run_name": "API enqueue",
            "run_mode": run_mode,
        }
    ]


@pytest.mark.parametrize("run_mode", ("formal", "experiment", "backtest"))
def test_scheduler_enqueue_and_worker_load_use_only_canonical_run_identity(monkeypatch, run_mode):
    """Scheduler delegates to enqueue, and the subprocess loads its request from Repository."""
    scheduled_payloads: list[dict[str, object]] = []

    def enqueue(_db, payload):
        scheduled_payloads.append(dict(payload))
        return {"run": {"run_id": "run-scheduler"}, "queued": True}

    monkeypatch.setattr("app.services.workbench_service.enqueue_workbench_run", enqueue)
    result = asyncio.run(
        scheduler_service._run_recommendation(
            object(),
            {
                "preset_id": "preset-scheduler",
                "run_name": "scheduler enqueue",
                "run_mode": run_mode,
                "disable_agent_review": True,
                "trade_date_override": "2026-07-14",
                "market_scope": "industry",
                "market_scope_value": "电子",
            },
        )
    )
    assert result["related_run_id"] == "run-scheduler"
    assert scheduled_payloads == [
        {
            "preset_id": "preset-scheduler",
            "run_name": "scheduler enqueue",
            "run_mode": run_mode,
            "disable_agent_review": True,
            "trade_date_override": "2026-07-14",
            "market_scope": "industry",
            "market_scope_value": "电子",
        }
    ]

    expected_request = parse_run_request({"run_name": "worker canonical", "run_mode": run_mode})
    class WorkerDb:
        closed = False

        def close(self) -> None:
            self.closed = True

    db = WorkerDb()
    captured: dict[str, object] = {}

    class Repository:
        def __init__(self, _db) -> None:
            pass

        def get_run(self, run_id: str):
            assert run_id == "run-scheduler"
            return SimpleNamespace(request=expected_request)

    async def run_workbench(received_db, request, *, existing_run_id: str):
        captured.update({"db": received_db, "request": request, "run_id": existing_run_id})

    monkeypatch.setattr(run_workbench_subprocess, "SessionLocal", lambda: db)
    monkeypatch.setattr(run_workbench_subprocess, "SQLAlchemyRunRepository", Repository)
    monkeypatch.setattr(run_workbench_subprocess, "run_workbench", run_workbench)
    monkeypatch.setattr(db, "close", lambda: setattr(db, "closed", True))

    asyncio.run(run_workbench_subprocess._run("run-scheduler"))

    assert captured == {"db": db, "request": expected_request, "run_id": "run-scheduler"}
    assert db.closed is True


def test_resume_dispatches_only_run_id_after_reloading_canonical_request(monkeypatch):
    """Recovery rejects params_json as worker input and re-enqueues only the durable ID."""
    engine, session = _sqlite_session()
    run_id = "run-resume-boundary"
    repository = _make_interrupted_run(session, run_id)
    row = session.get(RecommendationRun, run_id)
    assert row is not None
    row.params_json = "not a worker payload"
    session.commit()
    dispatched: list[str] = []

    monkeypatch.setattr(runs_api, "get_run_detail", lambda _db, _run_id: {"status": "interrupted"})
    monkeypatch.setattr(runs_api, "dispatch_workbench_run", lambda _db, dispatched_run_id: dispatched.append(dispatched_run_id))

    try:
        result = asyncio.run(runs_api.resume(run_id, session))
        record = repository.get_run(run_id)
        assert result["queued"] is True
        assert dispatched == [run_id]
        assert record is not None
        assert record.status is RunStatus.RUNNING
        assert record.request.run_mode is RunMode.EXPERIMENT
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("run_mode", ("formal", "experiment", "backtest"))
def test_worker_restart_and_recovery_preserve_each_canonical_run_mode(monkeypatch, run_mode):
    """A resumed subprocess receives the exact persisted request for every mode."""
    engine, session = _sqlite_session()
    run_id = f"run-restart-{run_mode}"
    repository = _make_interrupted_run(session, run_id, run_mode=run_mode)
    dispatched: list[str] = []
    try:
        row = session.get(RecommendationRun, run_id)
        assert row is not None
        assert row.run_mode == run_mode
        assert json.loads(row.request_json or "{}")["run_mode"] == run_mode

        monkeypatch.setattr(runs_api, "get_run_detail", lambda _db, _run_id: {"status": "interrupted"})
        monkeypatch.setattr(
            runs_api,
            "dispatch_workbench_run",
            lambda _db, dispatched_run_id: dispatched.append(dispatched_run_id),
        )

        resumed_response = asyncio.run(runs_api.resume(run_id, session))
        resumed = repository.get_run(run_id)
        assert resumed_response["queued"] is True
        assert dispatched == [run_id]
        assert resumed is not None
        assert resumed.request.run_mode.value == run_mode
        assert resumed.run_mode == run_mode

        expected_request = resumed.request
        captured: dict[str, object] = {}

        class WorkerDb:
            closed = False

            def close(self) -> None:
                self.closed = True

        worker_db = WorkerDb()

        class WorkerRepository:
            def __init__(self, _db) -> None:
                pass

            def get_run(self, received_run_id: str):
                assert received_run_id == run_id
                return SimpleNamespace(request=expected_request)

        async def worker_run(received_db, request, *, existing_run_id: str):
            captured.update({"db": received_db, "request": request, "run_id": existing_run_id})

        monkeypatch.setattr(run_workbench_subprocess, "SessionLocal", lambda: worker_db)
        monkeypatch.setattr(run_workbench_subprocess, "SQLAlchemyRunRepository", WorkerRepository)
        monkeypatch.setattr(run_workbench_subprocess, "run_workbench", worker_run)

        asyncio.run(run_workbench_subprocess._run(run_id))

        assert captured == {"db": worker_db, "request": expected_request, "run_id": run_id}
        assert worker_db.closed is True
    finally:
        session.close()
        engine.dispose()

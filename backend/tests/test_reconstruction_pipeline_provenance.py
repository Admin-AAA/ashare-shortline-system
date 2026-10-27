from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.evidence import EvidenceClass
from app.services.runs import pipeline


class _FakeDB:
    def __init__(self, run: SimpleNamespace) -> None:
        self.run = run
        self.rollbacks = 0
        self.commits = 0
        self.expired = 0

    def get(self, _model, run_id: str):
        return self.run if self.run.run_id == run_id else None

    def rollback(self) -> None:
        self.rollbacks += 1

    def commit(self) -> None:
        self.commits += 1

    def expire_all(self) -> None:
        self.expired += 1

    def flush(self) -> None:
        pass

    def scalar(self, _statement):
        return None  # 没有推荐行：最后一条推荐写入时刻为空


def _run(**overrides) -> SimpleNamespace:
    values = {
        "run_id": "run_pipeline_provenance",
        "run_mode": "formal",
        "evaluation_scope": "historical_replay",
        "evidence_class": None,
        "reconstruction_batch_id": None,
        "status": "completed",
        "progress_phase": None,
        "progress_message": None,
        "error_message": None,
        "finished_at": None,
        "heartbeat_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_unmarked_historical_replay_skips_reconstruction_finalizer(monkeypatch) -> None:
    db = _FakeDB(_run())
    finalized: list[str] = []
    monkeypatch.setattr(pipeline, "apply_formal_run_provenance", lambda run, **_kw: None)
    monkeypatch.setattr(
        "app.services.evidence.reconstruction_batch.finalize_reconstruction_run",
        lambda _db, run_id: finalized.append(run_id),
    )

    assert pipeline._finalize_completed_run_provenance(db, db.run.run_id) is False
    assert finalized == []


def test_marked_reconstruction_finalizes_after_completed_status(monkeypatch) -> None:
    db = _FakeDB(
        _run(
            evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
            reconstruction_batch_id="recon-v1-test",
        )
    )
    observed: list[tuple[str, str, str]] = []
    monkeypatch.setattr(pipeline, "apply_formal_run_provenance", lambda run, **_kw: None)
    monkeypatch.setattr(
        "app.services.evidence.reconstruction_batch.finalize_reconstruction_run",
        lambda _db, run_id: observed.append(
            (run_id, db.run.status, db.run.reconstruction_batch_id)
        ),
    )

    assert pipeline._finalize_completed_run_provenance(db, db.run.run_id) is True
    assert observed == [
        (db.run.run_id, "completed", "recon-v1-test"),
    ]


def test_failed_completion_rolls_back_then_fails_and_marks_batch(monkeypatch) -> None:
    run = _run(
        evidence_class=EvidenceClass.HISTORICAL_RECONSTRUCTION.value,
        reconstruction_batch_id="recon-v1-test",
    )
    db = _FakeDB(run)
    active_record = SimpleNamespace(status=pipeline.RunStatus.RUNNING)
    worker_lease = pipeline.WorkerLease(run.run_id, "opaque-token", 4)
    coordinator = SimpleNamespace(
        fail=lambda lease, now, message: lease == worker_lease
    )
    marked: list[tuple[str, str]] = []
    invalidated: list[bool] = []
    monkeypatch.setattr(
        pipeline.SQLAlchemyRunRepository,
        "get_run",
        lambda _self, run_id: active_record if run_id == run.run_id else None,
    )
    monkeypatch.setattr(
        "app.services.evidence.reconstruction_batch.mark_reconstruction_batch_failed",
        lambda _db, batch_id, message: marked.append((batch_id, message)),
    )
    monkeypatch.setattr(
        "app.services.workbench_service.invalidate_bootstrap_cache",
        lambda: invalidated.append(True),
    )

    pipeline._mark_pipeline_run_failed(
        db,
        run_id=run.run_id,
        coordinator=coordinator,
        worker_lease=worker_lease,
        message="seal rejected",
    )

    assert db.rollbacks == 1
    assert db.commits == 1
    assert db.expired == 1
    assert run.progress_phase == "failed"
    assert run.progress_message == "seal rejected"
    assert marked == [("recon-v1-test", "seal rejected")]
    assert invalidated == [True]


def test_failed_completion_refuses_terminal_cas_without_overwrite(monkeypatch) -> None:
    db = _FakeDB(_run())
    terminal_record = SimpleNamespace(status=pipeline.RunStatus.COMPLETED)
    coordinator = SimpleNamespace(
        fail=lambda *_args, **_kwargs: pytest.fail("must not fail completed state"),
        spawn_failed=lambda *_args, **_kwargs: pytest.fail("must not fail completed state"),
    )
    monkeypatch.setattr(
        pipeline.SQLAlchemyRunRepository,
        "get_run",
        lambda _self, _run_id: terminal_record,
    )

    pipeline._mark_pipeline_run_failed(
        db,
        run_id=db.run.run_id,
        coordinator=coordinator,
        worker_lease=None,
        message="seal rejected",
    )

    assert db.rollbacks == 1
    assert db.commits == 0

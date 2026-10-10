"""Regression tests for stale (orphaned) recommendation-run recovery.

These cover the logic exercised by the periodic watchdog
``recover_stale_runs`` Prefect flow (replaces the old in-process
``_periodic_recover_stale_runs``), which calls ``recover_interrupted_runs``
on a timer so that runs whose background process died (e.g. on a backend
restart) are marked ``interrupted`` even when no client hits the runs list
endpoint.
"""
from __future__ import annotations

import uuid
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import RecommendationRun
from app.services.workbench_service import recover_interrupted_runs

pytestmark = pytest.mark.integration


def _utc_naive(offset: timedelta) -> datetime:
    """Heartbeats are persisted as tz-stripped UTC (same convention as the writer)."""
    return (datetime.now(timezone.utc) + offset).replace(tzinfo=None)


def _make_run(run_id: str, *, heartbeat_at) -> RecommendationRun:
    return RecommendationRun(
        run_id=run_id,
        run_name="__test_stale_recovery__",
        trade_date="2026-07-08",
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="running",
        heartbeat_at=heartbeat_at,
        progress_phase="skill_review",
        progress_message="Skill 深审 40/60",
        progress_current=40,
        progress_total=60,
        universe_count=3000,
        recommended_count=0,
        market_scope="all",
        risk_template="balanced",
        score_line=72.0,
        output_count=6,
    )


def test_recover_interrupted_runs_marks_stale_orphan_interrupted():
    run_id_stale = f"__test_stale_{uuid.uuid4().hex[:12]}"
    run_id_fresh = f"__test_fresh_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        # 2h-old heartbeat -> far beyond RUN_HEARTBEAT_STALE_SECONDS (1200)
        db.add(_make_run(run_id_stale, heartbeat_at=_utc_naive(-timedelta(hours=2))))
        # 10s-old heartbeat -> still alive, must be preserved
        db.add(_make_run(run_id_fresh, heartbeat_at=_utc_naive(-timedelta(seconds=10))))
        db.commit()

        recovered = recover_interrupted_runs(db)

        stale = db.get(RecommendationRun, run_id_stale)
        fresh = db.get(RecommendationRun, run_id_fresh)
        assert recovered >= 1
        assert stale is not None and stale.status == "interrupted"
        assert stale.error_message and "中断" in stale.error_message
        assert fresh is not None and fresh.status == "running"
    finally:
        db.rollback()
        for rid in (run_id_stale, run_id_fresh):
            row = db.get(RecommendationRun, rid)
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()


def test_recover_interrupted_runs_noop_when_all_fresh():
    run_id = f"__test_fresh_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        db.add(_make_run(run_id, heartbeat_at=_utc_naive(-timedelta(seconds=5))))
        db.commit()

        recovered = recover_interrupted_runs(db)

        row = db.get(RecommendationRun, run_id)
        assert recovered == 0
        assert row is not None and row.status == "running"
    finally:
        db.rollback()
        row = db.get(RecommendationRun, run_id)
        if row is not None:
            db.delete(row)
        db.commit()
        db.close()


def test_recovery_skips_worker_locked_run_without_waiting_for_lock_timeout():
    """A live worker transaction must not make the watchdog wait 50s and raise 1205."""
    run_id = f"__test_locked_{uuid.uuid4().hex[:12]}"
    worker_db = SessionLocal()
    watchdog_db = SessionLocal()
    try:
        worker_db.add(_make_run(run_id, heartbeat_at=_utc_naive(-timedelta(hours=2))))
        worker_db.commit()

        locked = worker_db.scalar(
            select(RecommendationRun)
            .where(RecommendationRun.run_id == run_id)
            .with_for_update()
        )
        assert locked is not None
        locked.progress_message = "worker external review in progress"
        worker_db.flush()

        started = time.monotonic()
        recovered = recover_interrupted_runs(watchdog_db, run_ids=[run_id])
        elapsed = time.monotonic() - started

        assert recovered == 0
        assert elapsed < 2.0
    finally:
        watchdog_db.rollback()
        watchdog_db.close()
        worker_db.rollback()
        row = worker_db.get(RecommendationRun, run_id)
        if row is not None:
            worker_db.delete(row)
            worker_db.commit()
        worker_db.close()


def test_recover_interrupted_runs_redispatches_stale_queued_not_interrupted():
    """queued run 排队等待时 heartbeat 不更新，超时必须重新派发而非标 interrupted。

    回归场景：曝光补跑 stagger enqueue 多个全市场扫描 run，单 worker 串行 dispatch，
    排队中的 run heartbeat 停在创建时间，20min 后被周期巡检误判为 worker 失联。
    修复后 queued 超时 → redispatch（续命 heartbeat + 重新入队），状态保持 queued。
    """
    import app.services.workbench_service as ws_mod

    enqueued: list[str] = []
    real_enqueue = ws_mod._trigger_recommendation

    def _capture_enqueue(run_id: str) -> None:
        enqueued.append(run_id)

    ws_mod._trigger_recommendation = _capture_enqueue
    run_id_queued = f"__test_queued_{uuid.uuid4().hex[:12]}"
    run_id_running = f"__test_running_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        queued = _make_run(run_id_queued, heartbeat_at=_utc_naive(-timedelta(hours=2)))
        queued.status = "queued"
        queued.progress_message = "等待后台工作线程接管"
        db.add(queued)
        # running 失联仍应走 interrupted
        db.add(_make_run(run_id_running, heartbeat_at=_utc_naive(-timedelta(hours=2))))
        db.commit()

        recovered = recover_interrupted_runs(db)

        queued_row = db.get(RecommendationRun, run_id_queued)
        running_row = db.get(RecommendationRun, run_id_running)
        # queued 未被标 interrupted（未计入 recovered）
        assert queued_row is not None and queued_row.status == "queued"
        assert queued_row.error_message is None
        # running 仍标 interrupted
        assert running_row is not None and running_row.status == "interrupted"
        assert recovered >= 1
        # queued heartbeat 被续命（不再是 2h 前）
        assert queued_row.heartbeat_at is not None
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - queued_row.heartbeat_at).total_seconds()
        assert age < 60  # 续命到当前时间附近
        # queued run 被重新入队派发
        assert run_id_queued in enqueued
        assert run_id_running not in enqueued
    finally:
        ws_mod._trigger_recommendation = real_enqueue
        db.rollback()
        for rid in (run_id_queued, run_id_running):
            row = db.get(RecommendationRun, rid)
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()


def test_stale_run_message_names_phase_and_avoids_unproven_restart_claim():
    """心跳超时只能证明「停止推进」，不能断言原因。

    此前这条消息一律写「服务重启或进程退出」。实测有 18 条 run 停在 prepare 阶段
    被这样标记，而那些时刻并没有发生任何重启——断言单一原因把排查引向了错误方向。
    消息必须给出停留阶段（归因的关键线索），并且不得声称已知原因。
    """
    run_id = f"__test_phase_msg_{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        run = _make_run(run_id, heartbeat_at=_utc_naive(-timedelta(hours=2)))
        run.checkpoint_phase = "prepare"
        run.progress_phase = "prepare"
        run.progress_message = "阶段 prepare 完成"
        run.universe_count = 0
        db.add(run)
        db.commit()

        recover_interrupted_runs(db)

        stalled = db.get(RecommendationRun, run_id)
        assert stalled is not None and stalled.status == "interrupted"
        message = stalled.error_message or ""
        assert "prepare" in message, "必须指出停在哪个阶段，否则无法归因"
        assert "服务重启" not in message
        assert "进程退出" not in message
    finally:
        db.rollback()
        row = db.get(RecommendationRun, run_id)
        if row is not None:
            db.delete(row)
        db.commit()
        db.close()

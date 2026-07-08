"""Regression tests for stale (orphaned) recommendation-run recovery.

These cover the logic exercised by the periodic watchdog
``_periodic_recover_stale_runs`` registered in ``scheduler_service.start_scheduler``,
which calls ``recover_interrupted_runs`` on a timer so that runs whose background
subprocess died (e.g. on a backend restart) are marked ``interrupted`` even when no
client hits the runs list endpoint.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import RecommendationRun
from app.services.workbench_service import recover_interrupted_runs


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

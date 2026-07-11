"""曝光补跑「跳过在途任务」去重逻辑的单测。

避免同一策略在近窗口内已有 queued/running experiment run 时重复 enqueue，
防止多个全市场扫描子进程并发造成 CPU 争用与心跳超时中断（E 项样本饥饿根因之一）。
"""
import json
from datetime import datetime, timedelta, timezone

from app.models.tables import RecommendationRun
from app.services import scheduler_service


def _mk_run(db, run_id, skill_id, status, started_at):
    run = RecommendationRun(
        run_id=run_id,
        run_name="t",
        run_mode="experiment",
        trade_date=datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d"),
        status=status,
        selected_skills_json=json.dumps([{"skill_id": skill_id}]),
        started_at=started_at,
        heartbeat_at=started_at,
    )
    db.add(run)
    db.flush()


def test_inflight_true_when_running_recent(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _mk_run(db, "exp_inflight_1", "chip-single-peak-breakout", "running", now)
    assert scheduler_service._has_inflight_experiment_run(db, "chip-single-peak-breakout") is True


def test_inflight_false_when_completed(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _mk_run(db, "exp_inflight_2", "chip-single-peak-breakout", "completed", now)
    assert scheduler_service._has_inflight_experiment_run(db, "chip-single-peak-breakout") is False


def test_inflight_false_when_outside_window(db):
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=90)
    _mk_run(db, "exp_inflight_3", "chip-single-peak-breakout", "running", old)
    assert scheduler_service._has_inflight_experiment_run(db, "chip-single-peak-breakout") is False


def test_inflight_false_when_other_skill(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _mk_run(db, "exp_inflight_4", "other-skill", "running", now)
    assert scheduler_service._has_inflight_experiment_run(db, "chip-single-peak-breakout") is False

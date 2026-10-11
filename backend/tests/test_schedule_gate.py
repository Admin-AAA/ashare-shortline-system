"""定时作业放行判定回归测试：休市日跳过、收盘前作业迟到跳过、盘后作业照常补跑。

10-01~10-07 国庆休市时 cron mon-fri 照常触发，盘后推荐每天报「日K线未覆盖」failed；
09-30 机器重启后 08:03/08:30/09:20 的盘前作业 17:34 才被补跑，与盘后作业抢资源。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.tables import Base, ScheduledTask, ScheduledTaskRun
from app.services import scheduler_service as sched
from app.services.schedule_gate import CST, schedule_skip_reason

HOLIDAYS = {date(2026, 10, 1), date(2026, 10, 7)}


def _open(day: date) -> bool:
    return day not in HOLIDAYS and day.weekday() < 5


def _cst(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=CST)


def test_holiday_is_skipped():
    reason = schedule_skip_reason(_cst(2026, 10, 1, 17, 35), _cst(2026, 10, 1, 17, 35), _open)
    assert reason is not None and reason.startswith("非交易日跳过") and "2026-10-01" in reason


def test_trading_day_on_time_runs():
    assert schedule_skip_reason(_cst(2026, 10, 9, 8, 3), _cst(2026, 10, 9, 8, 3), _open) is None


def test_preclose_task_late_after_close_is_skipped():
    reason = schedule_skip_reason(_cst(2026, 9, 30, 9, 20), _cst(2026, 9, 30, 17, 34), _open)
    assert reason is not None and reason.startswith("迟到超时跳过")


def test_preclose_task_late_but_before_close_still_runs():
    assert schedule_skip_reason(_cst(2026, 9, 30, 8, 30), _cst(2026, 9, 30, 10, 0), _open) is None


def test_postclose_task_late_still_runs():
    assert schedule_skip_reason(_cst(2026, 10, 9, 17, 35), _cst(2026, 10, 9, 21, 30), _open) is None


def test_calendar_uses_planned_day_not_execution_day():
    # 10-09（交易日）21:00 的补数作业拖到次日凌晨才执行，仍按 10-09 判定放行
    seen: list[date] = []

    def recording(day: date) -> bool:
        seen.append(day)
        return _open(day)

    planned_utc = _cst(2026, 10, 9, 21, 0).astimezone(timezone.utc)
    now_utc = _cst(2026, 10, 10, 1, 0).astimezone(timezone.utc)
    assert schedule_skip_reason(planned_utc, now_utc, recording) is None
    assert seen == [date(2026, 10, 9)]


# ── _execute_task 接线 ─────────────────────────────────────────────────────────


@pytest.fixture()
def sqlite_scheduler(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[ScheduledTask.__table__, ScheduledTaskRun.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(sched, "SessionLocal", factory)
    with factory() as db:
        db.add(
            ScheduledTask(
                task_id="intraday_lock_create_monitors",
                name="盘前建监控",
                task_type=sched.TASK_TYPE_INTRADAY_LOCK_SHADOW,
                cron_expression="20 9 * * mon-fri",
                payload_json=json.dumps({"action": "create"}),
                enabled=True,
            )
        )
        db.commit()
    calls: list[dict] = []
    monkeypatch.setattr(
        sched, "_run_intraday_lock_shadow", lambda db, payload: calls.append(payload) or {"created": 0}
    )
    return factory, calls


def _runs(factory) -> list[ScheduledTaskRun]:
    with factory() as db:
        return list(db.scalars(select(ScheduledTaskRun)))


def test_execute_task_records_skip_on_holiday(sqlite_scheduler, monkeypatch):
    factory, calls = sqlite_scheduler
    monkeypatch.setattr("app.services.tushare_client.is_open_trade_date", lambda day: False)
    sched._execute_task(
        "intraday_lock_create_monitors",
        trigger_source="schedule",
        scheduled_at=datetime.now(timezone.utc),
    )
    assert calls == []
    [run] = _runs(factory)
    assert run.status == "skipped"
    assert run.summary.startswith("非交易日跳过")
    with factory() as db:
        assert db.get(ScheduledTask, "intraday_lock_create_monitors").last_status == "skipped"


def test_execute_task_runs_on_trading_day(sqlite_scheduler, monkeypatch):
    factory, calls = sqlite_scheduler
    monkeypatch.setattr("app.services.tushare_client.is_open_trade_date", lambda day: True)
    # 计划时刻取「现在」：收盘前是准点的盘前作业，收盘后是准点的盘后作业，都应放行
    sched._execute_task(
        "intraday_lock_create_monitors",
        trigger_source="schedule",
        scheduled_at=datetime.now(timezone.utc),
    )
    assert calls == [{"action": "create"}]
    [run] = _runs(factory)
    assert run.status == "completed"


def test_manual_trigger_bypasses_gate(sqlite_scheduler, monkeypatch):
    factory, calls = sqlite_scheduler
    monkeypatch.setattr(
        "app.services.tushare_client.is_open_trade_date",
        lambda day: (_ for _ in ()).throw(AssertionError("手动触发不应查交易日历")),
    )
    sched._execute_task("intraday_lock_create_monitors", force=True, trigger_source="manual")
    assert calls == [{"action": "create"}]


def test_schedule_trigger_requires_scheduled_at():
    with pytest.raises(ValueError, match="scheduled_at"):
        sched._execute_task("intraday_lock_create_monitors", trigger_source="schedule")


def test_late_preclose_schedule_trigger_records_skip(sqlite_scheduler, monkeypatch):
    factory, calls = sqlite_scheduler
    monkeypatch.setattr("app.services.tushare_client.is_open_trade_date", lambda day: True)
    sched._execute_task(
        "intraday_lock_create_monitors",
        trigger_source="schedule",
        scheduled_at=(datetime.now(CST) - timedelta(days=1)).replace(hour=9, minute=20),
    )
    assert calls == []
    [run] = _runs(factory)
    assert run.status == "skipped" and run.summary.startswith("迟到超时跳过")

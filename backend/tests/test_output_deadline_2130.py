"""出票截止跟着 21:30 盘后出票走：截止之前不把当天判成失败，截止之后真缺票照样报红。

背景（2026-11-27）：盘后出票 11-24 起从 17:35 挪到 21:30，但 OUTPUT_DEADLINE 还是 18:35（17:35 + 1 小时），
18:35–21:30 之间巡检 / 健康快照 / 自愈都会把还没开跑的当天判成「出票失败」。11-26 自愈 18:36、20:42
两轮把当天归档成漏票；11-25 20:52 自愈守护把当天判成漏票，抢在 21:00 补数和 21:30 出票之前自己补跑出票。
健康快照的原始记录窗口也还停在 18:40–19:40，22:55 的定时取样全部落进复核，原始记录一条没写。
"""
from __future__ import annotations

import datetime as dt
import plistlib
from pathlib import Path

import pytest

from app.services import daily_output_check as doc
from app.services import data_health_snapshot as hs
from app.services import data_quality_monitor as dqm
from app.services import scheduler_pure as sp
from app.services import startup_self_heal as sh

CN = dt.timezone(dt.timedelta(hours=8))
OPEN_DAYS = ["2026-11-25", "2026-11-26", "2026-11-27", "2026-11-30"]
TODAY, PREV = "2026-11-27", "2026-11-26"
REPO = Path(__file__).resolve().parents[2]


def _at(hour: int, minute: int, day: int = 27, month: int = 11) -> dt.datetime:
    return dt.datetime(2026, month, day, hour, minute)


def _cron_time(cron: str) -> dt.time:
    minute, hour = cron.split()[:2]
    return dt.time(int(hour), int(minute))


@pytest.mark.parametrize("hm", [(18, 35), (18, 40), (19, 0), (21, 0), (21, 29), (21, 30), (21, 50), (22, 15), (22, 49)])
def test_before_deadline_today_is_not_judged(hm):
    now = _at(*hm)
    assert not doc.output_due(TODAY, now)
    assert doc.output_due(PREV, now)
    assert doc.output_target_date(OPEN_DAYS, now) == PREV


@pytest.mark.parametrize("hm", [(22, 50), (22, 55), (23, 30)])
def test_after_deadline_today_is_judged(hm):
    now = _at(*hm)
    assert doc.output_due(TODAY, now)
    assert doc.output_target_date(OPEN_DAYS, now) == TODAY


def test_next_morning_still_judges_previous_trade_day():
    # 周一 08:50 盘前巡检：上周五（11-27）仍是判定对象，周一当天还没到截止
    assert doc.output_target_date(OPEN_DAYS, _at(8, 50, day=30)) == TODAY
    assert not doc.output_due("2026-11-30", _at(8, 50, day=30))


def test_output_due_converts_aware_time_to_beijing():
    utc = dt.timezone.utc
    assert not doc.output_due(TODAY, dt.datetime(2026, 11, 27, 14, 49, tzinfo=utc))  # 北京 22:49
    assert doc.output_due(TODAY, dt.datetime(2026, 11, 27, 14, 50, tzinfo=utc))  # 北京 22:50
    assert doc.output_target_date(OPEN_DAYS, dt.datetime(2026, 11, 27, 22, 49, tzinfo=CN)) == PREV


@pytest.fixture
def only_previous_day_output(monkeypatch):
    """上一交易日 a/b 两个短线策略都出了票、中长线全部正常；当天（11-27）什么都还没有。"""
    from app.services import midterm_service as ms

    runs = {PREV: {"a": {"run_id": "r1", "recommended_count": 2}, "b": {"run_id": "r2", "recommended_count": 0}}}
    status = {PREV: {sid: {"status": "no_signal"} for sid in ms.STRATEGIES}}
    monkeypatch.setattr(doc, "expected_short_strategies", lambda db: ["a", "b"])
    monkeypatch.setattr(doc, "_live_short_runs", lambda db, d: runs.get(d, {}))
    monkeypatch.setattr(doc, "_midlong_status", lambda d: {"status_file": d in status, "stored": status.get(d, {})})
    monkeypatch.setattr("app.services.strategy_data_readiness.blocked_on", lambda d: {})
    monkeypatch.setattr("app.services.startup_self_heal.archived_output_miss", lambda d: None)
    return runs, status


@pytest.mark.parametrize("hm", [(18, 36), (18, 40), (19, 0), (20, 42), (21, 0), (21, 29), (21, 50), (22, 49)])
def test_daily_output_not_red_while_2130_pipeline_pending(only_previous_day_output, hm):
    now = dt.datetime(2026, 11, 27, *hm, tzinfo=dqm._CST)
    item = dqm.check_daily_output(None, OPEN_DAYS, now)
    assert item["severity"] == dqm.GREEN
    assert item["latest"] == PREV


def test_daily_output_red_after_deadline_when_today_missing(only_previous_day_output):
    for hm in ((22, 50), (22, 55)):
        item = dqm.check_daily_output(None, OPEN_DAYS, dt.datetime(2026, 11, 27, *hm, tzinfo=dqm._CST))
        assert item["severity"] == dqm.RED and item["latest"] == TODAY
        assert "a" in item["detail"] and "b" in item["detail"]
    # 次一交易日 08:50 盘前巡检仍盯着 11-27，没补上就继续红
    item = dqm.check_daily_output(None, OPEN_DAYS, dt.datetime(2026, 11, 30, 8, 50, tzinfo=dqm._CST))
    assert item["severity"] == dqm.RED and item["latest"] == TODAY


def test_daily_output_green_after_deadline_once_today_output_lands(only_previous_day_output):
    runs, status = only_previous_day_output
    runs[TODAY] = dict(runs[PREV])
    status[TODAY] = dict(status[PREV])
    item = dqm.check_daily_output(None, OPEN_DAYS, dt.datetime(2026, 11, 27, 22, 50, tzinfo=dqm._CST))
    assert item["severity"] == dqm.GREEN and item["latest"] == TODAY


def test_self_heal_does_not_archive_today_before_deadline(monkeypatch):
    monkeypatch.setattr(doc, "daily_output_status",
                        lambda db, day, now=None: {"ok": False, "status": "failed", "reason": "短线 0/4",
                                                   "short_done": 0, "short_expected": 4})
    days = [PREV, TODAY]
    assert set(sh._archive_output_gaps(object(), days, _at(18, 36))) == {PREV}
    assert set(sh._archive_output_gaps(object(), days, _at(20, 42))) == {PREV}
    assert set(sh._archive_output_gaps(object(), days, _at(22, 50))) == {PREV, TODAY}


@pytest.fixture
def repick_env(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(sh, "_under_pytest", lambda: False)
    monkeypatch.setenv("ASHARE_SELF_HEAL_REPICK_ENABLED", "1")
    monkeypatch.setattr("app.services.midterm_live_seal.generated_before_entry_open", lambda d, instant: True)
    monkeypatch.setattr(doc, "daily_output_status", lambda db, d, now=None: {"ok": False})

    async def fake_repick(db, task_id):
        calls.append(task_id)
        return {"status": "completed", "trade_date": TODAY, "reason": None}

    monkeypatch.setattr("app.services.scheduler_service._late_repair_repick", fake_repick)
    return calls


def test_self_heal_does_not_repick_today_before_deadline(repick_env):
    # 11-25 20:52 的情形：当天还没到 21:00 补数 / 21:30 出票，自愈不能抢先出票
    out = sh._try_repick_missed_output(object(), [PREV, TODAY], _at(20, 52), {})
    assert out == {"status": "skipped", "reason": "before_output_deadline", "trade_date": TODAY}
    assert repick_env == []


def test_self_heal_repicks_after_deadline(repick_env):
    out = sh._try_repick_missed_output(object(), [PREV, TODAY], _at(23, 47), {})
    assert out["status"] == "completed" and repick_env == ["startup_self_heal_repick"]


def test_output_deadline_follows_schedule():
    """截止必须在全部自动出票路径之后、在要判当天的验收与快照之前；改了定时就得一起改截止。"""
    pipeline = _cron_time(sp.POSTCLOSE_PIPELINE_CRON)
    evening = dict((task_id, cron) for task_id, _name, cron in sp.DATA_QUALITY_CHECKS)["data_quality_check_evening"]
    plist = plistlib.loads((REPO / "deploy" / "launchd" / "com.wangao.ashare-daily-run-watchdog.plist").read_bytes())
    watchdog = {dt.time(i["Hour"], i["Minute"]) for i in plist["StartCalendarInterval"]}
    assert len(watchdog) == 1
    assert _cron_time(sp.POSTCLOSE_LATE_DATA_REPAIR_CRON) < pipeline < watchdog.pop() < doc.OUTPUT_DEADLINE
    assert doc.OUTPUT_DEADLINE <= _cron_time(evening) <= _cron_time(sp.DATA_HEALTH_SNAPSHOT_CRON)


def test_original_slot_follows_cron():
    assert hs.ORIGINAL_TIME == _cron_time(sp.DATA_HEALTH_SNAPSHOT_CRON)
    assert hs.ORIGINAL_TIME >= doc.OUTPUT_DEADLINE  # 原始记录判的是当天，不能早于截止
    assert hs.is_original_slot(TODAY, dt.datetime(2026, 11, 27, 22, 55, tzinfo=CN))


def test_scheduled_2255_snapshot_writes_original_for_today(monkeypatch, tmp_path):
    p = tmp_path / "snap.jsonl"
    monkeypatch.setattr(dqm, "load_open_days", lambda db: OPEN_DAYS)
    monkeypatch.setattr(hs, "measure", lambda db, now, trade_date: {
        "taken_at": now.isoformat(), "quality_ok": True, "score": 88, "red": [], "yellow": [],
        "output_status": "ok", "output_ok": True, "output": {"reason": ""}})
    out = hs.take_snapshot(None, mode="original", now=dt.datetime(2026, 11, 27, 22, 55, 3, tzinfo=CN), path=p)
    assert out == {"trade_date": TODAY, "written": "original", "sample": out["sample"]}
    assert hs.summarize(20, p)["days_with_original"] == 1

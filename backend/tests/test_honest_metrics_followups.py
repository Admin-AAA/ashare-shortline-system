"""PR #10 审查小建议：复核过 09:15 跳过、原始记录只在 18:40–19:40 写、新告警自动解除、快照文件锁、短线回放终点从数据读。"""
from __future__ import annotations

import datetime as dt
import threading

import pytest

from app.services import data_health_snapshot as hs

CN = dt.timezone(dt.timedelta(hours=8))
OPEN_DAYS = ["2026-10-23", "2026-10-26", "2026-10-27"]


@pytest.fixture
def fake_measure(monkeypatch):
    from app.services import data_quality_monitor as dqm

    monkeypatch.setattr(dqm, "load_open_days", lambda db: OPEN_DAYS)
    monkeypatch.setattr(hs, "measure", lambda db, now, trade_date: {
        "taken_at": now.isoformat(), "quality_ok": True, "score": 88, "red": [], "yellow": [],
        "output_status": "ok", "output_ok": True, "output": {"reason": ""}})


def test_original_only_between_1840_and_1940(fake_measure, tmp_path):
    p = tmp_path / "s.jsonl"
    late = hs.take_snapshot(None, mode="original", now=dt.datetime(2026, 10, 26, 19, 41, tzinfo=CN), path=p)
    assert late["written"] == "recheck"  # 超过 19:40 只记复核
    assert hs.load_snapshots(p)[0]["original"] is None
    ok = hs.take_snapshot(None, mode="original", now=dt.datetime(2026, 10, 27, 19, 39, tzinfo=CN), path=p)
    assert ok["written"] == "original"
    assert hs.is_original_slot("2026-10-27", dt.datetime(2026, 10, 27, 18, 40, tzinfo=CN))
    assert not hs.is_original_slot("2026-10-27", dt.datetime(2026, 10, 27, 19, 40, tzinfo=CN))


def test_scheduled_recheck_skipped_after_0915(monkeypatch):
    from app.services import data_quality_monitor as dqm
    from app.services import scheduler_service as ss

    monkeypatch.setattr(dqm, "_now", lambda: dt.datetime(2026, 10, 27, 9, 16, tzinfo=CN))
    monkeypatch.setattr(hs, "take_snapshot", lambda *a, **k: pytest.fail("09:15 后不应再复核"))
    out = ss._run_data_health_snapshot(None, {"mode": "recheck"})
    assert out["status"] == "skipped" and "09:15" in out["summary_text"]
    assert hs.recheck_too_late(dt.datetime(2026, 10, 27, 9, 15, tzinfo=CN))
    assert not hs.recheck_too_late(dt.datetime(2026, 10, 27, 8, 55, tzinfo=CN))


def test_snapshot_writes_are_file_locked(fake_measure, tmp_path, monkeypatch):
    """take_snapshot 的读改写在文件锁里：锁被别的进程占着时要等，不能并发写。"""
    from app.governance.locking import file_lock

    p = tmp_path / "s.jsonl"
    done = threading.Event()
    with file_lock(hs._lock_path(p)):
        # 进程内 _LOCK 不挡另一线程拿文件锁之前的步骤；用独立线程模拟另一个写入者
        t = threading.Thread(target=lambda: (hs.take_snapshot(
            None, mode="recheck", now=dt.datetime(2026, 10, 27, 8, 55, tzinfo=CN), path=p), done.set()))
        t.start()
        assert not done.wait(0.5)  # 锁没放开之前写不进去
        assert not p.exists()
    t.join(5)
    assert done.is_set() and len(hs.load_snapshots(p)[0]["rechecks"]) == 1


def test_managed_alerts_expire_after_24h_and_others_untouched():
    from app.core import alerts
    from app.services import honest_alerts as ha

    alerts.reset_alerts()
    alerts.emit_alert("daily_output_failed", "2026-10-26", "x")
    alerts.emit_alert("trade_calendar", "seal:2026-12-31", "x")
    alerts.emit_alert("trade_calendar", "weekday:20261231", "x")  # 非受管
    alerts.emit_alert("data_freshness", "t", "x")  # 非受管
    first = {r["kind"] + "|" + r["key"]: r["last_seen"] for r in alerts.active_alert_records()}
    seen = dt.datetime.strptime(first["daily_output_failed|2026-10-26"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN)
    assert ha.expire_stale(seen + dt.timedelta(hours=23)) == []
    gone = ha.expire_stale(seen + dt.timedelta(hours=24, minutes=1))
    assert sorted(gone) == ["daily_output_failed|2026-10-26", "trade_calendar|seal:2026-12-31"]
    left = {r["kind"] + "|" + r["key"] for r in alerts.active_alert_records()}
    assert left == {"trade_calendar|weekday:20261231", "data_freshness|t"}
    alerts.reset_alerts()


def test_recheck_recovery_resolves_output_alert(monkeypatch):
    from app.core import alerts
    from app.services import data_quality_monitor as dqm
    from app.services import scheduler_service as ss

    alerts.reset_alerts()
    monkeypatch.setattr(dqm, "_now", lambda: dt.datetime(2026, 10, 27, 8, 55, tzinfo=CN))
    monkeypatch.setattr(hs, "summarize", lambda days: {"days_with_original": 1, "avg_score": 80, "output_success_rate": 0.0,
                                                       "red_days": 0, "quality_failed_days": 0})

    def snap(status):
        return lambda db, mode: {"trade_date": "2026-10-26", "written": "recheck" if mode == "recheck" else "original",
                                 "sample": {"quality_ok": True, "score": 80, "red": [], "yellow": [], "score_db": 85,
                                            "penalty": 5, "output_status": status, "output_ok": status != "failed",
                                            "output": {"reason": "缺大宗"}}}

    monkeypatch.setattr(hs, "take_snapshot", snap("failed"))
    ss._run_data_health_snapshot(None, {"mode": "original"})
    assert [r["key"] for r in alerts.active_alert_records("daily_output_failed")] == ["2026-10-26"]
    monkeypatch.setattr(hs, "take_snapshot", snap("ok"))
    ss._run_data_health_snapshot(None, {"mode": "recheck"})
    assert alerts.active_alert_records("daily_output_failed") == []  # 复核恢复即解除
    alerts.reset_alerts()


def test_short_replay_end_read_from_data_with_constant_fallback():
    from app.services import rolling_r12 as rr

    class _DB:
        def __init__(self, value=None, boom=False):
            self.value, self.boom = value, boom

        def execute(self, *a, **k):
            if self.boom:
                raise RuntimeError("db down")
            v = self.value

            class R:
                def scalar(self_inner):
                    return v
            return R()

    assert rr.short_replay_coverage_end(_DB("2026-11-20")) == ("2026-11-20", "data")
    assert rr.short_replay_coverage_end(_DB(dt.date(2026, 11, 20))) == ("2026-11-20", "data")
    assert rr.short_replay_coverage_end(_DB(None)) == (rr.SHORT_REPLAY_COVERAGE_END, "constant")
    assert rr.short_replay_coverage_end(_DB(boom=True)) == (rr.SHORT_REPLAY_COVERAGE_END, "constant")

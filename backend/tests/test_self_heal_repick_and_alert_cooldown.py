"""回归：巡检告警不再自激刷 ERROR、自愈在数据补齐后补跑漏掉的正式出票。

背景（2026-11-15）：
- 巡检默认 10 分钟告警冷却对「每 2 小时一轮」完全无效，同一批红黄项反复写
  ``[ALERT:data_quality]`` ERROR，反过来把 ``log:error_volume`` 顶成黄项，黄项又续命告警。
- 2026-11-13 机器离线使 17:35 主流水线未启动，19:30 watchdog 只告警，21:00 / 次日 08:30
  补跑被健康闸门拦下；自愈把库表补齐后没有任何环节重试，漏票一直挂到下一交易日。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.services import data_quality_monitor as dq
from app.services import startup_self_heal as sh

_NOW = datetime(2026, 11, 15, 0, 30)


def _report(sev: str) -> dict:
    return {"items": [{"key": "k", "label": "L", "severity": sev, "detail": "d",
                       "impact": "x", "category": "c"}]}


@pytest.fixture(autouse=True)
def _clean_alerts():
    from app.core import alerts

    alerts.reset_alerts()
    yield
    alerts.reset_alerts()


def test_steady_quality_alert_is_cooldowned_but_escalation_is_immediate(monkeypatch):
    from app.core import alerts

    original = alerts.emit_alert
    cooldowns: list[float] = []

    def spy(kind, key, message, *, cooldown=600.0):
        cooldowns.append(cooldown)
        return original(kind, key, message, cooldown=cooldown)

    monkeypatch.setattr(alerts, "emit_alert", spy)
    dq.sync_alerts(_report(dq.YELLOW))   # 首次出现
    dq.sync_alerts(_report(dq.YELLOW))   # 稳态重复
    dq.sync_alerts(_report(dq.RED))      # 升级为红
    dq.sync_alerts(_report(dq.RED))      # 稳态重复
    assert cooldowns == [0.0, dq.QUALITY_ALERT_COOLDOWN_SECONDS,
                         0.0, dq.QUALITY_ALERT_COOLDOWN_SECONDS]


@pytest.fixture
def repick_env(monkeypatch):
    """让 _try_repick_missed_output 走真实分支，但把上游/生产入口换成桩。"""
    monkeypatch.setattr(sh, "_under_pytest", lambda: False)
    monkeypatch.setenv("ASHARE_SELF_HEAL_REPICK_ENABLED", "1")
    monkeypatch.setattr("app.services.midterm_live_seal.generated_before_entry_open",
                        lambda d, instant: True)
    monkeypatch.setattr("app.services.daily_output_check.daily_output_status",
                        lambda db, d, now=None: {"ok": False})

    async def fake_repick(db, task_id):
        return {"status": "completed", "trade_date": "2026-11-13", "reason": None}

    monkeypatch.setattr("app.services.scheduler_service._late_repair_repick", fake_repick)


def test_repick_runs_after_gate_recovers(repick_env):
    out = sh._try_repick_missed_output(object(), ["2026-11-12", "2026-11-13"], _NOW, {})
    assert out["status"] == "completed"
    assert out["trade_date"] == "2026-11-13"


def test_repick_skipped_when_data_gaps_remain(repick_env):
    out = sh._try_repick_missed_output(object(), ["2026-11-13"], _NOW,
                                       {"2026-11-13": ["index_daily"]})
    assert out["status"] == "skipped" and out["reason"] == "data_gaps_remain"
    assert out["gaps"] == ["2026-11-13"]


def test_repick_skipped_past_entry_open(repick_env, monkeypatch):
    monkeypatch.setattr("app.services.midterm_live_seal.generated_before_entry_open",
                        lambda d, instant: False)
    out = sh._try_repick_missed_output(object(), ["2026-11-13"], _NOW, {})
    assert out["status"] == "skipped" and out["reason"] == "past_entry_open"


def test_repick_skipped_when_output_already_complete(repick_env, monkeypatch):
    monkeypatch.setattr("app.services.daily_output_check.daily_output_status",
                        lambda db, d, now=None: {"ok": True})
    out = sh._try_repick_missed_output(object(), ["2026-11-13"], _NOW, {})
    assert out["status"] == "skipped" and out["reason"] == "already_completed"


def test_repick_respects_kill_switch(repick_env, monkeypatch):
    monkeypatch.setenv("ASHARE_SELF_HEAL_REPICK_ENABLED", "0")
    out = sh._try_repick_missed_output(object(), ["2026-11-13"], _NOW, {})
    assert out["status"] == "skipped" and out["reason"] == "disabled"


def test_run_self_heal_records_repick_result(monkeypatch):
    """自愈主流程必须把补跑结果写进状态，不能只归档漏票就结束。"""
    monkeypatch.setattr(sh, "_ACTIONS", (("a", lambda db, days, gaps: {"status": "completed"}),))
    monkeypatch.setattr(sh, "_archive_output_gaps", lambda db, days, now: {})
    monkeypatch.setattr(sh, "scan_gaps", lambda db, days: {})
    monkeypatch.setattr(sh, "_recent_closed_trade_days",
                        lambda db, now, lookback: ["2026-11-13"])
    monkeypatch.setattr(sh, "_save_state", lambda path, run: None)
    monkeypatch.setattr("app.services.data_health_service.clear_data_health_cache", lambda: None)
    monkeypatch.setattr(sh, "_try_repick_missed_output",
                        lambda db, days, now, gaps: {"status": "skipped", "reason": "pytest"})
    out = sh.run_self_heal(object(), trigger="manual", force=True, now=_NOW)
    assert out["output_repick"] == {"status": "skipped", "reason": "pytest"}
    assert out["status"] == "completed"


def test_result_derived_partial_failure_is_not_double_counted():
    assert dq.is_result_derived_partial_failure(
        "data_quality_check_evening", "partial_failed", "数据质量巡检 红 2 / 黄 14 / 绿 56")
    assert dq.is_result_derived_partial_failure(
        "data_freshness_check_daily", "partial_failed", "数据新鲜度正常；数据质量巡检 红 2 / 黄 34 / 绿 36")
    # 非巡检作业 / 真失败 / 非结果型摘要都照旧计黄，不能被放掉
    assert not dq.is_result_derived_partial_failure(
        "postclose_late_data_repair", "partial_failed", "数据质量巡检 红 2 / 黄 14 / 绿 56")
    assert not dq.is_result_derived_partial_failure(
        "data_quality_check_evening", "failed", "数据质量巡检 红 2 / 黄 14 / 绿 56")
    assert not dq.is_result_derived_partial_failure(
        "data_quality_check_evening", "partial_failed", "巡检整体崩溃：NameError")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, summary):
        self.summary = summary

    def execute(self, sql, params=None):
        if "FROM scheduled_tasks" in str(sql):
            return _Rows([("data_quality_check_evening", "晚间巡检", "40 21 * * mon-fri", None)])
        return _Rows([(datetime(2026, 11, 14, 13, 40), "partial_failed", self.summary)])


def test_scheduled_task_result_only_partial_failure_stays_green():
    item = dq.check_scheduled_tasks(_FakeDB("数据质量巡检 红 2 / 黄 14 / 绿 56"), [], _NOW)[0]
    assert item["severity"] == dq.GREEN and "不重复计黄" in item["detail"]


def test_scheduled_task_real_partial_failure_stays_yellow():
    item = dq.check_scheduled_tasks(_FakeDB("巡检整体崩溃：NameError"), [], _NOW)[0]
    assert item["severity"] == dq.YELLOW and "不重复计黄" not in item["detail"]


class _DB:
    def __init__(self):
        self.committed = False
        self.rolled = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled = True


def test_orphaned_queued_run_is_failed_not_left_inflight(monkeypatch):
    """Prefect 派发失败留下的 queued run 必须收尾，否则补跑永久被 inflight 挡住。"""
    from app.services import workbench_service as ws

    calls = []

    class _Coord:
        def __init__(self, repo):
            pass

        def spawn_failed(self, run_id, *, now, message):
            calls.append((run_id, message))
            return True

    monkeypatch.setattr(ws, "RunCoordinator", _Coord)
    db = _DB()
    assert ws.fail_orphaned_queued_run(db, object(), "run_x", "trigger boom") is True
    assert db.committed and not db.rolled and calls == [("run_x", "trigger boom")]

    class _RefusingCoord:
        def __init__(self, repo):
            pass

        def spawn_failed(self, run_id, *, now, message):
            return False  # 已被 worker 接管，状态机拒绝改

    monkeypatch.setattr(ws, "RunCoordinator", _RefusingCoord)
    db2 = _DB()
    assert ws.fail_orphaned_queued_run(db2, object(), "run_y", "x") is False
    assert db2.rolled and not db2.committed

    monkeypatch.setattr(ws, "RunCoordinator", lambda repo: (_ for _ in ()).throw(RuntimeError("down")))
    db3 = _DB()
    assert ws.fail_orphaned_queued_run(db3, object(), "run_z", "x") is False
    assert db3.rolled


def test_margin_backoff_only_when_upstream_publishing_nothing(tmp_path, monkeypatch):
    """上游连续 4 次给同一批半量数据才退避；行数一变、时间一过就恢复重试。"""
    from app.services import margin_detail_fill as mf

    monkeypatch.setenv("ASHARE_MARGIN_FILL_RUNTIME_DIR", str(tmp_path))
    path = tmp_path / "margin_fill_provenance.jsonl"
    now = datetime(2026, 11, 15, 1, 0)

    def rec(ts, rows, status="incomplete"):
        return {"ts": ts, "trade_date": "2026-11-13", "status": status, "rows_after": rows}

    def write(rows):
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    write([rec("2026-11-14T19:00:00", 2019)] * 3)
    assert not mf._backoff_active("2026-11-13", now)          # 次数不够
    write([rec("2026-11-14T19:00:00", 2019)] * 3 + [rec("2026-11-15T00:01:07", 2019)])
    assert mf._backoff_active("2026-11-13", now)              # 连续同值 → 退避
    write([rec("2026-11-14T19:00:00", 2019)] * 3 + [rec("2026-11-15T00:01:07", 2100)])
    assert not mf._backoff_active("2026-11-13", now)          # 行数变化 → 立即恢复
    write([rec("2026-11-14T19:00:00", 2019)] * 3 + [rec("2026-11-14T10:00:00", 2019)])
    assert not mf._backoff_active("2026-11-13", now)          # 超过 12h → 再试一次

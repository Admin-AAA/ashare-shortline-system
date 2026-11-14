"""开机自愈：交易日回退、缺口扫描、动作隔离、防抖与「出票只归档不伪造」契约。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app.services import startup_self_heal as sh

_ORIG_ARCHIVE = sh._archive_output_gaps


@pytest.fixture
def state_file(tmp_path: Path, monkeypatch):
    p = tmp_path / "runtime" / "startup_self_heal.json"
    monkeypatch.setattr(sh, "_state_path", lambda: p)
    return p


def _no_actions(monkeypatch):
    """把重动作全部替换成轻量桩，让 run_self_heal 只跑编排逻辑。"""
    calls: list[str] = []

    def ok(name):
        def _fn(db, days, gaps):
            calls.append(name)
            return {"status": "completed"}
        return _fn

    monkeypatch.setattr(sh, "_ACTIONS", tuple((n, ok(n)) for n in ("a", "b", "c")))
    monkeypatch.setattr(sh, "_archive_output_gaps", lambda db, days, now: {})
    monkeypatch.setattr(sh, "scan_gaps", lambda db, days: {})
    monkeypatch.setattr(sh, "_recent_closed_trade_days",
                        lambda db, now, lookback: ["2026-11-12", "2026-11-13"])
    monkeypatch.setattr("app.services.data_health_service.clear_data_health_cache", lambda: None)
    return calls


def test_recent_closed_trade_days_excludes_weekend(monkeypatch):
    monkeypatch.setattr("app.services.tushare_client.fetch_recent_trade_dates",
                        lambda: ["20261109", "20261110", "20261111", "20261112", "20261113", "20261114", "20261116"])
    db = object()
    monkeypatch.setattr(sh, "_norm_date", sh._norm_date)
    days = sh._recent_closed_trade_days(db, datetime(2026, 11, 14, 18, 45), 5)
    assert days == ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]


def test_scan_gaps_only_reports_tables_below_floor(monkeypatch):
    def rows(db, table, day):
        if day == "2026-11-13" and table in ("daily_basic", "index_daily"):
            return 0
        return 99999

    monkeypatch.setattr(sh, "_day_rows", rows)
    gaps = sh.scan_gaps(object(), ["2026-11-12", "2026-11-13"])
    assert set(gaps) == {"2026-11-13"}
    assert set(gaps["2026-11-13"]) == {"index_daily", "daily_basic"}


def test_run_self_heal_isolates_action_failure(state_file, monkeypatch):
    calls = _no_actions(monkeypatch)

    def boom(db, days, gaps):
        calls.append("boom")
        raise RuntimeError("upstream down")

    monkeypatch.setattr(sh, "_ACTIONS", (
        ("a", lambda db, days, gaps: calls.append("a") or {"status": "completed"}),
        ("boom", boom),
        ("c", lambda db, days, gaps: calls.append("c") or {"status": "completed"}),
    ))
    out = sh.run_self_heal(object(), now=datetime(2026, 11, 14, 18, 45), trigger="startup")
    assert calls == ["a", "boom", "c"]
    assert out["actions"]["boom"]["status"] == "failed"
    assert "upstream down" in out["actions"]["boom"]["error"]
    assert out["status"] == "completed"
    assert out["reference_trade_date"] == "2026-11-13"
    assert json.loads(state_file.read_text(encoding="utf-8"))["last_run_at"] == out["run_at"]


def test_run_self_heal_debounces_when_previous_run_was_clean(state_file, monkeypatch):
    _no_actions(monkeypatch)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "runs": [{"run_at": "2026-11-14T18:30:00", "reference_trade_date": "2026-11-13", "gaps": {}}],
    }), encoding="utf-8")
    out = sh.run_self_heal(object(), now=datetime(2026, 11, 14, 18, 45))
    assert out["status"] == "skipped"
    assert "距上次自愈" in out["reason"]


def test_every_core_table_has_a_declared_repair_owner():
    """静态护栏：新增核心表却没配补数归属时，测试必须失败。

    scan_gaps 会检测 12 张物理表；如果某张表既不在诊断补数清单、也没有专门动作、
    又没被显式登记为「不可自动补回」，它就会变成每轮报缺口却没人负责的静默盲区。
    """
    owners = sh._REMEDIABLE_VIA_DIAGNOSIS | sh._DEDICATED_ACTIONS | sh._NO_AUTO_REPAIR
    core = {t for t, _ in sh._CORE_DAILY_TABLES}
    assert core <= owners, f"未登记补数归属的核心表：{sorted(core - owners)}"
    assert not (sh._REMEDIABLE_VIA_DIAGNOSIS & sh._DEDICATED_ACTIONS)
    assert not (sh._DEDICATED_ACTIONS & sh._NO_AUTO_REPAIR)
    assert any(name == "stk_limits" for name, _ in sh._ACTIONS)
    assert any(name == "auction_data" for name, _ in sh._ACTIONS)


def test_remediate_skips_when_only_unrepairable_table_is_missing(monkeypatch):
    """只有 stock_quotes 缺口时别白跑一次昂贵的冷诊断，也不能伪造快照。"""
    called = []
    monkeypatch.setattr("app.services.data_gap_remediation_service.remediate_data_health_gaps",
                        lambda *a, **k: called.append(1) or {"status": "ok"})
    gaps = {"2026-11-13": ["stock_quotes"]}
    out = sh._act_remediate(object(), ["2026-11-13"], gaps)
    assert out["status"] == "skipped" and called == []
    # 专门动作只对各自负责的表出手
    assert sh._act_stk_limits(object(), ["2026-11-13"], gaps)["status"] == "skipped"
    assert sh._act_auction_data(object(), ["2026-11-13"], gaps)["status"] == "skipped"


def test_dedicated_actions_target_only_their_gap_days(monkeypatch):
    monkeypatch.setattr("app.services.scheduler_service._backfill_stk_limit_window",
                        lambda db, **k: {"backfilled_dates": [
                            {"trade_date": "2026-11-12", "status": "success", "count": 5500},
                            {"trade_date": "2026-11-13", "status": "success", "count": 5501}]})
    gaps = {"2026-11-12": ["stk_limits"], "2026-11-13": ["stk_limits", "daily_basic"]}
    out = sh._act_stk_limits(object(), ["2026-11-12", "2026-11-13"], gaps)
    assert out["status"] == "completed" and out["target_days"] == ["2026-11-12", "2026-11-13"]
    assert out["backfilled"]["2026-11-13"] == 5501


def test_fault_injection_repaired_gap_is_clean(state_file, monkeypatch):
    """故障注入：某日核心表缺失 → 动作跑完 → 复扫归零 → completed 且不告警。"""
    calls = _no_actions(monkeypatch)
    scans = [{"2026-11-13": ["index_daily"]}, {}]
    monkeypatch.setattr(sh, "scan_gaps", lambda db, days: scans.pop(0))
    monkeypatch.setattr("app.core.alerts.emit_alert",
                        lambda *a, **k: pytest.fail("缺口已修复，不应告警"))
    out = sh.run_self_heal(object(), now=datetime(2026, 11, 14, 18, 45))
    assert calls == ["a", "b", "c"]
    assert out["status"] == "completed" and out["remaining_gaps"] == {}
    assert out["gaps"] == {"2026-11-13": ["index_daily"]}


def test_fault_injection_unrepaired_gap_alerts_without_faking_snapshot(state_file, monkeypatch):
    """补不回的缺口必须显式告警；快照类缺口不得被自动组装成看起来像真的数据。"""
    _no_actions(monkeypatch)
    monkeypatch.setattr(sh, "scan_gaps",
                        lambda db, days: {"2026-11-13": ["stock_quotes"]})
    alerts: list[tuple] = []
    monkeypatch.setattr("app.core.alerts.emit_alert",
                        lambda *a, **k: alerts.append(a) or True)
    out = sh.run_self_heal(object(), now=datetime(2026, 11, 14, 18, 45))
    assert out["status"] == "partial"
    assert alerts and alerts[0][1] == "self_heal_unrepaired"
    assert "stock_quotes" in alerts[0][2]
    assert out["unrepaired"]["no_auto_repair_tables"] == ["stock_quotes"]
    assert out["unrepaired"]["alerted"] is True
    # 动作清单里没有任何「自动补快照」的动作
    assert not any("quote" in name or "snapshot" in name for name in out["actions"])


def test_event_parquets_only_refresh_when_stale(monkeypatch):
    """事件 parquet 有日更动作，但新鲜时不得打上游。"""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert any(name == "event_parquets" for name, _ in sh._ACTIONS)

    monkeypatch.setattr(sh, "_event_parquet_latest",
                        lambda year: {"holdertrade_in": "20261113", "holdertrade_de": "20261113"})
    fresh = sh._act_event_parquets(object(), ["2026-11-12", "2026-11-13"], {})
    assert fresh["status"] == "skipped" and "已新鲜" in fresh["reason"]

    # 滞后超过门槛时应当真正触发；这里把子进程替换掉，只验证会走取数分支
    calls: list[list[str]] = []
    monkeypatch.setattr(sh, "_event_parquet_latest",
                        lambda year: {"holdertrade_in": "20261105", "holdertrade_de": "20261105"})
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: (
        calls.append(cmd) or type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()))
    out = sh._act_event_parquets(object(), ["2026-11-12", "2026-11-13"], {})
    assert out["status"] == "completed"
    assert calls and calls[0][-2:] == ["events", "events_de"]
    assert set(out["before"]) == {"holdertrade_in", "holdertrade_de"}


def test_output_gaps_are_archived_but_no_ticket_is_created(state_file, monkeypatch):
    """实盘出票错过无法事后补记为 live_forward：自愈只归档事实，不创建 run。"""
    _no_actions(monkeypatch)
    monkeypatch.setattr(sh, "_archive_output_gaps", _ORIG_ARCHIVE)
    monkeypatch.setattr("app.services.daily_output_check.daily_output_status",
                        lambda db, day, now=None: {"ok": False, "status": "failed",
                                                   "reason": "短线 0/5", "short_done": 0, "short_expected": 5})
    out = sh.run_self_heal(object(), now=datetime(2026, 11, 14, 18, 45))
    assert set(out["output_missed"]) == {"2026-11-12", "2026-11-13"}
    assert out["output_missed"]["2026-11-13"]["short_done"] == 0
    # 动作清单里没有任何「补出票」类动作
    assert not any("output" in name or "ticket" in name for name in out["actions"])

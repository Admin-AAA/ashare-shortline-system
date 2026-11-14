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

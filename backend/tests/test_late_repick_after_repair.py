"""21:00 晚间补数后补跑当天正式出票（10-30/11-03/11-04：17:35 当天日K/涨停板未到被闸门硬阻断，短线 0/4，
21:00 补上日K后没人重跑，要等次日 08:30 安全网）。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import midterm_live_seal as seal
from app.services import scheduler_service as ss

TD = "2026-11-04"
ALL = ["chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader"]


class _DB:
    def __init__(self, payload: dict | None = None):
        self.payload = payload if payload is not None else {"pre_run_full_sync": True, "score_line": 72, "run_name": "x"}
        self.rolled = 0

    def get(self, _model, task_id):
        assert task_id == ss.POSTCLOSE_PIPELINE_TASK_ID
        return SimpleNamespace(payload_json=json.dumps(self.payload))

    def rollback(self):
        self.rolled += 1


@pytest.fixture
def env(monkeypatch):
    calls: dict = {"standalone": [], "readiness": 0, "midlong": 0, "alerts": []}
    state = {"missing": list(ALL), "done": {}, "midlong_todo": [], "inflight": 0, "gate": True, "before_open": True}
    monkeypatch.setattr(ss, "_latest_allowed_bar_date", lambda: TD)
    monkeypatch.setattr(ss, "_missing_standalone_strategies", lambda db, d: list(state["missing"]))
    monkeypatch.setattr(ss, "_completed_standalone_runs", lambda db, p: dict(state["done"]))
    monkeypatch.setattr(ss, "_midlong_error_strategies", lambda d: list(state["midlong_todo"]))
    monkeypatch.setattr(ss, "_formal_inflight_count", lambda db, d: state["inflight"])
    monkeypatch.setattr(seal, "generated_before_entry_open", lambda d, now: state["before_open"])

    def readiness(db, d):
        calls["readiness"] += 1
        assert d == TD
        return {"can_run_local_recommendation": state["gate"],
                "blockers": [] if state["gate"] else ["日K线未覆盖 2026-11-04（最新 2026-11-03）"]}

    monkeypatch.setattr(ss, "_diagnose_postclose_readiness", readiness)
    monkeypatch.setattr(ss, "_run_outcome_label", lambda db, p: {"labeled": 3})
    from app.services.market_pool import kbar_cache
    monkeypatch.setattr(kbar_cache, "enabled", lambda: False)

    def midlong(db):
        calls["midlong"] += 1
        return {"date": TD, "retried": list(state["midlong_todo"]), "still_error": []}

    monkeypatch.setattr(ss, "_safety_net_midlong_repair", midlong)

    async def standalone(db, payload):
        calls["standalone"].append(payload)
        n = len(payload.get("standalone_skill_ids") or ALL)
        return {"related_run_ids": ["r1"], "strategy_runs": [], "strategy_count": n, "completed_count": n,
                "recommended_count": 2, "final_run": {"status": "completed", "recommended_count": 2}}

    monkeypatch.setattr(ss, "_run_standalone_recommendations", standalone)
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda *a, **k: calls["alerts"].append(a))
    return state, calls


def _run(db=None):
    return asyncio.run(ss._late_repair_repick(db or _DB(), "postclose_late_data_repair"))


def test_hook_only_on_evening_repair():
    assert ss.LATE_REPICK_HOOK_TASK_IDS == {"postclose_late_data_repair"}


def test_gate_still_blocked_skips(env):
    state, calls = env
    state["gate"] = False
    out = _run()
    assert out["status"] == "skipped" and out["reason"] == "gate_blocked"
    assert "日K线未覆盖" in out["blockers"][0]
    assert calls["standalone"] == [] and calls["midlong"] == 0


def test_gate_passed_reruns_whole_day_with_safety_net_payload(env):
    state, calls = env
    out = _run()
    assert out["status"] == "completed" and out["recommendation"]["completed_count"] == 4
    (p,) = calls["standalone"]
    assert p["trade_date_override"] == TD and p["run_mode"] == "formal"
    assert p["data_source"] == ss.DATA_SOURCE_LOCAL_POOL and p["skip_aux_refresh"] is True
    assert "pre_run_full_sync" not in p and "standalone_skill_ids" not in p
    assert p["score_line"] == 72  # 复用 post_sync_formal_recommendation 的 payload
    assert calls["alerts"] == []


def test_partial_day_only_reruns_missing(env):
    state, calls = env
    state["missing"] = ALL[2:]
    state["done"] = {ALL[0]: {"run_id": "a"}, ALL[1]: {"run_id": "b"}}
    _run()
    (p,) = calls["standalone"]
    assert p["standalone_skill_ids"] == ALL[2:]


def test_already_completed_does_nothing(env):
    state, calls = env
    state["missing"] = []
    state["done"] = {s: {"run_id": s} for s in ALL}
    out = _run()
    assert out == {**out, "status": "skipped", "reason": "already_completed"}
    assert calls["readiness"] == 0 and calls["standalone"] == [] and calls["midlong"] == 0


def test_short_done_but_midlong_data_error_repairs_midlong_only(env):
    state, calls = env
    state["missing"] = []
    state["done"] = {s: {"run_id": s} for s in ALL}
    state["midlong_todo"] = ["mid-earnings-accel"]
    out = _run()
    assert out["status"] == "completed" and calls["midlong"] == 1 and calls["standalone"] == []


@pytest.mark.parametrize("key,val,reason", [("inflight", 2, "inflight"), ("before_open", False, "past_entry_open")])
def test_inflight_or_past_entry_open_skips(env, key, val, reason):
    state, calls = env
    state[key] = val
    out = _run()
    assert out["status"] == "skipped" and out["reason"] == reason
    assert calls["standalone"] == [] and calls["readiness"] == 0


def test_failed_rerun_alerts_and_never_raises(env, monkeypatch):
    state, calls = env

    async def boom(db, payload):
        raise RuntimeError("worker down")

    monkeypatch.setattr(ss, "_run_standalone_recommendations", boom)
    db = _DB()
    out = _run(db)
    assert out["status"] == "failed" and "RuntimeError" in out["error"] and db.rolled == 1
    assert calls["alerts"] and calls["alerts"][0][0] == "late_repick_failed"


def test_rerun_not_completed_alerts(env, monkeypatch):
    state, calls = env

    async def failed(db, payload):
        return {"final_run": {"status": "failed"}, "strategy_count": 4, "completed_count": 0}

    monkeypatch.setattr(ss, "_run_standalone_recommendations", failed)
    out = _run()
    assert out["status"] == "failed" and calls["alerts"][0][0] == "late_repick_failed"


def test_research_parquet_export_skipped_in_tests():
    out = ss._export_research_parquets("postclose_late_data_repair")
    assert out["status"] == "skipped" and "stk_limits" in out["tables"]


def test_research_parquet_export_failure_alerts(monkeypatch):
    import subprocess

    alerts = []
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda *a, **k: alerts.append(a))
    out = ss._export_research_parquets("postclose_late_data_repair")
    assert out["status"] == "failed" and alerts and alerts[0][0] == "research_parquet_export_failed"
    assert "stk_limits" in alerts[0][2]

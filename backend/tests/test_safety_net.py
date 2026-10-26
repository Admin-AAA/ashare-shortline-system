#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Formal Run 安全网测试：跨交易日断档检测 + 可恢复补跑。"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import scheduler_service
from app.services.scheduler_service import check_formal_recommendation_gap


@pytest.fixture(autouse=True)
def _single_standalone_strategy(monkeypatch):
    """单策略独立运行后，盘后/安全网对每个策略各派发一个 run；这里固定为 1 个策略，
    保持原有「派发 → 等待」时序断言。"""
    monkeypatch.setattr(
        scheduler_service,
        "_standalone_run_payloads",
        lambda _db, payload: [{**payload, "single_skill_id": "s1", "run_mode": "formal"}],
    )



@pytest.fixture(autouse=True)
def _no_midlong_repair(monkeypatch, request):
    """既有用例只关心 formal 补跑；中长线补跑另有专门用例（test_safety_net_midlong_*）。"""
    if "midlong" in request.node.name or "missing_strategies" in request.node.name:
        return
    monkeypatch.setattr(scheduler_service, "_safety_net_midlong_repair", lambda _db: {"date": None, "retried": [], "still_error": []})


def test_check_gap_no_runs(db):
    """最近 5 天无 formal run → needs_retry=True。"""
    # 清空最近 formal run（用 mock 或直接检查逻辑）
    # 由于使用真实 DB，这里验证函数签名和返回结构
    result = check_formal_recommendation_gap(db)
    assert "needs_retry" in result
    assert "trade_date" in result
    assert "reason" in result
    assert "ok_count" in result
    assert "total_count" in result


@pytest.mark.requires_market_data
def test_check_gap_already_has_recommendations(db):
    """已有 completed formal 的日期不会因为推荐数为 0 而反复重跑。"""
    result = check_formal_recommendation_gap(db)
    assert isinstance(result["needs_retry"], bool)
    if not result["missing_dates"]:
        assert result["needs_retry"] is False
        assert "completed formal" in result["reason"]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _GapDb:
    def __init__(self, trade_dates, run_rows):
        self._responses = iter((_Rows([(d,) for d in trade_dates]), _Rows(run_rows)))
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return next(self._responses)


def test_check_gap_finds_older_hole_after_newer_success(monkeypatch):
    """7/28 成功不能遮住 7/27 缺口；按最早缺口收敛。"""
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-07-28")
    db = _GapDb(
        ["2026-07-28", "2026-07-27", "2026-07-24", "2026-07-23", "2026-07-22"],
        [
            ("2026-07-28", 1, 1, 0, 3),
            ("2026-07-24", 1, 0, 0, 1),
            ("2026-07-23", 1, 1, 0, 1),
            ("2026-07-22", 1, 1, 0, 1),
        ],
    )

    result = check_formal_recommendation_gap(db)

    assert result["needs_retry"] is True
    assert result["trade_date"] == "2026-07-27"
    assert result["missing_dates"] == ["2026-07-27"]
    assert "run_mode='formal'" in db.statements[1][0]


def test_check_gap_accepts_completed_protective_zero_and_suppresses_inflight(monkeypatch):
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-07-28")
    complete_db = _GapDb(
        ["2026-07-28"],
        [("2026-07-28", 1, 0, 0, 1)],
    )
    complete = check_formal_recommendation_gap(complete_db)
    assert complete["needs_retry"] is False
    assert complete["productive_count"] == 0

    inflight_db = _GapDb(
        ["2026-07-28"],
        [("2026-07-28", 0, 0, 1, 4)],
    )
    inflight = check_formal_recommendation_gap(inflight_db)
    assert inflight["needs_retry"] is False
    assert "在途" in inflight["reason"]


def test_safety_net_replays_target_date_and_waits_for_terminal_state(monkeypatch):
    check = {
        "needs_retry": True,
        "trade_date": "2026-07-27",
        "ok_count": 0,
        "total_count": 1,
        "missing_dates": ["2026-07-27"],
        "reason": "missing",
    }
    task = SimpleNamespace(payload_json=json.dumps({
        "preset_id": "preset_formal_opening",
        "data_source": "local_pool_preferred",
        "skip_aux_refresh": False,
        "run_mode": "formal",
    }))

    class _Db:
        def get(self, _model, _task_id):
            return task

    captured = {}

    async def fake_run(_db, payload):
        captured["payload"] = dict(payload)
        return {"related_run_id": "run_recovery", "queued": True}

    async def fake_wait(_db, run_id, **kwargs):
        captured["wait"] = (run_id, kwargs)
        return {"status": "completed", "recommended_count": 0}

    monkeypatch.setattr(scheduler_service, "check_formal_recommendation_gap", lambda _db: check)
    monkeypatch.setattr(scheduler_service, "_run_recommendation", fake_run)
    monkeypatch.setattr(scheduler_service, "_wait_for_recommendation_run", fake_wait)

    result = asyncio.run(scheduler_service._safety_net_run(_Db(), {}))

    assert captured["payload"]["trade_date_override"] == "2026-07-27"
    assert captured["payload"]["data_source"] == "local_pool"
    assert captured["payload"]["skip_aux_refresh"] is True
    assert captured["wait"][0] == "run_recovery"
    assert result["status"] == "completed"
    assert result["recommended_count"] == 0


def test_safety_net_failed_worker_converges_to_failed_result(monkeypatch):
    check = {
        "needs_retry": True,
        "trade_date": "2026-07-27",
        "ok_count": 0,
        "total_count": 2,
        "missing_dates": ["2026-07-27"],
        "reason": "missing",
    }

    class _Db:
        def get(self, _model, _task_id):
            return None

    async def fake_run(_db, _payload):
        return {"related_run_id": "run_failed", "queued": True}

    async def fake_wait(_db, _run_id, **_kwargs):
        return {"status": "failed", "error": "worker boom", "recommended_count": 0}

    monkeypatch.setattr(scheduler_service, "check_formal_recommendation_gap", lambda _db: check)
    monkeypatch.setattr(scheduler_service, "_run_recommendation", fake_run)
    monkeypatch.setattr(scheduler_service, "_wait_for_recommendation_run", fake_wait)

    result = asyncio.run(scheduler_service._safety_net_run(_Db(), {}))

    assert result["status"] == "failed"
    assert result["error"] == "worker boom"
    assert scheduler_service._task_result_error(result) == "worker boom"


def test_safety_net_task_type_constant():
    """任务类型常量正确定义。"""
    from app.services.scheduler_service import TASK_TYPE_FORMAL_SAFETY_NET
    assert TASK_TYPE_FORMAL_SAFETY_NET == "formal_safety_net"


def test_safety_net_task_id_constant():
    """任务 ID 常量正确定义。"""
    from app.services.scheduler_service import SAFETY_NET_TASK_ID
    assert SAFETY_NET_TASK_ID == "formal_run_safety_net"


# ---- 2026-10-26 复盘后的加强：部分策略缺批次 / 中长线 data_error 盘前补跑 ----

def _gap_db_all_dates_completed():
    """5 个交易日都有 completed formal（旧逻辑判无缺口）。"""
    dates = ["2026-10-26", "2026-10-23", "2026-10-22", "2026-10-21", "2026-10-20"]

    class _R:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _D:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "DISTINCT trade_date" in sql and "GROUP BY" not in sql:
                return _R([(d,) for d in dates])
            return _R([(d, 1, 1, 0, 1) for d in dates])

    return _D()


def test_gap_missing_strategies_triggers_partial_retry_for_latest_date(monkeypatch):
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-10-26")
    monkeypatch.setattr(scheduler_service, "_missing_standalone_strategies", lambda _db, d: ["s2", "s3"])
    res = check_formal_recommendation_gap(_gap_db_all_dates_completed())
    assert res["needs_retry"] is True and res["trade_date"] == "2026-10-26"
    assert res["missing_strategies"] == ["s2", "s3"] and res["missing_dates"] == []


def test_gap_missing_strategies_none_keeps_old_behaviour(monkeypatch):
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-10-26")
    monkeypatch.setattr(scheduler_service, "_missing_standalone_strategies", lambda _db, d: [])
    res = check_formal_recommendation_gap(_gap_db_all_dates_completed())
    assert res["needs_retry"] is False


def test_missing_strategies_fails_open_on_fake_db():
    assert scheduler_service._missing_standalone_strategies(object(), "2026-10-26") == []


def test_safety_net_midlong_repairs_only_data_error_or_unrecorded(monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-10-26")
    status = ms._status_dir()
    status.mkdir(parents=True, exist_ok=True)
    rows = {sid: {"status": "ok"} for sid in ms.STRATEGIES}
    rows["long-growth-streak"] = {"status": "data_error"}
    rows["mid-incentive-option"] = {"status": "no_signal"}
    del rows["long-net-cash-switch"]  # 从未记录
    (status / "2026-10-26.json").write_text(json.dumps(rows))
    seen = {}

    def fake_generate(db, as_of, force=False, horizon=None, strategy_ids=None, apply_daily_cap=False):
        seen.update(as_of=as_of, ids=sorted(strategy_ids), cap=apply_daily_cap)
        return {"long-growth-streak": "no_data", "long-net-cash-switch": 3}

    monkeypatch.setattr(ms, "generate_picks", fake_generate)
    emitted = []
    monkeypatch.setattr("app.core.alerts.emit_alert", lambda kind, key, msg, **k: emitted.append((kind, key, msg)) or True)
    out = scheduler_service._safety_net_midlong_repair(None)
    assert seen == {"as_of": "2026-10-26", "ids": ["long-growth-streak", "long-net-cash-switch"], "cap": True}
    assert out["still_error"] == ["long-growth-streak"] and out["picks"] == {"long-net-cash-switch": 3}
    assert emitted and emitted[0][0] == "midlong_repair_data_error"


def test_safety_net_midlong_noop_when_all_ok(monkeypatch, tmp_path):
    from app.services import midterm_service as ms

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-10-26")
    status = ms._status_dir()
    status.mkdir(parents=True, exist_ok=True)
    (status / "2026-10-26.json").write_text(json.dumps({sid: {"status": "ok"} for sid in ms.STRATEGIES}))
    monkeypatch.setattr(ms, "generate_picks", lambda *a, **k: pytest.fail("不应调用"))
    out = scheduler_service._safety_net_midlong_repair(None)
    assert out["retried"] == [] and "error" not in out


def test_safety_net_midlong_failure_is_non_blocking(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", boom)
    out = scheduler_service._safety_net_midlong_repair(None)
    assert "RuntimeError" in out["error"]

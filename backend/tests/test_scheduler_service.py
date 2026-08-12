import asyncio
import uuid

import pytest
from sqlalchemy import update

from app.models.tables import Preset, ScheduledTaskRun
from app.services import scheduler_service


def test_run_preset_matrix_tags_all_active_presets(monkeypatch, db):
    db.execute(update(Preset).where(Preset.status == "active").values(status="disabled"))
    preset_a_id = f"preset_matrix_active_a_{uuid.uuid4().hex[:8]}"
    preset_b_id = f"preset_matrix_active_b_{uuid.uuid4().hex[:8]}"
    db.add_all(
        [
            Preset(
                preset_id=preset_a_id,
                name="矩阵预设A",
                market_scope="all",
                risk_template="balanced",
                score_line=72,
                output_count=3,
                bucket_limits_json="{}",
                data_source="local_pool",
                hide_recent_repeats=True,
                status="active",
            ),
            Preset(
                preset_id=preset_b_id,
                name="矩阵预设B",
                market_scope="all",
                risk_template="balanced",
                score_line=70,
                output_count=2,
                bucket_limits_json="{}",
                data_source="local_pool",
                hide_recent_repeats=True,
                status="active",
            ),
            Preset(
                preset_id=f"preset_matrix_disabled_{uuid.uuid4().hex[:8]}",
                name="矩阵停用预设",
                market_scope="all",
                risk_template="balanced",
                score_line=72,
                output_count=3,
                bucket_limits_json="{}",
                data_source="local_pool",
                hide_recent_repeats=True,
                status="disabled",
            ),
        ]
    )
    db.commit()

    calls = []

    async def fake_run_workbench(_db, payload):
        calls.append(dict(payload))
        return {
            "run": {
                "run_id": f"run_{payload['preset_id']}",
                "recommended_count": 2,
            }
        }

    monkeypatch.setattr("app.services.workbench_service.run_workbench", fake_run_workbench)

    result = asyncio.run(
        scheduler_service._run_preset_matrix(
            db,
            {
                "matrix_batch_id": "matrix_test_batch",
                "matrix_batch_name": "测试矩阵",
                "run_name_prefix": "测试矩阵",
                "data_source": "local_pool",
                "skip_aux_refresh": True,
            },
        )
    )

    assert result["matrix_batch_id"] == "matrix_test_batch"
    assert result["completed_count"] == 2
    assert result["failed_count"] == 0
    assert result["recommended_count"] == 4
    assert {call["preset_id"] for call in calls} == {preset_a_id, preset_b_id}
    assert all(call["matrix_batch_id"] == "matrix_test_batch" for call in calls)
    assert all(call["matrix_run"] is True for call in calls)
    assert all(call["data_source"] == "local_pool" for call in calls)
    assert all(call["skip_aux_refresh"] is True for call in calls)


def test_run_preset_matrix_continues_after_single_preset_failure(monkeypatch, db):
    db.execute(update(Preset).where(Preset.status == "active").values(status="disabled"))
    failing_preset_id = f"preset_matrix_fail_first_{uuid.uuid4().hex[:8]}"
    successful_preset_id = f"preset_matrix_ok_second_{uuid.uuid4().hex[:8]}"
    db.add_all(
        [
            Preset(
                preset_id=failing_preset_id,
                name="矩阵失败预设",
                market_scope="all",
                risk_template="balanced",
                score_line=72,
                output_count=3,
                bucket_limits_json="{}",
                data_source="local_pool",
                hide_recent_repeats=True,
                status="active",
            ),
            Preset(
                preset_id=successful_preset_id,
                name="矩阵成功预设",
                market_scope="all",
                risk_template="balanced",
                score_line=72,
                output_count=3,
                bucket_limits_json="{}",
                data_source="local_pool",
                hide_recent_repeats=True,
                status="active",
            ),
        ]
    )
    db.commit()

    async def fake_run_workbench(_db, payload):
        if payload["preset_id"] == failing_preset_id:
            raise RuntimeError("boom")
        return {"run": {"run_id": "run_ok", "recommended_count": 1}}

    monkeypatch.setattr("app.services.workbench_service.run_workbench", fake_run_workbench)

    result = asyncio.run(scheduler_service._run_preset_matrix(db, {"matrix_batch_id": "matrix_test_failure"}))

    assert result["completed_count"] == 1
    assert result["failed_count"] == 1
    assert result["recommended_count"] == 1
    status_by_preset = {row["preset_id"]: row["status"] for row in result["runs"]}
    assert status_by_preset[failing_preset_id] == "failed"
    assert status_by_preset[successful_preset_id] == "completed"


@pytest.mark.parametrize("run_mode", ("formal", "experiment", "backtest"))
def test_run_preset_matrix_defaults_to_local_pool_preferred(monkeypatch, db, run_mode):
    db.execute(update(Preset).where(Preset.status == "active").values(status="disabled"))
    preset_id = f"preset_matrix_default_source_{uuid.uuid4().hex[:8]}"
    db.add(
        Preset(
            preset_id=preset_id,
            name="矩阵默认数据源预设",
            market_scope="all",
            risk_template="balanced",
            score_line=72,
            output_count=3,
            bucket_limits_json="{}",
            data_source="local_pool",
            hide_recent_repeats=True,
            status="active",
        )
    )
    db.commit()

    calls = []

    async def fake_run_workbench(_db, payload):
        calls.append(dict(payload))
        return {"run": {"run_id": "run_default_source", "recommended_count": 1}}

    monkeypatch.setattr("app.services.workbench_service.run_workbench", fake_run_workbench)

    result = asyncio.run(
        scheduler_service._run_preset_matrix(
            db,
            {"matrix_batch_id": "matrix_default_source", "run_mode": run_mode},
        )
    )

    assert result["completed_count"] == 1
    assert calls[0]["data_source"] == "local_pool_preferred"
    assert calls[0]["run_mode"] == run_mode


def test_run_data_sync_daily_bars_uses_trade_date_batch_remediation(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        scheduler_service,
        "SessionLocal",
        lambda: type("FakeDb", (), {"close": lambda self: None})(),
    )
    monkeypatch.setattr(
        scheduler_service,
        "_run_backfill_script",
        lambda payload: (_ for _ in ()).throw(AssertionError("should not call legacy per-stock script")),
    )

    def _fake_remediate(_db, **kwargs):
        calls.update(kwargs)
        return {
            "status": "completed",
            "action_summary": {"completed_count": 4, "failed_count": 0},
            "comparison": {"tables_fully_remediated": ["minute_kbar"]},
        }

    monkeypatch.setattr("app.services.data_gap_remediation_service.remediate_data_health_gaps", _fake_remediate)

    result = scheduler_service._run_data_sync(
        object(),
        {"sync_scope": "daily_bars", "health_daily_kbar_code_limit": 123, "health_post_diagnosis": False},
    )

    assert calls["target_tables"] == ["daily_kbar"]
    assert calls["daily_kbar_code_limit"] == 123
    assert calls["post_diagnosis"] is False
    assert "backfill" not in result
    assert "health_remediation" not in result
    assert result["daily_kbar_batch"]["action_summary"]["completed_count"] == 4
    assert result["steps"] == [{"name": "daily_kbar_batch", "status": "completed"}]
    assert "日K按日批量补数 4 / 失败 0" in result["summary_text"]


def test_run_data_sync_runs_health_remediation_as_independent_scope(monkeypatch):
    monkeypatch.setattr(
        scheduler_service,
        "_run_daily_kbar_batch",
        lambda payload: (_ for _ in ()).throw(AssertionError("should not run daily batch")),
    )
    monkeypatch.setattr(
        scheduler_service,
        "_run_auxiliary_script",
        lambda payload: (_ for _ in ()).throw(AssertionError("should not run auxiliary")),
    )
    monkeypatch.setattr(
        scheduler_service,
        "_run_data_health_remediation",
        lambda payload: {
            "status": "completed",
            "action_summary": {"completed_count": 2, "failed_count": 1},
        },
    )

    result = scheduler_service._run_data_sync(
        object(),
        {"sync_scope": "health_remediation"},
    )

    assert result["health_remediation"]["action_summary"]["completed_count"] == 2
    assert result["steps"] == [{"name": "health_remediation", "status": "completed"}]
    assert "健康补数完成 2 / 失败 1" in result["summary_text"]


def test_run_data_sync_surfaces_empty_remediation_as_partial_completed(monkeypatch):
    monkeypatch.setattr(
        scheduler_service,
        "_run_daily_kbar_batch",
        lambda payload: (_ for _ in ()).throw(AssertionError("should not run daily batch")),
    )
    monkeypatch.setattr(
        scheduler_service,
        "_run_data_health_remediation",
        lambda payload: {
            "status": "partial_completed",
            "action_summary": {"completed_count": 2, "empty_count": 1, "skipped_count": 0, "failed_count": 0},
            "actions": [{"table": "limit_up_board", "trade_date": "2026-06-11", "status": "empty", "row_count": 0}],
        },
    )

    result = scheduler_service._run_data_sync(object(), {"sync_scope": "health_remediation"})

    assert result["status"] == "partial_completed"
    assert result["steps"] == [{"name": "health_remediation", "status": "partial_completed"}]
    assert result["summary_text"] == "健康补数完成 2 / 空结果待重试 1 / 失败 0"


def test_run_data_sync_runs_auxiliary_remediation_as_independent_scope(monkeypatch):
    monkeypatch.setattr(
        scheduler_service,
        "_run_auxiliary_script",
        lambda payload: (_ for _ in ()).throw(AssertionError("should not run legacy auxiliary script")),
    )

    seen = {}

    def fake_auxiliary(payload):
        seen.update(payload)
        return {
            "status": "completed",
            "action_summary": {"completed_count": 3, "failed_count": 0},
        }

    monkeypatch.setattr(scheduler_service, "_run_auxiliary_remediation", fake_auxiliary)

    result = scheduler_service._run_data_sync(
        object(),
        {"sync_scope": "auxiliary", "flow_top_n": 123},
    )

    assert seen["sync_scope"] == "auxiliary"
    assert seen["flow_top_n"] == 123
    assert result["auxiliary"]["action_summary"]["completed_count"] == 3
    assert result["steps"] == [{"name": "auxiliary", "status": "completed"}]
    assert "辅助数据补数完成 3 / 失败 0" in result["summary_text"]


def test_start_preset_matrix_now_returns_running_task_without_blocking(monkeypatch):
    scheduler_service._mark_done_memory(scheduler_service.MANUAL_PRESET_MATRIX_TASK_ID)
    started_threads = []
    task_run = ScheduledTaskRun(
        task_run_id="taskrun_manual_matrix_unit",
        task_id=scheduler_service.MANUAL_PRESET_MATRIX_TASK_ID,
        task_name="测试手工矩阵",
        task_type=scheduler_service.TASK_TYPE_PRESET_MATRIX_RUN,
        trigger_source="manual",
        status="running",
        payload_json='{"matrix_batch_name":"测试手工矩阵"}',
        result_json="{}",
    )

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            started_threads.append(self)

    class FakeDb:
        def close(self):
            pass

    def fake_start_task_run(_db, task, *, trigger_source):
        assert task.task_id == scheduler_service.MANUAL_PRESET_MATRIX_TASK_ID
        assert task.task_type == scheduler_service.TASK_TYPE_PRESET_MATRIX_RUN
        assert trigger_source == "manual"
        return task_run

    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(scheduler_service, "_latest_running_preset_matrix_task_run", lambda _db: None)
    monkeypatch.setattr(scheduler_service, "start_task_run", fake_start_task_run)
    monkeypatch.setattr(
        scheduler_service,
        "get_latest_preset_matrix_task_run",
        lambda: {
            "task_run_id": task_run.task_run_id,
            "task_type": task_run.task_type,
            "status": task_run.status,
            "payload": {"matrix_batch_name": "测试手工矩阵"},
        },
    )
    orchestration_runs = []
    monkeypatch.setattr(
        "app.orchestration.trigger_manual_matrix",
        lambda task_run_id, payload: orchestration_runs.append((task_run_id, payload)),
    )

    result = scheduler_service.start_preset_matrix_now({"matrix_batch_name": "测试手工矩阵"})

    assert result["started"] is True
    assert result["task_run"]["status"] == "running"
    assert result["task_run"]["task_type"] == scheduler_service.TASK_TYPE_PRESET_MATRIX_RUN
    assert result["task_run"]["payload"]["matrix_batch_name"] == "测试手工矩阵"
    assert orchestration_runs[0][0] == task_run.task_run_id
    assert orchestration_runs[0][1]["matrix_batch_name"] == "测试手工矩阵"

    scheduler_service._mark_done_memory(scheduler_service.MANUAL_PRESET_MATRIX_TASK_ID)


def test_start_preset_matrix_now_reuses_existing_running_task(monkeypatch):
    scheduler_service._mark_done_memory(scheduler_service.MANUAL_PRESET_MATRIX_TASK_ID)
    run = ScheduledTaskRun(
        task_run_id=f"taskrun_matrix_running_{uuid.uuid4().hex[:8]}",
        task_id=scheduler_service.MANUAL_PRESET_MATRIX_TASK_ID,
        task_name="运行中的矩阵",
        task_type=scheduler_service.TASK_TYPE_PRESET_MATRIX_RUN,
        trigger_source="manual",
        status="running",
        payload_json='{"matrix_batch_name":"运行中的矩阵"}',
        result_json="{}",
    )

    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(scheduler_service, "_latest_running_preset_matrix_task_run", lambda _db: run)
    monkeypatch.setattr(
        scheduler_service,
        "serialize_scheduled_task_run",
        lambda task_run: {
            "task_run_id": task_run.task_run_id,
            "status": task_run.status,
            "payload": {"matrix_batch_name": "运行中的矩阵"},
        },
    )
    monkeypatch.setattr(scheduler_service.threading, "Thread", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not start")))

    result = scheduler_service.start_preset_matrix_now({"matrix_batch_name": "新的矩阵"})

    assert result["started"] is False
    assert result["task_run"]["task_run_id"] == run.task_run_id
    assert result["task_run"]["status"] == "running"
    assert result["task_run"]["payload"]["matrix_batch_name"] == "运行中的矩阵"

    scheduler_service._mark_done_memory(scheduler_service.MANUAL_PRESET_MATRIX_TASK_ID)


def test_start_data_health_remediation_now_returns_running_task_without_blocking(monkeypatch):
    scheduler_service._mark_done_memory(scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
    started_threads = []
    task_run = ScheduledTaskRun(
        task_run_id="taskrun_manual_data_health_unit",
        task_id=scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID,
        task_name="手动数据补数",
        task_type="data_backfill",
        trigger_source="manual",
        status="running",
        payload_json='{"post_diagnosis":true}',
        result_json="{}",
    )

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            started_threads.append(self)

    class FakeDb:
        def close(self):
            pass

    def fake_start_task_run(_db, task, *, trigger_source):
        assert task.task_id == scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID
        assert task.task_type == "data_backfill"
        assert trigger_source == "manual"
        assert '"post_diagnosis": true' in task.payload_json
        return task_run

    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(scheduler_service, "_latest_running_task_run", lambda _db, task_id: None)
    monkeypatch.setattr(scheduler_service, "start_task_run", fake_start_task_run)
    monkeypatch.setattr(
        scheduler_service,
        "get_latest_data_health_remediation_task_run",
        lambda: {
            "task_run_id": task_run.task_run_id,
            "task_id": task_run.task_id,
            "task_name": task_run.task_name,
            "task_type": task_run.task_type,
            "status": task_run.status,
            "payload": {"post_diagnosis": True},
        },
    )
    orchestration_runs = []
    monkeypatch.setattr(
        "app.orchestration.trigger_manual_remediation",
        lambda task_run_id, payload: orchestration_runs.append((task_run_id, payload)),
    )

    result = scheduler_service.start_data_health_remediation_now({"post_diagnosis": True})

    assert result["started"] is True
    assert result["task_run"]["status"] == "running"
    assert result["task_run"]["task_id"] == scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID
    assert orchestration_runs[0][0] == task_run.task_run_id
    assert orchestration_runs[0][1]["post_diagnosis"] is True

    scheduler_service._mark_done_memory(scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)


def test_start_data_health_remediation_now_reuses_existing_running_task(monkeypatch):
    scheduler_service._mark_done_memory(scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
    run = ScheduledTaskRun(
        task_run_id=f"taskrun_data_health_running_{uuid.uuid4().hex[:8]}",
        task_id=scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID,
        task_name="运行中的数据补数",
        task_type="data_backfill",
        trigger_source="manual",
        status="running",
        payload_json='{"post_diagnosis":true}',
        result_json="{}",
    )

    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(scheduler_service, "_latest_running_task_run", lambda _db, task_id: run)
    monkeypatch.setattr(
        scheduler_service,
        "serialize_scheduled_task_run",
        lambda task_run: {
            "task_run_id": task_run.task_run_id,
            "status": task_run.status,
            "payload": {"post_diagnosis": True},
        },
    )
    monkeypatch.setattr(scheduler_service.threading, "Thread", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not start")))

    result = scheduler_service.start_data_health_remediation_now({"post_diagnosis": True})

    assert result["started"] is False
    assert result["task_run"]["task_run_id"] == run.task_run_id
    assert result["task_run"]["status"] == "running"

    scheduler_service._mark_done_memory(scheduler_service.MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)


def test_run_manual_data_health_remediation_wraps_task_result(monkeypatch):
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.remediate_data_health_gaps",
        lambda _db, **kwargs: {
            "status": "completed",
            "actions": [{"table": "daily_kbar", "status": "completed"}],
            "action_summary": {"failed_count": 0},
            "comparison": {
                "target_missing_day_count": 2,
                "resolved_missing_day_count": 2,
            },
            "seen": kwargs,
        },
    )

    result = scheduler_service._run_manual_data_health_remediation(
        object(),
        {
            "target_tables": ["daily_kbar"],
            "daily_kbar_code_limit": 123,
            "capital_flow_code_limit": 45,
            "minute_kbar_code_limit": 67,
            "post_diagnosis": True,
        },
    )

    assert result["remediation_type"] == "data_health"
    assert result["summary_text"] == "数据补数完成 1 项，修复缺口 2/2，失败 0"
    assert result["seen"]["target_tables"] == ["daily_kbar"]
    assert result["seen"]["daily_kbar_code_limit"] == 123
    assert result["seen"]["capital_flow_code_limit"] == 45
    assert result["seen"]["minute_kbar_code_limit"] == 67
    assert result["seen"]["post_diagnosis"] is True


def test_run_manual_data_health_remediation_summary_surfaces_empty_count(monkeypatch):
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.remediate_data_health_gaps",
        lambda _db, **kwargs: {
            "status": "partial_completed",
            "actions": [{"table": "limit_up_board", "status": "empty"}],
            "action_summary": {"failed_count": 0, "empty_count": 1},
            "comparison": {
                "target_missing_day_count": 1,
                "resolved_missing_day_count": 0,
                "unresolved_missing_day_count": 1,
            },
        },
    )

    result = scheduler_service._run_manual_data_health_remediation(object(), {"target_tables": ["limit_up_board"]})

    assert result["status"] == "partial_completed"
    assert result["summary_text"] == "数据补数完成 1 项，修复缺口 0/1，仍缺 1，失败 0，空结果待重试 1"


def test_execute_manual_data_health_remediation_failure_writes_small_payload(monkeypatch):
    """失败路径的收尾必须保留原始错误，且二次写库的 payload 必须足够小，
    不能因为（可能内嵌完整明细的）result 写不进去而再次失败、丢失原始错误。"""
    from datetime import datetime, timezone

    import json as _json

    from app.services.cost_service import _RESULT_JSON_MAX_BYTES

    captured: dict = {}
    huge = "x" * ((_RESULT_JSON_MAX_BYTES // 4) + 1)

    def fake_finish_task_run(_db, task_run_id, *, status, error, summary, result):
        captured["status"] = status
        captured["error"] = error
        captured["result"] = result
        payload = _json.dumps(result, ensure_ascii=False, default=str)
        assert len(payload.encode("utf-8")) <= _RESULT_JSON_MAX_BYTES

    class FakeDb:
        def __init__(self):
            self.committed = False

        def rollback(self):
            self.committed = True

        def close(self):
            pass

    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(scheduler_service, "finish_task_run", fake_finish_task_run)
    monkeypatch.setattr(
        scheduler_service,
        "_run_manual_data_health_remediation",
        lambda _db, _payload: (_ for _ in ()).throw(RuntimeError(huge)),
    )

    scheduler_service._execute_manual_data_health_remediation(
        "taskrun_failure_path_test", {"target_tables": ["daily_kbar"]}
    )

    assert captured["status"] == "failed"
    assert captured["error"] == huge
    assert captured["result"]["remediation_type"] == "data_health"
    assert captured["result"]["summary_text"] == huge

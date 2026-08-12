from __future__ import annotations

import json
from datetime import datetime

from app.models.tables import ScheduledTaskRun
from app.services.cost_service import (
    _RESULT_JSON_MAX_BYTES,
    _task_result_payload,
    _task_run_summary_text,
)


def test_task_result_payload_keeps_normal_result_untouched():
    result = {"status": "completed", "action_summary": {"completed_count": 2}}

    payload = _task_result_payload(result)

    assert json.loads(payload) == result
    assert "truncated" not in json.loads(payload)


def test_task_result_payload_truncates_oversized_result_to_summary():
    """补数结果内嵌完整诊断明细（如 present_dates 全量列表）时超过 MEDIUMTEXT 上限，
    此前 finish_task_run 写库直接 1406 失败；必须降级为摘要而不是丢弃原始错误。"""
    result = {
        "status": "partial_failed",
        "remediation_type": "data_health",
        "summary_text": "数据补数完成 3 项，修复缺口 0/1，仍缺 1，失败 1",
        "actions": [{"table": "daily_kbar", "status": "completed"}],
        "action_summary": {"planned_count": 3, "completed_count": 2, "failed_count": 1},
        "before": {"score": 60},
        "after": {"score": 70},
        "comparison": {
            "status": "done",
            "score_delta": 10,
            "issue_count_delta": -2,
            "resolved_missing_day_count": 1,
            "unresolved_missing_day_count": 2,
            "target_missing_day_count": 3,
            "table_deltas": {
                "daily_kbar": {
                    "before_missing_dates": ["2026-08-01", "2026-08-02"],
                    "resolved_missing_dates": ["2026-08-02"],
                    # 巨型明细：模拟诊断内嵌全量日期列表（真实故障里 ~29MB）
                    "unresolved_missing_dates": [
                        "2026-01-02" for _ in range(_RESULT_JSON_MAX_BYTES // 2)
                    ],
                }
            },
        },
        "diagnosis_detail": {"recent_trade_day_coverage": {"tables": {}}},
    }

    payload = _task_result_payload(result)
    stored = json.loads(payload)

    assert len(payload.encode("utf-8")) <= _RESULT_JSON_MAX_BYTES
    assert stored["truncated"] == "result_json_too_large"
    assert stored["status"] == "partial_failed"
    assert stored["remediation_type"] == "data_health"
    assert stored["summary_text"] == result["summary_text"]
    assert stored["action_summary"] == result["action_summary"]
    assert stored["comparison"]["score_delta"] == 10
    assert "before" not in stored
    assert "diagnosis_detail" not in stored


def test_task_result_payload_empty_result():
    assert _task_result_payload(None) == "{}"
    assert _task_result_payload({}) == "{}"


def test_finish_task_run_commits_oversized_result_as_summary(production_mysql, db):
    """回归：真实 MySQL MEDIUMTEXT(16MiB) 上限下，内嵌完整诊断明细的补数结果
    此前 commit 抛 1406，任务收尾失败。降级后收尾必须能正常提交。"""
    import uuid

    from app.models.tables import ScheduledTask
    from app.services.cost_service import finish_task_run

    task_id = f"__test_result_json_{uuid.uuid4().hex[:12]}"
    task_run_id = f"taskrun_{uuid.uuid4().hex[:12]}"
    task = ScheduledTask(
        task_id=task_id,
        name="__test_result_json__",
        task_type="data_backfill",
        cron_expression="0 0 * * *",
        payload_json="{}",
        enabled=False,
    )
    db.add(task)
    db.commit()
    db.add(
        ScheduledTaskRun(
            task_run_id=task_run_id,
            task_id=task_id,
            task_name="手动数据补数",
            task_type="data_backfill",
            trigger_source="manual",
            status="running",
            payload_json="{}",
            result_json="{}",
        )
    )
    db.commit()

    try:
        oversized = {
            "status": "partial_failed",
            "remediation_type": "data_health",
            "summary_text": "数据补数完成 3 项，修复缺口 0/1，仍缺 1，失败 1",
            # >16MiB（MEDIUMTEXT 上限）的超大字段：修复前直接 1406 收尾失败
            "huge_blob": "x" * 20_000_000,
        }
        finish_task_run(
            db,
            task_run_id,
            status="partial_failed",
            summary="数据补数完成 3 项，修复缺口 0/1，仍缺 1，失败 1",
            result=oversized,
        )
        db.refresh(db.get(ScheduledTaskRun, task_run_id))
        stored = json.loads(db.get(ScheduledTaskRun, task_run_id).result_json)
        assert stored["status"] == "partial_failed"
        assert stored["truncated"] == "result_json_too_large"
        assert "huge_blob" not in stored
        assert stored["summary_text"] == oversized["summary_text"]
    finally:
        db.execute(
            __import__("sqlalchemy").delete(ScheduledTaskRun).where(
                ScheduledTaskRun.task_run_id == task_run_id
            )
        )
        db.delete(db.get(ScheduledTask, task_id))
        db.commit()


def test_task_run_summary_text_for_data_health_remediation():
    run = ScheduledTaskRun(
        task_run_id="taskrun_manual_data_health",
        task_id="manual_data_health_remediation",
        task_name="手动数据补数",
        task_type="data_backfill",
        trigger_source="manual",
        started_at=datetime(2026, 5, 27, 10, 0),
        status="completed",
        payload_json="{}",
        result_json="{}",
    )
    result = {
        "remediation_type": "data_health",
        "actions": [{"table": "index_daily"}, {"table": "capital_flow"}],
        "comparison": {
            "issue_count_delta": -3,
            "score_delta": 11,
        },
    }

    summary = _task_run_summary_text(run, result)

    assert summary == "数据补数 2 项，问题数 -3，评分 +11"


def test_task_run_summary_text_for_data_health_skipped_post_diagnosis():
    run = ScheduledTaskRun(
        task_run_id="taskrun_manual_data_health",
        task_id="manual_data_health_remediation",
        task_name="手动数据补数",
        task_type="data_backfill",
        trigger_source="manual",
        started_at=datetime(2026, 5, 27, 10, 0),
        status="completed",
        payload_json="{}",
        result_json="{}",
    )
    result = {
        "remediation_type": "data_health",
        "actions": [{"table": "daily_kbar"}, {"table": "index_daily"}],
        "comparison": {"status": "skipped", "reason": "post_diagnosis=false"},
    }

    summary = _task_run_summary_text(run, result)

    assert summary == "数据补数 2 项，已跳过后置诊断"


def test_task_run_summary_text_for_data_health_pre_diagnosis_failed():
    run = ScheduledTaskRun(
        task_run_id="taskrun_manual_data_health",
        task_id="manual_data_health_remediation",
        task_name="手动数据补数",
        task_type="data_backfill",
        trigger_source="manual",
        started_at=datetime(2026, 5, 27, 10, 0),
        status="diagnosis_failed",
        payload_json="{}",
        result_json="{}",
    )
    result = {
        "remediation_type": "data_health",
        "actions": [],
        "comparison": {"status": "skipped", "reason": "pre_diagnosis_failed"},
    }

    summary = _task_run_summary_text(run, result)

    assert summary == "数据补数未执行，前置诊断失败"


def test_task_run_summary_text_for_data_health_post_diagnosis_failed():
    run = ScheduledTaskRun(
        task_run_id="taskrun_manual_data_health",
        task_id="manual_data_health_remediation",
        task_name="手动数据补数",
        task_type="data_backfill",
        trigger_source="manual",
        started_at=datetime(2026, 5, 27, 10, 0),
        status="completed_post_diagnosis_failed",
        payload_json="{}",
        result_json="{}",
    )
    result = {
        "remediation_type": "data_health",
        "actions": [{"table": "daily_kbar"}, {"table": "index_daily"}],
        "comparison": {"status": "skipped", "reason": "post_diagnosis_failed"},
    }

    summary = _task_run_summary_text(run, result)

    assert summary == "数据补数 2 项，后置诊断失败待复核"


def test_task_run_summary_text_for_minute_gap_remediation():
    run = ScheduledTaskRun(
        task_run_id="taskrun_manual_minute_gap",
        task_id="manual_minute_gap_remediation",
        task_name="手动分钟补数",
        task_type="data_backfill",
        trigger_source="manual",
        started_at=datetime(2026, 5, 27, 10, 0),
        status="completed",
        payload_json="{}",
        result_json="{}",
    )
    result = {
        "remediation_type": "minute_gap",
        "actions": [{"scope": "stock"}],
        "comparison": {
            "candidate_coverage_rate_delta": 0.65,
            "benchmark_coverage_rate_delta": 0.5,
            "remediation_coverage_rate": 0.6,
            "unresolved_missing_unit_count": 2,
            "gate_passed_after": True,
        },
    }

    summary = _task_run_summary_text(run, result)

    assert summary == "分钟补数 1 步，候选覆盖 +65.0%，基准覆盖 +50.0%，修复率 60.0%，仍缺 2，门禁通过"


def test_task_run_summary_text_for_preset_matrix_run():
    run = ScheduledTaskRun(
        task_run_id="taskrun_matrix",
        task_id="daily_preset_matrix",
        task_name="盘后预设矩阵",
        task_type="preset_matrix_run",
        trigger_source="schedule",
        started_at=datetime(2026, 6, 4, 15, 30),
        status="completed",
        payload_json="{}",
        result_json="{}",
    )
    result = {
        "preset_count": 4,
        "completed_count": 3,
        "failed_count": 1,
        "recommended_count": 9,
    }

    summary = _task_run_summary_text(run, result)

    assert summary == "预设矩阵 3/4 完成，失败 1，推荐 9 条"


def test_recover_scheduled_task_runs_marks_stuck_idle_task_interrupted(db):
    """L-044 卡死残留：is_running=True 但 last_status 停在旧值 idle 的任务，
    重启恢复时必须标记 interrupted（原逻辑只在 last_status in {None, running}
    时标记，卡死任务 last_status=idle 漏标，last_error 无法区分新旧故障）。"""
    import uuid
    from datetime import datetime, timezone

    from app.models.tables import ScheduledTask
    from app.services.cost_service import recover_scheduled_task_runs

    task_id = f"__test_stuck_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task = ScheduledTask(
        task_id=task_id,
        name="__test_stuck__",
        task_type="recommendation_run",
        cron_expression="35 17 * * mon-fri",
        payload_json="{}",
        enabled=True,
        is_running=True,          # 卡死残留
        last_status="idle",       # 旧值非 running
        last_error="post_sync 卡死在 news 补数",
        last_run_at=now,
    )
    db.add(task)
    db.commit()

    try:
        recovered = recover_scheduled_task_runs(db)
        db.refresh(task)
        assert recovered >= 1
        assert task.is_running is False
        assert task.last_status == "interrupted"
        # 原 last_error 诊断痕迹应保留（不覆盖）
        assert "news 补数" in (task.last_error or "")
    finally:
        db.delete(db.get(ScheduledTask, task_id))
        db.commit()

from datetime import datetime

from app.models.tables import ScheduledTaskRun
from app.services.cost_service import _task_run_summary_text


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

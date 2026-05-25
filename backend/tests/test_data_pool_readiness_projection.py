from app.services.data_pool.health_projection import summarize_alerts, summarize_tasks
from app.services.data_pool.readiness import summarize_pool_status


def test_summarize_pool_status_reports_ready_counts():
    status = {
        "unified_quote_count": 4300,
        "active_unified_quote_count": 4100,
        "eligible_quote_count": 2600,
        "daily_bar_coverage_rate": 0.92,
        "eligible_daily_bar_coverage_rate": 0.96,
        "recommendation_readiness": {
            "status": "ready",
            "can_run_local_recommendation": True,
            "messages": ["本地池可运行"],
        },
    }

    summary = summarize_pool_status(status)

    assert summary["status"] == "ready"
    assert summary["quote_total"] == 4300
    assert summary["eligible_quote_count"] == 2600
    assert summary["can_run_local_recommendation"] is True
    assert summary["bar_coverage_pct"] == 92.0


def test_summarize_alerts_groups_severity():
    alerts = [
        {"severity": "high", "message": "报价源全部失败"},
        {"severity": "medium", "message": "日K覆盖不足"},
    ]

    summary = summarize_alerts(alerts)

    assert summary["high"] == 1
    assert summary["medium"] == 1
    assert summary["messages"] == ["报价源全部失败", "日K覆盖不足"]


def test_summarize_tasks_counts_running_and_failures():
    tasks = [
        {"task_id": "a", "enabled": True, "is_running": True, "last_status": "completed"},
        {"task_id": "b", "enabled": True, "is_running": False, "last_status": "failed", "last_error": "boom"},
    ]

    summary = summarize_tasks(tasks)

    assert summary["enabled_count"] == 2
    assert summary["running_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["last_errors"] == ["boom"]

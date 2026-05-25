from __future__ import annotations

from typing import Any


def summarize_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"high": 0, "medium": 0, "low": 0, "messages": []}
    for alert in alerts:
        severity = str(alert.get("severity") or "low")
        if severity not in {"high", "medium", "low"}:
            severity = "low"
        summary[severity] += 1
        message = str(alert.get("message") or "")
        if message:
            summary["messages"].append(message)
    return summary


def summarize_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    enabled = [task for task in tasks if task.get("enabled")]
    running = [task for task in enabled if task.get("is_running")]
    failed = [task for task in enabled if task.get("last_status") == "failed"]
    return {
        "enabled_count": len(enabled),
        "running_count": len(running),
        "failed_count": len(failed),
        "last_errors": [str(task.get("last_error")) for task in failed if task.get("last_error")],
    }

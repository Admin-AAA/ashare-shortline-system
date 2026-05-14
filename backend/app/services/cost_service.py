"""LLM API 成本追踪与系统仪表盘服务。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import ApiCallLog, RecommendationRun, ScheduledTask


def record_api_call(
    db: Session,
    *,
    run_id: str | None = None,
    skill_id: str | None = None,
    model: str = "unknown",
    endpoint: str = "",
    prompt_chars: int = 0,
    completion_chars: int = 0,
    latency_ms: float = 0,
    status: str = "success",
    error_message: str | None = None,
) -> ApiCallLog:
    """记录一次 LLM API 调用。"""
    log = ApiCallLog(
        run_id=run_id,
        skill_id=skill_id,
        model=model,
        endpoint=endpoint,
        prompt_chars=prompt_chars,
        completion_chars=completion_chars,
        latency_ms=latency_ms,
        status=status,
        error_message=error_message,
    )
    db.add(log)
    db.commit()
    return log


def cost_summary(db: Session, days: int = 30) -> dict[str, Any]:
    """API 成本汇总。"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    logs = db.scalars(
        select(ApiCallLog).where(ApiCallLog.created_at >= cutoff)
    ).all()

    total_calls = len(logs)
    success_calls = sum(1 for log in logs if log.status == "success")
    error_calls = total_calls - success_calls

    total_prompt_chars = sum(log.prompt_chars for log in logs)
    total_completion_chars = sum(log.completion_chars for log in logs)
    total_latency_ms = sum(log.latency_ms for log in logs)

    # 按模型统计
    by_model: dict[str, dict[str, Any]] = {}
    for log in logs:
        model = log.model
        if model not in by_model:
            by_model[model] = {"calls": 0, "prompt_chars": 0, "completion_chars": 0, "errors": 0}
        by_model[model]["calls"] += 1
        by_model[model]["prompt_chars"] += log.prompt_chars
        by_model[model]["completion_chars"] += log.completion_chars
        if log.status != "success":
            by_model[model]["errors"] += 1

    # 按 Skill 统计
    by_skill: dict[str, dict[str, Any]] = {}
    for log in logs:
        skill = log.skill_id or "unknown"
        if skill not in by_skill:
            by_skill[skill] = {"calls": 0, "prompt_chars": 0, "errors": 0}
        by_skill[skill]["calls"] += 1
        by_skill[skill]["prompt_chars"] += log.prompt_chars
        if log.status != "success":
            by_skill[skill]["errors"] += 1

    return {
        "period_days": days,
        "total_calls": total_calls,
        "success_calls": success_calls,
        "error_calls": error_calls,
        "error_rate": round(error_calls / max(1, total_calls), 3),
        "total_prompt_chars": total_prompt_chars,
        "total_completion_chars": total_completion_chars,
        "avg_latency_ms": round(total_latency_ms / max(1, total_calls), 1),
        "by_model": [
            {"model": model, **stats}
            for model, stats in sorted(by_model.items(), key=lambda x: x[1]["calls"], reverse=True)
        ],
        "by_skill": [
            {"skill_id": skill, **stats}
            for skill, stats in sorted(by_skill.items(), key=lambda x: x[1]["calls"], reverse=True)
        ],
    }


def system_health(db: Session) -> dict[str, Any]:
    """系统健康仪表盘数据。"""
    now = datetime.utcnow()

    # 最近运行
    recent_runs = db.scalars(
        select(RecommendationRun)
        .order_by(RecommendationRun.started_at.desc())
        .limit(10)
    ).all()

    run_stats = {
        "total": len(recent_runs),
        "completed": sum(1 for r in recent_runs if r.status == "completed"),
        "failed": sum(1 for r in recent_runs if r.status == "failed"),
        "running": sum(1 for r in recent_runs if r.status == "running"),
    }

    # API 错误率（最近 7 天）
    week_ago = now - timedelta(days=7)
    week_logs = db.scalars(
        select(ApiCallLog).where(ApiCallLog.created_at >= week_ago)
    ).all()
    week_calls = len(week_logs)
    week_errors = sum(1 for log in week_logs if log.status != "success")

    # 活跃调度任务
    scheduled = db.scalars(
        select(ScheduledTask).where(ScheduledTask.enabled == True)
    ).all()

    return {
        "timestamp": now.isoformat(),
        "recent_runs": run_stats,
        "api_calls_7d": week_calls,
        "api_errors_7d": week_errors,
        "api_error_rate_7d": round(week_errors / max(1, week_calls), 3),
        "scheduled_tasks": len(scheduled),
        "scheduled_tasks_enabled": sum(1 for t in scheduled if t.enabled),
        "latest_run_at": max((r.started_at for r in recent_runs if r.started_at), default=None),
    }


def list_scheduled_tasks(db: Session) -> list[dict[str, Any]]:
    """列出所有调度任务。"""
    tasks = db.scalars(select(ScheduledTask)).all()
    return [
        {
            "task_id": t.task_id,
            "name": t.name,
            "task_type": t.task_type,
            "cron_expression": t.cron_expression,
            "enabled": t.enabled,
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
            "last_status": t.last_status,
            "last_error": t.last_error,
        }
        for t in tasks
    ]


def upsert_scheduled_task(
    db: Session,
    task_id: str,
    name: str,
    task_type: str,
    cron_expression: str,
    payload: dict[str, Any] | None = None,
    enabled: bool = True,
) -> ScheduledTask:
    """创建或更新调度任务。"""
    task = db.get(ScheduledTask, task_id)
    if task is None:
        task = ScheduledTask(task_id=task_id)
        db.add(task)
    task.name = name
    task.task_type = task_type
    task.cron_expression = cron_expression
    task.payload_json = __import__("json").dumps(payload or {})
    task.enabled = enabled
    task.updated_at = datetime.utcnow()
    db.commit()
    return task


def record_task_run(
    db: Session,
    task_id: str,
    status: str,
    error: str | None = None,
) -> None:
    """记录调度任务执行结果。"""
    task = db.get(ScheduledTask, task_id)
    if task:
        task.last_run_at = datetime.utcnow()
        task.last_status = status
        task.last_error = error
        db.commit()

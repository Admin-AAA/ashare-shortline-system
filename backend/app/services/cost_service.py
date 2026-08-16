"""LLM API 成本追踪与系统仪表盘服务。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# result_json/payload_json 落库上限。MySQL MEDIUMTEXT 上限 16 MiB；保留安全余量，
# 超过即降级为摘要，避免「结果本身写不进去」把一次任务收尾变成二次失败。
_RESULT_JSON_MAX_BYTES = 4 * 1024 * 1024

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.models.tables import ApiCallLog, RecommendationRun, ScheduledTask, ScheduledTaskRun


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
    # 估算字段（agno_skill_agent 会传，但 ApiCallLog 表不存储，仅用于运行时汇总）
    prompt_tokens_est: int | None = None,
    completion_tokens_est: int | None = None,
    # 真实用量（来自 provider usage 字段，drain_api_costs 传入；None 表示未取得真实值）
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cache_hit_tokens: int | None = None,
    # prompt context 投影/裁剪审计摘要（drain_api_costs 传入；不含完整 prompt 原文）
    context_audit: dict[str, Any] | None = None,
) -> ApiCallLog:
    """记录一次 LLM API 调用。

    prompt_tokens_est / completion_tokens_est 是调用方估算的 token 数，仅用于运行时汇总。
    prompt_tokens / completion_tokens / cache_hit_tokens 是 provider 返回的真实用量，持久化到表。
    """
    log = ApiCallLog(
        run_id=run_id,
        skill_id=skill_id,
        model=model,
        endpoint=endpoint,
        prompt_chars=prompt_chars,
        completion_chars=completion_chars,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_hit_tokens=cache_hit_tokens,
        latency_ms=latency_ms,
        status=status,
        error_message=error_message,
        context_audit_json=(
            json.dumps(context_audit, ensure_ascii=False, sort_keys=True, default=str)
            if context_audit is not None
            else None
        ),
    )
    db.add(log)
    db.commit()
    return log


def cost_summary(db: Session, days: int = 30) -> dict[str, Any]:
    """API 成本汇总。"""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)

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
    week_errors = sum(
        1 for log in week_logs if log.status not in {"success", "cache_hit"}
    )

    # 活跃调度任务
    scheduled = db.scalars(
        select(ScheduledTask).where(ScheduledTask.enabled == True)
    ).all()
    latest_run_at = max(
        (r.started_at for r in recent_runs if r.started_at),
        default=None,
    )

    return {
        "timestamp": now.isoformat(),
        "recent_runs": run_stats,
        "api_calls_7d": week_calls,
        "api_errors_7d": week_errors,
        "api_error_rate_7d": round(week_errors / max(1, week_calls), 3),
        "scheduled_tasks": len(scheduled),
        "scheduled_tasks_enabled": sum(1 for t in scheduled if t.enabled),
        "latest_run_at": latest_run_at.isoformat() if latest_run_at else None,
    }


def list_scheduled_tasks(db: Session) -> list[dict[str, Any]]:
    """列出所有调度任务。"""
    tasks = db.scalars(select(ScheduledTask).order_by(ScheduledTask.created_at.asc())).all()
    return [
        {
            "task_id": t.task_id,
            "name": t.name,
            "task_type": t.task_type,
            "cron_expression": t.cron_expression,
            "trigger_time": _daily_trigger_time(t.cron_expression),
            "payload": _loads(t.payload_json, {}),
            "enabled": t.enabled,
            "is_running": t.is_running,
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
            "last_status": t.last_status,
            "last_error": t.last_error,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in tasks
    ]


def list_scheduled_task_runs(
    db: Session,
    limit: int = 100,
    *,
    task_id: str | None = None,
    include_result: bool = True,
) -> list[dict[str, Any]]:
    stmt = select(ScheduledTaskRun)
    if not include_result:
        stmt = stmt.options(
            defer(ScheduledTaskRun.result_json),
            defer(ScheduledTaskRun.payload_json),
        )
    if task_id:
        stmt = stmt.where(ScheduledTaskRun.task_id == task_id)
    runs = db.scalars(
        stmt.order_by(ScheduledTaskRun.started_at.desc()).limit(limit)
    ).all()
    related_run_ids: dict[str, str | None] = {}
    if not include_result and runs:
        dialect = str(db.bind.dialect.name if db.bind is not None else "")
        related_expr = func.json_extract(ScheduledTaskRun.result_json, "$.related_run_id")
        if dialect == "mysql":
            related_expr = func.json_unquote(related_expr)
        rows = db.execute(
            select(ScheduledTaskRun.task_run_id, related_expr)
            .where(ScheduledTaskRun.task_run_id.in_([run.task_run_id for run in runs]))
        ).all()
        related_run_ids = {
            str(task_run_id): str(related_run_id) if related_run_id else None
            for task_run_id, related_run_id in rows
        }
    return [
        _serialize_task_run(
            run,
            include_result=include_result,
            related_run_id=related_run_ids.get(run.task_run_id),
        )
        for run in runs
    ]


def serialize_scheduled_task_run(run: ScheduledTaskRun) -> dict[str, Any]:
    return _serialize_task_run(run)


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
    task.payload_json = json.dumps(payload or {}, ensure_ascii=False)
    task.enabled = enabled
    task.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return task


def delete_scheduled_task(db: Session, task_id: str) -> bool:
    """删除调度任务。"""
    task = db.get(ScheduledTask, task_id)
    if task is None:
        return False
    db.delete(task)
    db.commit()
    return True


def start_task_run(
    db: Session,
    task: ScheduledTask,
    *,
    trigger_source: str = "schedule",
) -> ScheduledTaskRun:
    """为自动任务创建一条执行历史。"""
    task_run = ScheduledTaskRun(
        task_run_id=f"taskrun_{uuid.uuid4().hex[:24]}",
        task_id=task.task_id,
        task_name=task.name,
        task_type=task.task_type,
        trigger_source=trigger_source,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status="running",
        payload_json=task.payload_json or "{}",
        result_json="{}",
    )
    db.add(task_run)
    db.commit()
    return task_run


def _task_result_payload(result: dict[str, Any] | None) -> str:
    """把任务结果序列化为 result_json；过大时降级为摘要，保证收尾写库永不因超限失败。"""
    if not result:
        return "{}"
    payload = json.dumps(result, ensure_ascii=False, default=str)
    if len(payload.encode("utf-8")) <= _RESULT_JSON_MAX_BYTES:
        return payload
    logger.warning(
        "task result_json 过大（%d 字节），降级保存为摘要；原因通常是结果内嵌了完整诊断明细",
        len(payload.encode("utf-8")),
    )
    return json.dumps(_task_result_summary(result), ensure_ascii=False, default=str)


def _task_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """结果降级摘要：保留 status/摘要/错误等复盘与前端渲染所需的字段，丢弃巨型明细。"""
    summary: dict[str, Any] = {}
    for key in ("status", "summary_text", "remediation_type", "requested_tables", "target_tables"):
        if key in result:
            summary[key] = result[key]
    for key in ("error", "error_type"):
        if key in result and result[key] is not None:
            summary[key] = _bounded_error(result[key])
    action_summary = result.get("action_summary")
    if isinstance(action_summary, dict):
        summary["action_summary"] = action_summary
    comparison = result.get("comparison")
    if isinstance(comparison, dict) and comparison.get("status") == "skipped":
        summary["comparison"] = {
            key: _bounded_error(comparison[key]) if key in ("message", "error") else comparison[key]
            for key in ("status", "reason", "message", "error_type", "error")
            if key in comparison
        }
    if isinstance(comparison, dict):
        summary["comparison"] = {
            key: comparison[key]
            for key in (
                "issue_count_delta",
                "score_delta",
                "max_missing_days_delta",
                "target_missing_day_count",
                "resolved_missing_day_count",
                "unresolved_missing_day_count",
                "remediation_coverage_rate",
                "tables_fully_remediated",
                "tables_still_missing",
            )
            if key in comparison
        }
    if "truncated" not in summary:
        summary["truncated"] = "result_json_too_large"
    return summary


def _bounded_error(value: Any) -> Any:
    """错误字符串可能内嵌完整 SQL/参数（如 1406 报错带 result_json 原文），截断防再超限。"""
    if not isinstance(value, str):
        return value
    if len(value) <= 4000:
        return value
    return f"{value[:3997]}..."


def finish_task_run(
    db: Session,
    task_run_id: str,
    *,
    status: str,
    error: str | None = None,
    summary: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """结束自动任务执行历史，并回写任务最新状态。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    task_run = db.get(ScheduledTaskRun, task_run_id)
    task_id = task_run.task_id if task_run is not None else None
    if task_run is not None:
        task_run.finished_at = now
        task_run.status = status
        task_run.summary = summary
        task_run.error_message = error
        task_run.result_json = _task_result_payload(result)

    task = db.get(ScheduledTask, task_id) if task_id else None
    if task is not None:
        task.last_run_at = now
        task.last_status = status
        task.last_error = error
        task.updated_at = now
    db.commit()


def record_task_run(
    db: Session,
    task_id: str,
    status: str,
    error: str | None = None,
    *,
    summary: str | None = None,
    trigger_source: str = "api",
    result: dict[str, Any] | None = None,
    task_name: str | None = None,
    task_type: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """兼容旧调用：直接补记一条已结束的自动任务执行历史。"""
    task = db.get(ScheduledTask, task_id)
    if not task and not (task_name and task_type):
        return
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    db.add(
        ScheduledTaskRun(
            task_run_id=f"taskrun_{uuid.uuid4().hex[:24]}",
            task_id=task.task_id if task else task_id,
            task_name=task.name if task else str(task_name or task_id),
            task_type=task.task_type if task else str(task_type or "data_backfill"),
            trigger_source=trigger_source,
            started_at=started_at,
            finished_at=started_at,
            status=status,
            summary=summary,
            error_message=error,
            payload_json=task.payload_json if task else payload_json,
            result_json=_task_result_payload(result),
        )
    )
    if task is not None:
        task.last_run_at = started_at
        task.last_status = status
        task.last_error = error
        task.updated_at = started_at
    db.commit()


def recover_scheduled_task_runs(db: Session) -> int:
    """启动时恢复残留的自动任务运行状态。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recovered = 0
    stale_message = "服务启动时发现上次自动任务未正常结束，已标记为中断"

    stale_tasks = db.scalars(select(ScheduledTask).where(ScheduledTask.is_running == True)).all()
    for task in stale_tasks:
        # is_running=True 本身就是「上次运行未正常结束」的强证据（正常路径
        # finish_task_run 后 finally 必清 is_running）。卡死任务 last_status 可能停在
        # 任意旧值（如 idle，L-044 卡死场景），不能用 last_status 是否 running 判定残留
        # ——否则卡死任务漏标 interrupted，last_error 无法区分新旧故障。
        task.is_running = False
        task.last_status = "interrupted"
        # 保留原 last_error 追溯信息（如卡死原因），仅补充中断标记，不覆盖诊断痕迹。
        task.last_error = (
            f"{task.last_error or ''}；{stale_message}".strip("；")
            if task.last_error
            else stale_message
        )
        task.last_run_at = task.last_run_at or now
        task.updated_at = now
        recovered += 1

    stale_runs = db.scalars(
        select(ScheduledTaskRun).where(
            ScheduledTaskRun.status == "running",
            ScheduledTaskRun.finished_at.is_(None),
        )
    ).all()
    for run in stale_runs:
        run.status = "interrupted"
        run.finished_at = now
        if not run.error_message:
            run.error_message = stale_message
        recovered += 1

    if recovered:
        db.commit()
    return recovered


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _daily_trigger_time(cron_expression: str | None) -> str | None:
    parts = (cron_expression or "").split()
    if len(parts) != 5:
        return None
    minute, hour, day, month, weekday = parts
    if day != "*" or month != "*" or weekday != "*":
        return None
    try:
        return f"{int(hour):02d}:{int(minute):02d}"
    except ValueError:
        return None


def _serialize_task_run(
    run: ScheduledTaskRun,
    *,
    include_result: bool = True,
    related_run_id: str | None = None,
) -> dict[str, Any]:
    payload = _loads(run.payload_json, {}) if include_result else None
    result = _loads(run.result_json, {}) if include_result else {}
    duration_seconds = None
    if run.started_at and run.finished_at:
        duration_seconds = max(0, int((run.finished_at - run.started_at).total_seconds()))
    return {
        "task_run_id": run.task_run_id,
        "task_id": run.task_id,
        "task_name": run.task_name,
        "task_type": run.task_type,
        "trigger_source": run.trigger_source,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "status": run.status,
        "summary": run.summary,
        "summary_text": _task_run_summary_text(run, result) if include_result else (run.summary or run.error_message or ""),
        "error_message": run.error_message,
        "duration_seconds": duration_seconds,
        "payload": payload,
        "result": result if include_result else None,
        "related_run_id": result.get("related_run_id") if include_result else related_run_id,
    }


def _task_run_summary_text(run: ScheduledTaskRun, result: dict[str, Any]) -> str:
    if run.summary:
        return run.summary

    remediation_type = str(result.get("remediation_type") or "").strip() if isinstance(result, dict) else ""
    if remediation_type == "data_health":
        comparison = result.get("comparison") if isinstance(result, dict) else None
        actions = result.get("actions") if isinstance(result, dict) else None
        if isinstance(comparison, dict):
            action_count = len(actions) if isinstance(actions, list) else 0
            if comparison.get("status") == "skipped":
                reason = str(comparison.get("reason") or "").strip()
                if reason == "pre_diagnosis_failed":
                    return "数据补数未执行，前置诊断失败"
                if reason == "post_diagnosis_failed":
                    return f"数据补数 {action_count} 项，后置诊断失败待复核"
                return f"数据补数 {action_count} 项，已跳过后置诊断"
            issue_delta = int(comparison.get("issue_count_delta") or 0)
            score_delta = float(comparison.get("score_delta") or 0)
            score_prefix = f"{score_delta:+.0f}" if float(score_delta).is_integer() else f"{score_delta:+.1f}"
            return f"数据补数 {action_count} 项，问题数 {issue_delta:+d}，评分 {score_prefix}"
        return "数据健康补数"

    if remediation_type == "minute_gap":
        comparison = result.get("comparison") if isinstance(result, dict) else None
        actions = result.get("actions") if isinstance(result, dict) else None
        if isinstance(comparison, dict):
            cand_delta = float(comparison.get("candidate_coverage_rate_delta") or 0)
            bench_delta = float(comparison.get("benchmark_coverage_rate_delta") or 0)
            remediation_rate = float(comparison.get("remediation_coverage_rate") or 0)
            unresolved = int(comparison.get("unresolved_missing_unit_count") or 0)
            gate_after = "通过" if comparison.get("gate_passed_after") else "未通过"
            action_count = len(actions) if isinstance(actions, list) else 0
            return (
                f"分钟补数 {action_count} 步，候选覆盖 {cand_delta:+.1%}，"
                f"基准覆盖 {bench_delta:+.1%}，修复率 {remediation_rate:.1%}，"
                f"仍缺 {unresolved}，门禁{gate_after}"
            )
        return "分钟缺口补数"

    if run.task_type == "recommendation_run":
        parts: list[str] = []
        related_run_id = str(result.get("related_run_id") or "").strip()
        if related_run_id:
            parts.append(f"关联 Run {related_run_id}")
        if result.get("recommended_count") is not None:
            parts.append(f"推荐 {int(result.get('recommended_count') or 0)} 条")
        return "，".join(parts) if parts else "自动推荐任务"

    if run.task_type == "preset_matrix_run":
        completed = int(result.get("completed_count") or 0)
        total = int(result.get("preset_count") or result.get("run_count") or 0)
        failed = int(result.get("failed_count") or 0)
        recommended = int(result.get("recommended_count") or 0)
        return f"预设矩阵 {completed}/{total} 完成，失败 {failed}，推荐 {recommended} 条"

    backfill = result.get("backfill") if isinstance(result, dict) else None
    if isinstance(backfill, dict) and backfill.get("processed") is not None:
        return (
            f"处理 {int(backfill.get('processed') or 0)}，"
            f"入库 {int(backfill.get('persisted') or 0)}，"
            f"短历史 {int(backfill.get('short_history') or 0)}，"
            f"失败 {int(backfill.get('failed') or 0)}"
        )

    quotes = result.get("quotes") if isinstance(result, dict) else None
    if isinstance(quotes, dict) and quotes.get("refreshed") is not None:
        return f"行情池刷新 {int(quotes.get('refreshed') or 0)} 条"

    stdout_tail = str(result.get("stdout_tail") or "").strip() if isinstance(result, dict) else ""
    if stdout_tail:
        lines = [line.strip() for line in stdout_tail.splitlines() if line.strip()]
        if lines:
            return lines[-1]

    return "-"

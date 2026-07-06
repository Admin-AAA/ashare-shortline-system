"""APScheduler 调度执行器 — 按 Cron 表达式执行 ScheduledTask。

在 FastAPI 启动时创建 BackgroundScheduler，从数据库加载已启用的任务。
支持任务类型:
  - data_sync: 同步行情数据（刷新报价池 + 按日批量日K + 健康补数 + 辅助数据）
  - recommendation_run: 执行推荐流水线
  - preset_matrix_run: 盘后批量执行全部启用预设，用于预设矩阵对比
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import Preset, ScheduledTask, ScheduledTaskRun
from app.services.cost_service import finish_task_run, serialize_scheduled_task_run, start_task_run

logger = logging.getLogger("scheduler")

_scheduler: BackgroundScheduler | None = None
_running_lock = threading.Lock()
_running_tasks: set[str] = set()
TASK_TYPE_PRESET_MATRIX_RUN = "preset_matrix_run"
MANUAL_PRESET_MATRIX_TASK_ID = "manual_preset_matrix_run"
MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID = "manual_data_health_remediation"
DEFAULT_RECOMMENDATION_DATA_SOURCE = "local_pool_preferred"


def _is_task_running(task_id: str) -> bool:
    with _running_lock:
        return task_id in _running_tasks


def _mark_running(task_id: str) -> None:
    with _running_lock:
        _running_tasks.add(task_id)
    _set_db_running(task_id, True)


def _mark_done(task_id: str) -> None:
    with _running_lock:
        _running_tasks.discard(task_id)
    _set_db_running(task_id, False)


def _set_db_running(task_id: str, running: bool) -> None:
    try:
        db = SessionLocal()
        task = db.get(ScheduledTask, task_id)
        if task:
            task.is_running = running
            db.commit()
        db.close()
    except Exception:
        logger.exception("更新 is_running 失败: %s", task_id)


def _mark_running_memory(task_id: str) -> None:
    with _running_lock:
        _running_tasks.add(task_id)


def _try_mark_running_memory(task_id: str) -> bool:
    with _running_lock:
        if task_id in _running_tasks:
            return False
        _running_tasks.add(task_id)
        return True


def _mark_done_memory(task_id: str) -> None:
    with _running_lock:
        _running_tasks.discard(task_id)


def start_scheduler() -> BackgroundScheduler:
    """启动调度器，从数据库加载已启用任务。"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _load_and_schedule()
    _scheduler.start()
    logger.info("调度器已启动，已加载 %d 个任务", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("调度器已停止")


def _load_and_schedule() -> None:
    """从数据库加载已启用的任务并注册到调度器。"""
    if _scheduler is None:
        return
    try:
        db = SessionLocal()
        tasks = db.query(ScheduledTask).filter(ScheduledTask.enabled == True).all()
        for task in tasks:
            try:
                trigger = CronTrigger.from_crontab(task.cron_expression, timezone="Asia/Shanghai")
            except Exception:
                logger.warning("无效 cron 表达式 '%s'，任务 %s 跳过", task.cron_expression, task.task_id)
                continue

            _scheduler.add_job(
                _execute_task,
                trigger=trigger,
                args=[task.task_id],
                id=task.task_id,
                name=task.name,
                replace_existing=True,
            )
        db.close()
    except Exception:
        logger.exception("加载调度任务失败")


def _execute_task(task_id: str, *, force: bool = False, trigger_source: str = "schedule") -> None:
    """执行单个调度任务（同步包装异步）。"""
    if _is_task_running(task_id):
        logger.warning("调度任务 %s 正在执行中，跳过重复触发", task_id)
        return

    logger.info("调度任务开始: %s", task_id)
    _mark_running(task_id)

    db = SessionLocal()
    task_run_id: str | None = None
    try:
        task = db.get(ScheduledTask, task_id)
        if not task or (not task.enabled and not force):
            logger.info("调度任务 %s 已禁用或不存在，跳过", task_id)
            return

        payload = json.loads(task.payload_json or "{}")
        task_type = task.task_type
        task_run_id = start_task_run(db, task, trigger_source=trigger_source).task_run_id

        if task_type == "recommendation_run":
            result = asyncio.run(_run_recommendation(db, payload))
        elif task_type == TASK_TYPE_PRESET_MATRIX_RUN:
            result = asyncio.run(_run_preset_matrix(db, payload, task_run_id=task_run_id))
        elif task_type in ("data_sync", "pool_refresh", "data_backfill"):
            result = _run_data_sync(db, payload, task_type=task_type)
        else:
            raise RuntimeError(f"未知任务类型: {task_type}")

        finish_task_run(
            db,
            task_run_id,
            status=_task_result_status(result),
            summary=str(result.get("summary_text") or ""),
            result=result,
        )
        logger.info("调度任务完成: %s", task_id)
    except Exception as e:
        logger.exception("调度任务失败: %s", task_id)
        db.rollback()
        try:
            if task_run_id:
                finish_task_run(
                    db,
                    task_run_id,
                    status="failed",
                    error=str(e),
                    summary=str(e),
                    result={"summary_text": str(e)},
                )
        except Exception:
            pass
    finally:
        db.close()
        _mark_done(task_id)


def start_preset_matrix_now(payload: dict | None = None) -> dict:
    """异步启动一次手工预设矩阵批次，立即返回任务运行状态。"""
    if not _try_mark_running_memory(MANUAL_PRESET_MATRIX_TASK_ID):
        running = get_latest_preset_matrix_task_run()
        if running and running.get("status") == "running":
            return {"started": False, "task_run": running}
        raise RuntimeError("预设矩阵正在执行中，请等待当前批次完成")

    run_payload = _default_matrix_payload(payload or {})
    db = SessionLocal()
    task_run_id: str | None = None
    try:
        existing_running = _latest_running_preset_matrix_task_run(db)
        if existing_running is not None:
            _mark_done_memory(MANUAL_PRESET_MATRIX_TASK_ID)
            return {"started": False, "task_run": serialize_scheduled_task_run(existing_running)}

        task = _manual_preset_matrix_task(run_payload)
        task_run = start_task_run(db, task, trigger_source="manual")
        task_run_id = task_run.task_run_id
    except Exception:
        _mark_done_memory(MANUAL_PRESET_MATRIX_TASK_ID)
        raise
    finally:
        db.close()

    thread = threading.Thread(
        target=_execute_manual_preset_matrix,
        args=[task_run_id, run_payload],
        name=f"preset-matrix-{task_run_id}",
        daemon=True,
    )
    thread.start()
    latest = get_latest_preset_matrix_task_run()
    return {"started": True, "task_run": latest}


def get_latest_preset_matrix_task_run() -> dict | None:
    db = SessionLocal()
    try:
        run = db.scalars(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.task_type == TASK_TYPE_PRESET_MATRIX_RUN)
            .order_by(ScheduledTaskRun.started_at.desc())
            .limit(1)
        ).first()
        return serialize_scheduled_task_run(run) if run is not None else None
    finally:
        db.close()


def _latest_running_preset_matrix_task_run(db) -> ScheduledTaskRun | None:
    return db.scalars(
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.task_type == TASK_TYPE_PRESET_MATRIX_RUN,
            ScheduledTaskRun.status == "running",
            ScheduledTaskRun.finished_at.is_(None),
        )
        .order_by(ScheduledTaskRun.started_at.desc())
        .limit(1)
    ).first()


def _execute_manual_preset_matrix(task_run_id: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        result = asyncio.run(_run_preset_matrix(db, payload, task_run_id=task_run_id))
        finish_task_run(
            db,
            task_run_id,
            status=_task_result_status(result),
            summary=str(result.get("summary_text") or ""),
            result=result,
        )
    except Exception as exc:
        logger.exception("手工预设矩阵运行失败")
        db.rollback()
        try:
            finish_task_run(
                db,
                task_run_id,
                status="failed",
                error=str(exc),
                summary=str(exc),
                result={"summary_text": str(exc)},
            )
        except Exception:
            logger.exception("手工预设矩阵失败状态写入失败")
    finally:
        db.close()
        _mark_done_memory(MANUAL_PRESET_MATRIX_TASK_ID)


def start_data_health_remediation_now(payload: dict | None = None) -> dict:
    """异步启动一次手工数据健康补数，立即返回任务运行状态。"""
    if not _try_mark_running_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID):
        running = get_latest_data_health_remediation_task_run()
        if running and running.get("status") == "running":
            return {"started": False, "task_run": running}
        raise RuntimeError("数据补数正在执行中，请等待当前批次完成")

    run_payload = _default_data_health_remediation_payload(payload or {})
    db = SessionLocal()
    task_run_id: str | None = None
    try:
        existing_running = _latest_running_task_run(db, MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
        if existing_running is not None:
            _mark_done_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
            return {"started": False, "task_run": serialize_scheduled_task_run(existing_running)}

        task = _manual_data_health_remediation_task(run_payload)
        task_run = start_task_run(db, task, trigger_source="manual")
        task_run_id = task_run.task_run_id
    except Exception:
        _mark_done_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
        raise
    finally:
        db.close()

    thread = threading.Thread(
        target=_execute_manual_data_health_remediation,
        args=[task_run_id, run_payload],
        name=f"data-health-remediation-{task_run_id}",
        daemon=True,
    )
    thread.start()
    latest = get_latest_data_health_remediation_task_run()
    return {"started": True, "task_run": latest}


def get_latest_data_health_remediation_task_run() -> dict | None:
    db = SessionLocal()
    try:
        run = db.scalars(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.task_id == MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
            .order_by(ScheduledTaskRun.started_at.desc())
            .limit(1)
        ).first()
        return serialize_scheduled_task_run(run) if run is not None else None
    finally:
        db.close()


def _latest_running_task_run(db, task_id: str) -> ScheduledTaskRun | None:
    return db.scalars(
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.task_id == task_id,
            ScheduledTaskRun.status == "running",
            ScheduledTaskRun.finished_at.is_(None),
        )
        .order_by(ScheduledTaskRun.started_at.desc())
        .limit(1)
    ).first()


def _execute_manual_data_health_remediation(task_run_id: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        result = _run_manual_data_health_remediation(db, payload)
        finish_task_run(
            db,
            task_run_id,
            status=_task_result_status(result),
            summary=str(result.get("summary_text") or ""),
            result=result,
        )
    except Exception as exc:
        logger.exception("手工数据健康补数失败")
        db.rollback()
        try:
            finish_task_run(
                db,
                task_run_id,
                status="failed",
                error=str(exc),
                summary=str(exc),
                result={"summary_text": str(exc), "remediation_type": "data_health"},
            )
        except Exception:
            logger.exception("手工数据健康补数失败状态写入失败")
    finally:
        db.close()
        _mark_done_memory(MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)


def _run_manual_data_health_remediation(db, payload: dict) -> dict:
    from app.services.data_gap_remediation_service import (
        DEFAULT_DAILY_KBAR_CODE_LIMIT,
        DEFAULT_REMEDIABLE_TABLES,
        remediate_data_health_gaps,
    )

    target_tables = payload.get("target_tables")
    result = remediate_data_health_gaps(
        db,
        target_tables=list(target_tables) if target_tables is not None else None,
        dry_run=False,
        capital_flow_code_limit=int(payload.get("capital_flow_code_limit", 200)),
        daily_kbar_code_limit=int(payload.get("daily_kbar_code_limit", DEFAULT_DAILY_KBAR_CODE_LIMIT)),
        minute_kbar_code_limit=int(payload.get("minute_kbar_code_limit", 300)),
        post_diagnosis=bool(payload.get("post_diagnosis", True)),
    )
    return {
        **result,
        "summary_text": _data_health_remediation_summary(result),
        "remediation_type": "data_health",
        "target_tables": target_tables or list(DEFAULT_REMEDIABLE_TABLES),
    }


def _manual_data_health_remediation_task(payload: dict) -> ScheduledTask:
    task = ScheduledTask(task_id=MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID)
    task.name = "手动数据补数"
    task.task_type = "data_backfill"
    task.cron_expression = "0 0 * * *"
    task.payload_json = json.dumps(payload, ensure_ascii=False)
    task.enabled = False
    task.is_running = True
    return task


def _default_data_health_remediation_payload(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import DEFAULT_DAILY_KBAR_CODE_LIMIT, DEFAULT_REMEDIABLE_TABLES

    return {
        "target_tables": payload.get("target_tables") or list(DEFAULT_REMEDIABLE_TABLES),
        "capital_flow_code_limit": int(payload.get("capital_flow_code_limit", 200)),
        "daily_kbar_code_limit": int(payload.get("daily_kbar_code_limit", DEFAULT_DAILY_KBAR_CODE_LIMIT)),
        "minute_kbar_code_limit": int(payload.get("minute_kbar_code_limit", 300)),
        "post_diagnosis": bool(payload.get("post_diagnosis", True)),
    }


def _data_health_remediation_summary(result: dict) -> str:
    actions = result.get("actions") if isinstance(result, dict) else None
    action_count = len(actions) if isinstance(actions, list) else 0
    summary = result.get("action_summary") or {} if isinstance(result, dict) else {}
    failed_count = int(summary.get("failed_count") or 0)
    empty_count = int(summary.get("empty_count") or 0)
    comparison = result.get("comparison") if isinstance(result, dict) else None
    empty_suffix = f"，空结果待重试 {empty_count}" if empty_count else ""
    if isinstance(comparison, dict) and comparison.get("status") != "skipped":
        resolved = int(comparison.get("resolved_missing_day_count") or 0)
        target = int(comparison.get("target_missing_day_count") or 0)
        unresolved = int(comparison.get("unresolved_missing_day_count") or 0)
        unresolved_suffix = f"，仍缺 {unresolved}" if unresolved else ""
        return f"数据补数完成 {action_count} 项，修复缺口 {resolved}/{target}{unresolved_suffix}，失败 {failed_count}{empty_suffix}"
    return f"数据补数完成 {action_count} 项，失败 {failed_count}{empty_suffix}"


def _manual_preset_matrix_task(payload: dict) -> ScheduledTask:
    task = ScheduledTask(task_id=MANUAL_PRESET_MATRIX_TASK_ID)
    task.name = str(payload.get("matrix_batch_name") or payload.get("run_name_prefix") or "手工预设矩阵")
    task.task_type = TASK_TYPE_PRESET_MATRIX_RUN
    task.cron_expression = "0 0 * * *"
    task.payload_json = json.dumps(payload, ensure_ascii=False)
    task.enabled = False
    task.is_running = True
    return task


def _default_matrix_payload(payload: dict) -> dict:
    result = {
        "matrix_batch_name": str(payload.get("matrix_batch_name") or "手工预设矩阵"),
        "run_name_prefix": str(payload.get("run_name_prefix") or payload.get("matrix_batch_name") or "手工预设矩阵"),
        "data_source": payload.get("data_source") or DEFAULT_RECOMMENDATION_DATA_SOURCE,
    }
    for key, value in payload.items():
        if key not in result:
            result[key] = value
    return result


def _run_data_sync(db, payload: dict, *, task_type: str = "data_sync") -> dict:
    """统一数据同步入口，按 sync_scope 执行可恢复的小阶段。"""
    from app.api.data_sources import invalidate_pool_status_cache
    from app.services.market_pool import refresh_quote_pool

    sync_scope = str(payload.get("sync_scope") or _default_sync_scope(task_type))
    result: dict[str, object] = {"sync_scope": sync_scope, "steps": []}
    summary_parts: list[str] = []

    # 1. 刷新行情池
    if sync_scope in ("quotes", "all"):
        quotes, meta = refresh_quote_pool(db)
        invalidate_pool_status_cache()
        logger.info("行情池刷新: %d 条行情, %s", len(quotes), meta)
        result["quotes"] = {"refreshed": len(quotes), "meta": meta}
        quote_trade_dates = [str(row.get("trade_date") or "") for row in quotes if row.get("trade_date")]
        if quote_trade_dates:
            result["trade_date"] = max(quote_trade_dates)
        summary_parts.append(f"行情池 {len(quotes)} 条")
        result["steps"].append({"name": "quotes", "status": "completed", "row_count": len(quotes)})

    # 2. 按交易日批量回填日K，避免逐股票脚本在全市场 force 模式下长时间超时。
    if sync_scope in ("daily_bars", "all"):
        daily_kbar_batch = _run_daily_kbar_batch(payload)
        invalidate_pool_status_cache()
        result["daily_kbar_batch"] = daily_kbar_batch
        summary_parts.append(_sync_step_summary("日K按日批量补数", daily_kbar_batch))
        result["steps"].append({"name": "daily_kbar_batch", "status": daily_kbar_batch.get("status")})

    # 3. 健康补数
    if sync_scope in ("health_remediation", "all") and not bool(payload.get("skip_health_remediation")):
        health_remediation = _run_data_health_remediation(payload)
        invalidate_pool_status_cache()
        result["health_remediation"] = health_remediation
        summary_parts.append(_sync_step_summary("健康补数完成", health_remediation))
        result["steps"].append({"name": "health_remediation", "status": health_remediation.get("status")})

    # 4. 回填辅助数据
    if sync_scope in ("auxiliary", "all"):
        auxiliary_payload = dict(payload)
        if result.get("trade_date") and not auxiliary_payload.get("trade_date"):
            auxiliary_payload["trade_date"] = result["trade_date"]
        auxiliary = _run_auxiliary_remediation(auxiliary_payload)
        invalidate_pool_status_cache()
        result["auxiliary"] = auxiliary
        summary_parts.append(_sync_step_summary("辅助数据补数完成", auxiliary))
        result["steps"].append({"name": "auxiliary", "status": auxiliary.get("status")})

    result["status"] = _task_result_status(result)
    result["summary_text"] = "；".join(summary_parts) if summary_parts else "自动任务执行完成"
    return result


def _task_result_status(result: dict | None) -> str:
    if not isinstance(result, dict):
        return "completed"
    status = str(result.get("status") or "").strip()
    if status == "completed_post_diagnosis_failed":
        return "partial_completed"
    if status in {"failed", "partial_failed", "partial_completed", "diagnosis_failed", "post_diagnosis_failed"}:
        return status
    step_statuses = [
        str(step.get("status") or "").strip()
        for step in (result.get("steps") or [])
        if isinstance(step, dict)
    ]
    if any(status == "failed" for status in step_statuses):
        return "failed"
    if any(status in {"partial_failed", "diagnosis_failed", "post_diagnosis_failed"} for status in step_statuses):
        return "partial_failed"
    if any(status in {"partial_completed", "completed_post_diagnosis_failed"} for status in step_statuses):
        return "partial_completed"
    if any(_action_summary_count(result.get(key), "empty_count") for key in ("daily_kbar_batch", "health_remediation", "auxiliary")):
        return "partial_completed"
    return "completed"


def _sync_step_summary(label: str, result: dict) -> str:
    summary = result.get("action_summary") or {}
    completed = int(summary.get("completed_count") or 0)
    failed = int(summary.get("failed_count") or 0)
    empty = int(summary.get("empty_count") or 0)
    skipped = int(summary.get("skipped_count") or 0)
    parts = [f"{label} {completed}"]
    if empty:
        parts.append(f"空结果待重试 {empty}")
    if skipped:
        parts.append(f"跳过 {skipped}")
    parts.append(f"失败 {failed}")
    return " / ".join(parts)


def _action_summary_count(result: object, key: str) -> int:
    if not isinstance(result, dict):
        return 0
    summary = result.get("action_summary")
    if not isinstance(summary, dict):
        return 0
    return int(summary.get(key) or 0)


def _run_daily_kbar_batch(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import (
        DEFAULT_DAILY_KBAR_CODE_LIMIT,
        remediate_data_health_gaps,
    )

    db = SessionLocal()
    try:
        return remediate_data_health_gaps(
            db,
            target_tables=["daily_kbar"],
            daily_kbar_code_limit=int(payload.get("health_daily_kbar_code_limit") or DEFAULT_DAILY_KBAR_CODE_LIMIT),
            post_diagnosis=bool(payload.get("health_post_diagnosis", True)),
        )
    finally:
        db.close()


def _run_data_health_remediation(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import (
        DEFAULT_DAILY_KBAR_CODE_LIMIT,
        remediate_data_health_gaps,
    )

    target_tables = payload.get("health_remediation_tables")
    if target_tables is None:
        target_tables = ["daily_kbar", "minute_kbar", "index_daily", "limit_up_board", "capital_flow"]
    db = SessionLocal()
    try:
        return remediate_data_health_gaps(
            db,
            target_tables=list(target_tables),
            capital_flow_code_limit=int(payload.get("health_capital_flow_code_limit") or payload.get("flow_top_n") or 300),
            daily_kbar_code_limit=int(payload.get("health_daily_kbar_code_limit") or DEFAULT_DAILY_KBAR_CODE_LIMIT),
            minute_kbar_code_limit=int(payload.get("health_minute_kbar_code_limit") or payload.get("tushare_aux_top_n") or 300),
            post_diagnosis=bool(payload.get("health_post_diagnosis", True)),
        )
    finally:
        db.close()


def _run_auxiliary_remediation(payload: dict) -> dict:
    from app.services.data_gap_remediation_service import remediate_data_health_gaps

    target_tables = payload.get("auxiliary_remediation_tables")
    if target_tables is None:
        target_tables = ["limit_up_board", "concept_sector", "dragon_tiger", "news", "capital_flow"]
    db = SessionLocal()
    try:
        result = remediate_data_health_gaps(
            db,
            target_tables=list(target_tables),
            capital_flow_code_limit=int(payload.get("auxiliary_capital_flow_code_limit") or payload.get("flow_top_n") or 300),
            post_diagnosis=bool(payload.get("auxiliary_post_diagnosis", True)),
        )

        # 新增批量接口同步（全市场，1次API调用/接口）
        trade_date = payload.get("trade_date") or result.get("trade_date")
        if trade_date:
            from app.services.market_pool import (
                refresh_margin_detail,
                refresh_block_trade,
                refresh_hsgt_flow,
                refresh_hsgt_top10,
            )
            batch_results = {}
            for name, func in [
                ("margin_detail", lambda: refresh_margin_detail(db, trade_date=trade_date)),
                ("block_trade", lambda: refresh_block_trade(db, trade_date=trade_date)),
                ("hsgt_flow", lambda: refresh_hsgt_flow(db, trade_date=trade_date)),
                ("hsgt_top10", lambda: refresh_hsgt_top10(db, trade_date=trade_date)),
            ]:
                try:
                    count_or_data = func()
                    batch_results[name] = {
                        "status": "success",
                        "count": count_or_data if isinstance(count_or_data, int) else (1 if count_or_data else 0),
                    }
                except Exception as exc:
                    db.rollback()
                    batch_results[name] = {"status": "failed", "error": str(exc)[:200]}
            result["batch_sync"] = batch_results

        return result
    finally:
        db.close()


def _run_backfill_script(payload: dict) -> dict:
    """执行日K线回填脚本。"""
    import subprocess
    import sys
    from pathlib import Path

    from app.core.config import get_settings

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    script = scripts_dir / "backfill_daily_bars.py"
    if not script.exists():
        raise RuntimeError(f"回填脚本不存在: {script}")

    args = [sys.executable, str(script)]
    if payload.get("days"):
        args.extend(["--days", str(payload["days"])])
    if payload.get("min_bars"):
        args.extend(["--min-bars", str(payload["min_bars"])])
    if payload.get("limit"):
        args.extend(["--limit", str(payload["limit"])])
    if payload.get("offset"):
        args.extend(["--offset", str(payload["offset"])])
    if payload.get("commit_interval"):
        args.extend(["--commit-interval", str(payload["commit_interval"])])
    if payload.get("rate_limit") is not None:
        args.extend(["--rate-limit", str(payload["rate_limit"])])
    if payload.get("force"):
        args.append("--force")
    if payload.get("reset_progress"):
        args.append("--reset-progress")

    timeout = get_settings().script_timeout_seconds or None
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"日K回填脚本失败: {result.stderr[:500]}")
    logger.info("backfill_daily_bars.py 完成:\n%s", result.stdout[-500:])
    return _parse_backfill_stdout(result.stdout)


def _run_auxiliary_script(payload: dict) -> dict:
    """执行辅助数据回填脚本。"""
    import subprocess
    import sys
    from pathlib import Path

    from app.core.config import get_settings

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    script = scripts_dir / "backfill_auxiliary_data.py"
    if not script.exists():
        raise RuntimeError(f"回填脚本不存在: {script}")

    args = [sys.executable, str(script)]
    if payload.get("skip_concepts"):
        args.append("--skip-concepts")
    if payload.get("skip_dragon_tiger"):
        args.append("--skip-dragon-tiger")
    if payload.get("skip_capital_flows"):
        args.append("--skip-capital-flows")
    if payload.get("skip_news"):
        args.append("--skip-news")
    if payload.get("skip_tushare_aux"):
        args.append("--skip-tushare-aux")
    if payload.get("flow_top_n"):
        args.extend(["--flow-top-n", str(payload["flow_top_n"])])
    if payload.get("tushare_aux_top_n"):
        args.extend(["--tushare-aux-top-n", str(payload["tushare_aux_top_n"])])
    if payload.get("trade_date"):
        args.extend(["--trade-date", str(payload["trade_date"])])

    timeout = get_settings().script_timeout_seconds or None
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"辅助数据回填脚本失败: {result.stderr[:500]}")
    logger.info("backfill_auxiliary_data.py 完成:\n%s", result.stdout[-500:])
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {
        "stdout_tail": "\n".join(lines[-10:]),
        "summary_text": lines[-1] if lines else "辅助数据回填完成",
    }


async def _run_recommendation(db, payload: dict) -> dict:
    """执行推荐流水线。"""
    from app.services.workbench_service import run_workbench

    run_payload = {
        "preset_id": payload.get("preset_id"),
        "run_name": payload.get("run_name", f"定时推荐 {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d')}"),
    }
    for key in (
        "run_mode",
        "data_source",
        "risk_template",
        "score_line",
        "output_count",
        "selected_skill_ids",
        "skip_aux_refresh",
    ):
        if key in payload:
            run_payload[key] = payload.get(key)
    result = await run_workbench(db, run_payload)
    logger.info("定时推荐完成: %d 条", result.get("recommended_count", 0))
    run = result.get("run") or {}
    related_run_id = str(run.get("run_id") or "")
    recommended_count = int(run.get("recommended_count") or result.get("recommended_count") or 0)
    return {
        "related_run_id": related_run_id,
        "recommended_count": recommended_count,
        "summary_text": (
            f"关联 Run {related_run_id}，推荐 {recommended_count} 条"
            if related_run_id
            else f"自动推荐 {recommended_count} 条"
        ),
    }


def _update_matrix_progress(db, task_run_id: str, summary: str) -> None:
    """更新矩阵任务的阶段进度摘要，供前端轮询显示。

    单独 commit 避免长事务占着连接；失败只记日志，不中断矩阵运行。
    """
    try:
        run = db.get(ScheduledTaskRun, task_run_id)
        if run is None or run.status != "running":
            return
        run.summary = summary[:500]
        run.finished_at = run.finished_at  # 触发 ORM 脏标记
        db.commit()
    except Exception:
        logger.exception("更新矩阵进度失败: %s", task_run_id)
        try:
            db.rollback()
        except Exception:
            pass


async def _run_preset_matrix(db, payload: dict, task_run_id: str | None = None) -> dict:
    """串行执行全部启用预设，并给每个推荐 Run 写入同一个矩阵批次标签。

    若提供 task_run_id，会在每个预设开始时更新 ScheduledTaskRun.summary，
    供前端矩阵页轮询显示阶段进度（已完成 N/M 预设: 当前预设名）。
    """
    from app.services.workbench_service import run_workbench

    presets = db.scalars(
        select(Preset)
        .where(Preset.status == "active")
        .order_by(Preset.created_at.asc(), Preset.name.asc())
    ).all()
    matrix_batch_id = str(
        payload.get("matrix_batch_id")
        or f"matrix_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')}"
    )
    matrix_batch_name = str(payload.get("matrix_batch_name") or "盘后预设矩阵")
    run_name_prefix = str(payload.get("run_name_prefix") or matrix_batch_name)
    data_source = payload.get("data_source") or DEFAULT_RECOMMENDATION_DATA_SOURCE
    stop_on_error = bool(payload.get("stop_on_error", False))

    total = len(presets)
    rows: list[dict[str, object]] = []
    recommended_total = 0
    for index, preset in enumerate(presets, start=1):
        if task_run_id:
            _update_matrix_progress(
                db,
                task_run_id,
                summary=f"执行预设 {index}/{total}: {preset.name}（已完成 {index - 1}/{total}）",
            )
        run_payload = {
            "preset_id": preset.preset_id,
            "run_name": f"{run_name_prefix} · {preset.name}",
            "data_source": data_source,
            "matrix_batch_id": matrix_batch_id,
            "matrix_batch_name": matrix_batch_name,
            "matrix_run": True,
        }
        for key in (
            "run_mode",
            "risk_template",
            "score_line",
            "output_count",
            "selected_skill_ids",
            "skip_aux_refresh",
            "disable_agent_review",
            "agent_review_limit",
            "agent_review_concurrency",
        ):
            if key in payload:
                run_payload[key] = payload.get(key)
        try:
            result = await run_workbench(db, run_payload)
            run = result.get("run") or {}
            run_summary = run.get("result_summary") or {}
            recommended_count = int(run.get("recommended_count") or 0)
            recommended_total += recommended_count
            _slm = run_summary.get("score_line_meta") or {}
            _pipeline = run_summary.get("pipeline") or {}
            _scored = (
                _pipeline.get("scored_count")
                or _pipeline.get("skill_pass_count")
                or _pipeline.get("skill_gate_candidate_count")
                or _pipeline.get("actual_scan_count")
            )
            rows.append(
                {
                    "preset_id": preset.preset_id,
                    "preset_name": preset.name,
                    "run_id": str(run.get("run_id") or ""),
                    "status": "completed",
                    "recommended_count": recommended_count,
                    "universe_count": int(run.get("universe_count") or 0),
                    "scored_count": int(_scored) if _scored is not None else None,
                    "effective_score_line": _slm.get("effective_score_line"),
                    "market_base": _slm.get("market_base"),
                    "market_sentiment": _slm.get("market_sentiment"),
                    "top_candidate_score": run_summary.get("top_candidate_score"),
                    "climax_watchlist": run_summary.get("climax_watchlist") or [],
                    "result_summary": run_summary,
                }
            )
        except Exception as exc:
            logger.exception("预设矩阵批次 %s 运行失败: %s", matrix_batch_id, preset.preset_id)
            db.rollback()
            rows.append(
                {
                    "preset_id": preset.preset_id,
                    "preset_name": preset.name,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            if stop_on_error:
                break

    completed_count = sum(1 for row in rows if row.get("status") == "completed")
    failed_count = sum(1 for row in rows if row.get("status") == "failed")
    # 聚合行情状态与观察池，供前端矩阵报告展示「正确静默」而非误判为 bug
    _regime_rows = [r for r in rows if r.get("market_base")]
    market_base = _regime_rows[0]["market_base"] if _regime_rows else None
    market_sentiment = _regime_rows[0]["market_sentiment"] if _regime_rows else None
    market_regime = f"{market_base}·{market_sentiment}" if market_base and market_sentiment else None
    aggregated_watchlist: list[dict[str, object]] = []
    _seen_codes: set[str] = set()
    for r in rows:
        for c in (r.get("climax_watchlist") or []):
            code = c.get("code")
            if code and code not in _seen_codes:
                _seen_codes.add(code)
                aggregated_watchlist.append(c)
    aggregated_watchlist.sort(key=lambda x: float(x.get("final_score") or 0), reverse=True)
    top_candidate_score = max(
        (float(c.get("final_score") or 0) for c in aggregated_watchlist), default=0.0
    )
    if not top_candidate_score:
        top_candidate_score = max(
            (float(r.get("top_candidate_score") or 0) for r in rows if r.get("top_candidate_score")),
            default=0.0,
        )
    return {
        "matrix_batch_id": matrix_batch_id,
        "matrix_batch_name": matrix_batch_name,
        "preset_count": len(presets),
        "run_count": len(rows),
        "completed_count": completed_count,
        "failed_count": failed_count,
        "recommended_count": recommended_total,
        "runs": rows,
        "market_regime": market_regime,
        "market_base": market_base,
        "market_sentiment": market_sentiment,
        "top_candidate_score": round(top_candidate_score, 2) if top_candidate_score else None,
        "aggregated_watchlist": aggregated_watchlist,
        "all_zero": recommended_total == 0,
        "summary_text": (
            f"矩阵批次 {completed_count}/{len(presets)} 个预设完成，"
            f"失败 {failed_count} 个，推荐合计 {recommended_total} 条"
        ),
    }


def run_task_now(task_id: str) -> None:
    """立即执行一个调度任务，用于页面上的手动触发。"""
    _execute_task(task_id, force=True, trigger_source="manual")


def reload_tasks() -> int:
    """重新加载全部调度任务（用于热更新 Cron 配置后）。"""
    global _scheduler
    if _scheduler is None:
        return 0
    for job in _scheduler.get_jobs():
        _scheduler.remove_job(job.id)
    _load_and_schedule()
    count = len(_scheduler.get_jobs())
    logger.info("调度任务已重新加载: %d 个", count)
    return count


def _default_sync_scope(task_type: str) -> str:
    if task_type == "data_backfill":
        return "daily_bars"
    if task_type == "pool_refresh":
        return "quotes"
    return "all"


def _parse_backfill_stdout(stdout: str) -> dict:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    parsed_scope = _parse_stats_line(next((line for line in lines if line.startswith("scope=")), ""))
    parsed_result = _parse_stats_line(next((line for line in reversed(lines) if line.startswith("processed=")), ""))
    processed = _to_int(parsed_result.get("processed"))
    persisted = _to_int(parsed_result.get("persisted"))
    short_history = _to_int(parsed_result.get("short_history"))
    failed = _to_int(parsed_result.get("failed"))
    summary_text = (
        f"处理 {processed}，入库 {persisted}，短历史 {short_history}，失败 {failed}"
        if processed is not None
        else (lines[-1] if lines else "日K回填完成")
    )
    return {
        "scope": parsed_scope.get("scope"),
        "universe": _to_int(parsed_scope.get("universe")),
        "ready_or_progress": _to_int(parsed_scope.get("ready_or_progress")),
        "pending": _to_int(parsed_scope.get("pending")),
        "processed": processed,
        "persisted": persisted,
        "short_history": short_history,
        "failed": failed,
        "elapsed_seconds": _to_float(parsed_result.get("elapsed")),
        "stdout_tail": "\n".join(lines[-10:]),
        "summary_text": summary_text,
    }


def _parse_stats_line(line: str) -> dict[str, str]:
    if not line:
        return {}
    parts: dict[str, str] = {}
    for token in line.replace("|", " ").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parts[key.strip()] = value.strip()
    return parts


def _to_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value.rstrip("s"))
    except ValueError:
        return None

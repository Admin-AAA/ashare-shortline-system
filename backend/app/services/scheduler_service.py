"""APScheduler 调度执行器 — 按 Cron 表达式执行 ScheduledTask。

在 FastAPI 启动时创建 BackgroundScheduler，从数据库加载已启用的任务。
支持任务类型:
  - data_sync: 同步行情数据（刷新报价池 + 按日批量日K + 健康补数 + 辅助数据）
  - recommendation_run: 执行推荐流水线
  - preset_matrix_run: 盘后批量执行全部启用预设，用于预设矩阵对比
  - strategy_exposure_run: 盘前对近30天样本不足的策略单独补跑，产出仅供胜率统计（experiment 模式，不进记录中心）
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import (
    Preset,
    RecommendationRun,
    ScheduledTask,
    ScheduledTaskRun,
)
from app.services.cost_service import finish_task_run, serialize_scheduled_task_run, start_task_run
from app.engine.trading_calendar import latest_allowed_bar_date as _latest_allowed_bar_date
from app.services.registry.data_source import (
    DATA_SOURCE_LOCAL_POOL_PREFERRED,
    DEFAULT_RECOMMENDATION_DATA_SOURCE,
)

logger = logging.getLogger("scheduler")

_scheduler: BackgroundScheduler | None = None
_running_lock = threading.Lock()
_running_tasks: set[str] = set()
TASK_TYPE_PRESET_MATRIX_RUN = "preset_matrix_run"
TASK_TYPE_FORMAL_SAFETY_NET = "formal_safety_net"
TASK_TYPE_STRATEGY_EXPOSURE_RUN = "strategy_exposure_run"
TASK_TYPE_OUTCOME_LABEL = "outcome_label"
MANUAL_PRESET_MATRIX_TASK_ID = "manual_preset_matrix_run"
MANUAL_DATA_HEALTH_REMEDIATION_TASK_ID = "manual_data_health_remediation"
SAFETY_NET_TASK_ID = "formal_run_safety_net"
SAFETY_NET_MAX_ATTEMPTS = 3
POSTCLOSE_PIPELINE_TASK_ID = "post_sync_formal_recommendation"
POSTCLOSE_PIPELINE_CRON = "35 17 * * 1-5"
LEGACY_POSTCLOSE_TASK_IDS = (
    "postclose_quote_refresh",
    "postclose_daily_kbar_batch",
    "postclose_data_health_remediation",
    "postclose_auxiliary_data_sync",
    "postclose_outcome_label",
)
# 策略曝光补跑调度任务（E 项样本可信前置）：盘前对近30天样本不足的策略单独补跑。
# 任务 ID 固定，start_scheduler 时幂等注册（已存在则不覆盖用户手动调整的 cron/payload）。
EXPOSURE_TASK_ID = "strategy_exposure_run"
EXPOSURE_CRON = "3 8 * * 1-5"  # 周一到周五 08:03（盘前，避免与盘后结算/回填争抢资源）
# 曝光补跑默认阈值：近30天已兑现样本 < 此值的 enabled 策略才补跑。
# 取 20 与北极星 E 项样本置信度下限对齐——样本 <20 时胜率统计不可信（置信区间过宽），
# 补跑持续喂到此值才让每个策略胜率可被信任，进而能按胜率选策略。
DEFAULT_EXPOSURE_MIN_SAMPLE = 20


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
    _ensure_postclose_pipeline_task()
    _ensure_strategy_exposure_task()
    _load_and_schedule()
    _scheduler.start()
    _register_periodic_jobs()
    logger.info("调度器已启动，已加载 %d 个任务", len(_scheduler.get_jobs()))
    return _scheduler


def _postclose_pipeline_payload(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(existing or {})
    payload.update(
        {
            "pre_run_full_sync": True,
            "sync_scope": "all",
            "preset_id": payload.get("preset_id") or "preset_formal_opening",
            "run_name": payload.get("run_name") or "盘后全量同步后正式推荐",
            "run_mode": "formal",
            "data_source": DATA_SOURCE_LOCAL_POOL_PREFERRED,
            "risk_template": payload.get("risk_template") or "balanced",
            "score_line": float(payload.get("score_line") or 72),
            "output_count": int(payload.get("output_count") or 6),
            "skip_aux_refresh": True,
            "health_remediation_tables": [
                "daily_kbar",
                "minute_kbar",
                "index_daily",
                "limit_up_board",
                "capital_flow",
            ],
            "auxiliary_remediation_tables": [
                "limit_up_board",
                "concept_sector",
                "dragon_tiger",
                "news",
                "capital_flow",
            ],
            "health_post_diagnosis": True,
            "auxiliary_post_diagnosis": False,
            "horizon_days": int(payload.get("horizon_days") or 5),
            "recommendation_wait_timeout_seconds": int(
                payload.get("recommendation_wait_timeout_seconds") or 3600
            ),
        }
    )
    return payload


def _configure_postclose_pipeline_tasks(db) -> None:
    """幂等收敛旧盘后 cron，避免同步阶段与推荐再次并发。"""
    task = db.get(ScheduledTask, POSTCLOSE_PIPELINE_TASK_ID)
    existing_payload: dict[str, Any] = {}
    if task is None:
        task = ScheduledTask(task_id=POSTCLOSE_PIPELINE_TASK_ID)
        db.add(task)
    else:
        try:
            existing_payload = json.loads(task.payload_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            existing_payload = {}
    task.name = "盘后全量同步、健康检查与正式推荐"
    task.task_type = "recommendation_run"
    task.cron_expression = POSTCLOSE_PIPELINE_CRON
    task.payload_json = json.dumps(_postclose_pipeline_payload(existing_payload), ensure_ascii=False)
    task.enabled = True

    for task_id in LEGACY_POSTCLOSE_TASK_IDS:
        legacy = db.get(ScheduledTask, task_id)
        if legacy is not None:
            legacy.enabled = False
    db.commit()


def _ensure_postclose_pipeline_task() -> None:
    try:
        db = SessionLocal()
        try:
            _configure_postclose_pipeline_tasks(db)
        finally:
            db.close()
    except Exception:
        logger.exception("注册盘后全量同步流水线失败")


def _ensure_strategy_exposure_task() -> None:
    """确保策略曝光补跑调度任务存在（E 项样本可信前置）。

    盘前对近30天样本 < DEFAULT_EXPOSURE_MIN_SAMPLE(20) 的 enabled 策略单独补跑，
    使其胜率统计可信（样本 ≥20 才能按胜率选策略、低胜率策略才敢标记）。

    ⚠️ 历史根因：本函数曾缺失（仅 manual 触发路径 + _run_strategy_exposure 实现存在，
    但 start_scheduler 从不注册定时任务），导致 exposure cron 从未被排程——4 个低频
    策略（chip/high-range/small-cap/trend-pullback）长期卡在 n<20，胜率统计不可信，
    E 项持续误触发。此处使用幂等注册修复该盲区。

    若任务已存在则不覆盖（保留用户手动调整的 cron/payload）。
    """
    try:
        db = SessionLocal()
        try:
            existing = db.get(ScheduledTask, EXPOSURE_TASK_ID)
            if existing:
                return
            task = ScheduledTask(
                task_id=EXPOSURE_TASK_ID,
                name="策略曝光补跑（盘前样本补齐）",
                task_type=TASK_TYPE_STRATEGY_EXPOSURE_RUN,
                cron_expression=EXPOSURE_CRON,
                payload_json=json.dumps(
                    {
                        "window_days": 30,
                        "min_sample_threshold": DEFAULT_EXPOSURE_MIN_SAMPLE,
                    }
                ),
                enabled=True,
            )
            db.add(task)
            db.commit()
            logger.info("已注册策略曝光补跑调度任务: %s (cron=%s)", EXPOSURE_TASK_ID, EXPOSURE_CRON)
        finally:
            db.close()
    except Exception:
        logger.exception("注册策略曝光补跑任务失败")


def _register_periodic_jobs() -> None:
    """注册与业务调度任务无关的周期巡检任务（如失联运行回收）。"""
    if _scheduler is None:
        return
    _scheduler.add_job(
        _periodic_recover_stale_runs,
        trigger="interval",
        seconds=300,
        id="periodic_stale_run_recovery",
        name="失联运行周期回收",
        replace_existing=True,
        next_run_time=datetime.now() + timedelta(seconds=60),
    )
    logger.info("已注册失联运行周期回收任务（间隔 300s）")


def _periodic_recover_stale_runs() -> None:
    """周期巡检：回收心跳超时的失联运行，避免孤儿 run 长期滞留 running 状态。

    与启动期 recover_interrupted_runs 互补：后者仅在进程启动时执行一次。
    若两次重启之间无人访问 runs 列表接口，失联运行不会被回收（列表接口的
    _auto_recover_stale_runs 依赖客户端请求触发）。此周期任务保证即使前端
    未打开，孤儿运行也会在心跳超时后（默认 1200s）被标记为 interrupted。
    """
    try:
        from app.services.workbench_service import recover_interrupted_runs

        db = SessionLocal()
        try:
            recovered = recover_interrupted_runs(db)
            if recovered:
                logger.warning("周期巡检回收 %d 个失联运行", recovered)
        finally:
            db.close()
    except Exception:
        logger.exception("周期巡检回收失联运行失败")


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
            result = asyncio.run(
                _run_postclose_pipeline(db, payload)
                if payload.get("pre_run_full_sync")
                else _run_recommendation(db, payload)
            )
        elif task_type == TASK_TYPE_FORMAL_SAFETY_NET:
            result = asyncio.run(_safety_net_run(db, payload))
        elif task_type == TASK_TYPE_PRESET_MATRIX_RUN:
            result = asyncio.run(_run_preset_matrix(db, payload, task_run_id=task_run_id))
        elif task_type == TASK_TYPE_STRATEGY_EXPOSURE_RUN:
            result = asyncio.run(_run_strategy_exposure(db, payload, task_run_id=task_run_id))
        elif task_type == TASK_TYPE_OUTCOME_LABEL:
            result = _run_outcome_label(db, payload)
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


MANUAL_STRATEGY_EXPOSURE_TASK_ID = "manual_strategy_exposure_run"


def start_strategy_exposure_now(payload: dict | None = None) -> dict:
    """异步启动一次低曝光策略补跑，立即返回任务运行状态。

    供手动触发/测试用；定时路径由 scheduled_tasks 表的 strategy_exposure_run 任务按 cron 触发。
    """
    if not _try_mark_running_memory(MANUAL_STRATEGY_EXPOSURE_TASK_ID):
        running = get_latest_strategy_exposure_task_run()
        if running and running.get("status") == "running":
            return {"started": False, "task_run": running}
        raise RuntimeError("策略曝光补跑正在执行中，请等待当前批次完成")

    run_payload = dict(payload or {})
    db = SessionLocal()
    task_run_id: str | None = None
    try:
        existing_running = _latest_running_strategy_exposure_task_run(db)
        if existing_running is not None:
            _mark_done_memory(MANUAL_STRATEGY_EXPOSURE_TASK_ID)
            return {"started": False, "task_run": serialize_scheduled_task_run(existing_running)}

        task = ScheduledTask(task_id=MANUAL_STRATEGY_EXPOSURE_TASK_ID)
        task.name = "手工策略曝光补跑"
        task.task_type = TASK_TYPE_STRATEGY_EXPOSURE_RUN
        task.cron_expression = "0 0 * * *"
        task.payload_json = json.dumps(run_payload, ensure_ascii=False)
        task.enabled = False
        task.is_running = True
        task_run = start_task_run(db, task, trigger_source="manual")
        task_run_id = task_run.task_run_id
    except Exception:
        _mark_done_memory(MANUAL_STRATEGY_EXPOSURE_TASK_ID)
        raise
    finally:
        db.close()

    thread = threading.Thread(
        target=_execute_manual_strategy_exposure,
        args=[task_run_id, run_payload],
        name=f"strategy-exposure-{task_run_id}",
        daemon=True,
    )
    thread.start()
    return {"started": True, "task_run": get_latest_strategy_exposure_task_run()}


def get_latest_strategy_exposure_task_run() -> dict | None:
    db = SessionLocal()
    try:
        run = db.scalars(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.task_type == TASK_TYPE_STRATEGY_EXPOSURE_RUN)
            .order_by(ScheduledTaskRun.started_at.desc())
            .limit(1)
        ).first()
        return serialize_scheduled_task_run(run) if run is not None else None
    finally:
        db.close()


def _latest_running_strategy_exposure_task_run(db) -> ScheduledTaskRun | None:
    return db.scalars(
        select(ScheduledTaskRun)
        .where(
            ScheduledTaskRun.task_type == TASK_TYPE_STRATEGY_EXPOSURE_RUN,
            ScheduledTaskRun.status == "running",
            ScheduledTaskRun.finished_at.is_(None),
        )
        .order_by(ScheduledTaskRun.started_at.desc())
        .limit(1)
    ).first()


def _execute_manual_strategy_exposure(task_run_id: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        result = asyncio.run(_run_strategy_exposure(db, payload, task_run_id=task_run_id))
        finish_task_run(
            db,
            task_run_id,
            status=_task_result_status(result),
            summary=str(result.get("summary_text") or ""),
            result=result,
        )
    except Exception as exc:
        logger.exception("手工策略曝光补跑失败: %s", task_run_id)
        db.rollback()
        try:
            finish_task_run(db, task_run_id, status="failed", error=str(exc), summary=str(exc))
        except Exception:
            pass
    finally:
        db.close()
        _mark_done_memory(MANUAL_STRATEGY_EXPOSURE_TASK_ID)


def _run_outcome_label(db, payload: dict) -> dict:
    """结算 pending 推荐 outcome（D 项前瞻验证前置基础设施）。

    收盘后日K同步完成（postclose_daily_kbar_batch 17:40 + auxiliary 18:05）后触发，
    调 label_pending_outcomes 用 T+1 日K结算昨日推荐的执行收益/胜负标签。
    无此前置：自检 D 项前瞻窗口永远报"等结算"，上几轮毒区改动无法前瞻确认。
    """
    from app.services.outcome_service import label_pending_outcomes

    horizon_days = int(payload.get("horizon_days") or 5)
    result = label_pending_outcomes(db, horizon_days=horizon_days)
    labeled = int(result.get("labeled") or 0)
    logger.info("outcome 结算完成: %d 笔（horizon=%d）", labeled, horizon_days)
    return {
        "summary_text": f"结算 {labeled} 笔",
        "labeled": labeled,
        "horizon_days": horizon_days,
    }


def _run_data_sync(db, payload: dict, *, task_type: str = "data_sync") -> dict:
    """统一数据同步入口，按 sync_scope 执行可恢复的小阶段。"""
    from app.api.data_sources import invalidate_pool_status_cache
    from app.services.data_health_service import clear_data_health_cache
    from app.services.market_pool import refresh_quote_pool

    sync_scope = str(payload.get("sync_scope") or _default_sync_scope(task_type))
    result: dict[str, object] = {"sync_scope": sync_scope, "steps": []}
    summary_parts: list[str] = []

    # 1. 刷新行情池
    if sync_scope in ("quotes", "all"):
        quotes, meta = refresh_quote_pool(db)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        logger.info("行情池刷新: %d 条行情, %s", len(quotes), meta)
        result["quotes"] = {"refreshed": len(quotes), "meta": meta}
        quote_trade_dates = [str(row.get("trade_date") or "") for row in quotes if row.get("trade_date")]
        if quote_trade_dates:
            result["trade_date"] = max(quote_trade_dates)
        summary_parts.append(f"行情池 {len(quotes)} 条")
        result["steps"].append({"name": "quotes", "status": "completed", "row_count": len(quotes)})

    # 2. 按交易日批量回填日K，避免逐股票脚本在全市场 force 模式下长时间超时。
    if sync_scope in ("daily_bars", "all"):
        daily_payload = dict(payload)
        if sync_scope == "all":
            daily_payload["health_post_diagnosis"] = False
        daily_kbar_batch = _run_daily_kbar_batch(daily_payload)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        result["daily_kbar_batch"] = daily_kbar_batch
        summary_parts.append(_sync_step_summary("日K按日批量补数", daily_kbar_batch))
        result["steps"].append({"name": "daily_kbar_batch", "status": daily_kbar_batch.get("status")})

    # 3. 回填辅助数据。all 模式必须先完成辅助数据，再做最终健康诊断。
    if sync_scope in ("auxiliary", "all"):
        auxiliary_payload = dict(payload)
        if result.get("trade_date") and not auxiliary_payload.get("trade_date"):
            auxiliary_payload["trade_date"] = result["trade_date"]
        auxiliary = _run_auxiliary_remediation(auxiliary_payload)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        result["auxiliary"] = auxiliary
        summary_parts.append(_sync_step_summary("辅助数据补数完成", auxiliary))
        result["steps"].append({"name": "auxiliary", "status": auxiliary.get("status")})

    # 4. 最终健康补数与诊断，确保后续推荐读取的是所有同步阶段之后的快照。
    if sync_scope in ("health_remediation", "all") and not bool(payload.get("skip_health_remediation")):
        health_remediation = _run_data_health_remediation(payload)
        invalidate_pool_status_cache()
        clear_data_health_cache()
        result["health_remediation"] = health_remediation
        summary_parts.append(_sync_step_summary("健康补数完成", health_remediation))
        result["steps"].append({"name": "health_remediation", "status": health_remediation.get("status")})

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
        result = remediate_data_health_gaps(
            db,
            target_tables=["daily_kbar"],
            daily_kbar_code_limit=int(payload.get("health_daily_kbar_code_limit") or DEFAULT_DAILY_KBAR_CODE_LIMIT),
            post_diagnosis=bool(payload.get("health_post_diagnosis", True)),
        )
        # K 线同步后修本地可算指标（ma5/ma10/ma20/vol_ratio/bias/breakout/max_dd）。
        # remediate_data_health_gaps 只检测行数缺失，不检测 ma5=0——批量同步写入 0 指标时
        # 不会被自动修复，导致 _build_stock_from_bars fallback 重算（策略能跑但数据质量差）。
        # try/except 保护：指标补齐是 best-effort 增强，不能因它失败而让 K 线同步整体失败。
        try:
            indicator_fix = _backfill_missing_local_indicators(db, days=7)
            if indicator_fix.get("fixed_dates"):
                result.setdefault("indicator_backfill", indicator_fix)
                logger.info("daily_kbar_batch 后补本地指标: %s", indicator_fix)
        except Exception as exc:
            logger.warning("daily_kbar_batch 后补本地指标失败（不影响 K 线同步）: %s", exc)
        return result
    finally:
        db.close()


def _backfill_missing_local_indicators(db, days: int = 7) -> dict:
    """检测最近 N 天 ma5=0 占比 >50% 的交易日，调 backfill_indicators.backfill_date 修复。

    与 scripts/backfill_indicators.py 同逻辑，供 scheduler 在 K 线同步后自动调用，
    避免 ma5=0 全市场缺失（07-07/07-03 曾发生 5505 只全 0）。
    """
    import sys as _sys
    from pathlib import Path as _Path
    scripts_dir = _Path(__file__).resolve().parent.parent.parent / "scripts"
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    try:
        from backfill_indicators import _dates_needing_indicators, backfill_date
    except ImportError:
        return {"fixed_dates": [], "error": "backfill_indicators 不可导入"}

    dates = _dates_needing_indicators(db, days)
    fixed: list[dict] = []
    for trade_date in dates:
        stats = backfill_date(db, trade_date)
        fixed.append({"trade_date": trade_date, **stats})
    return {"fixed_dates": fixed}


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
    from app.services.auxiliary.production import AUXILIARY_DATASET_INVENTORY

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

        # 独立辅助任务也必须拥有明确交易日；此前缺失该字段导致整个批量分支静默跳过。
        trade_date = str(payload.get("trade_date") or result.get("trade_date") or _latest_allowed_bar_date())
        from app.services.auxiliary.application import run_auxiliary_application

        datasets = tuple(
            payload.get("auxiliary_batch_datasets")
            or [entry.key for entry in AUXILIARY_DATASET_INVENTORY]
        )
        auxiliary = run_auxiliary_application(
            db,
            instruments=(),
            trade_date=trade_date,
            refresh=True,
            datasets=datasets,
        )
        result["trade_date"] = trade_date
        result["batch_sync"] = {
            item.dataset_key: {
                "status": "failed" if item.errors else "success",
                "count": item.coverage.record_count,
                "errors": [error.code.value for error in item.errors],
            }
            for item in auxiliary.orchestrated.results
        }
        # S4 application boundary 明确把事务所有权交给调用方；调度入口必须提交，
        # 否则 Session close 会把已经拉取和 upsert 的批量数据全部回滚。
        db.commit()
        if any(item["status"] == "failed" for item in result["batch_sync"].values()):
            result["status"] = "partial_failed"

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


def _diagnose_postclose_readiness(db, trade_date: str) -> dict[str, Any]:
    from app.services.data_health_service import (
        diagnose_all,
        recommendation_readiness_from_diagnosis,
    )

    diagnosis = diagnose_all(db, force_refresh=True)
    readiness = recommendation_readiness_from_diagnosis(
        diagnosis,
        expected_trade_date=trade_date,
    )
    return {
        **readiness,
        "score": diagnosis.get("score"),
        "issues": diagnosis.get("issues") or [],
    }


async def _wait_for_recommendation_run(
    db,
    run_id: str,
    *,
    timeout_seconds: int = 3600,
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """等待独立 worker 的最终状态，让调度任务不再把“已入队”误报为完成。"""
    deadline = time.monotonic() + max(1, timeout_seconds)
    terminal = {"completed", "failed", "interrupted", "cancelled"}
    while time.monotonic() < deadline:
        db.expire_all()
        run = db.get(RecommendationRun, run_id)
        if run is None:
            raise RuntimeError(f"推荐 Run 不存在: {run_id}")
        if run.status in terminal:
            return {
                "run_id": run.run_id,
                "status": run.status,
                "recommended_count": int(run.recommended_count or 0),
                "error": run.error_message,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }
        await asyncio.sleep(max(0.1, poll_seconds))
    raise TimeoutError(f"推荐 Run {run_id} 在 {timeout_seconds} 秒内未结束")


async def _run_postclose_pipeline(db, payload: dict) -> dict[str, Any]:
    """单一盘后流水线：全量同步 → 健康闸门 → outcome → 正式推荐。"""
    trade_date = _latest_allowed_bar_date()
    sync_payload = dict(payload)
    sync_payload.update({"sync_scope": "all", "trade_date": trade_date})
    sync_result = _run_data_sync(db, sync_payload)

    readiness = _diagnose_postclose_readiness(db, trade_date)
    if not readiness.get("can_run_local_recommendation"):
        blockers = "；".join(readiness.get("blockers") or ["未知数据健康问题"])
        return {
            "status": "failed",
            "trade_date": trade_date,
            "sync": sync_result,
            "readiness": readiness,
            "summary_text": f"盘后同步完成，但健康闸门阻止推荐：{blockers}",
        }

    outcome = _run_outcome_label(
        db,
        {"horizon_days": int(payload.get("horizon_days") or 5)},
    )
    recommendation = await _run_recommendation(db, payload)
    run_id = str(recommendation.get("related_run_id") or "")
    if not run_id:
        raise RuntimeError("盘后正式推荐未返回关联 Run ID")
    final_run = await _wait_for_recommendation_run(
        db,
        run_id,
        timeout_seconds=int(payload.get("recommendation_wait_timeout_seconds") or 3600),
    )
    final_status = str(final_run.get("status") or "failed")
    if final_status != "completed":
        return {
            "status": "failed",
            "trade_date": trade_date,
            "sync": sync_result,
            "readiness": readiness,
            "outcome": outcome,
            "recommendation": recommendation,
            "final_run": final_run,
            "related_run_id": run_id,
            "summary_text": f"盘后推荐 Run {run_id} 最终状态为 {final_status}",
        }

    recommended_count = int(final_run.get("recommended_count") or 0)
    return {
        "status": "completed",
        "trade_date": trade_date,
        "sync": sync_result,
        "readiness": readiness,
        "outcome": outcome,
        "recommendation": recommendation,
        "final_run": final_run,
        "related_run_id": run_id,
        "recommended_count": recommended_count,
        "summary_text": f"盘后数据已同步并通过健康检查，正式推荐完成 {recommended_count} 条",
    }


async def _run_recommendation(db, payload: dict) -> dict:
    """执行推荐流水线（异步入队到独立子进程，后端重启不影响执行）。

    T12 稳定性修复：原先在此处用 asyncio.run(run_workbench(...)) 在后端进程内
    同步执行，后端一旦重启/热重载会直接杀死正在跑的 formal run
    （error=「服务重启或进程退出」），导致 A 项（每日可见推荐）断档。

    现改为与 API 手动触发一致的 enqueue_workbench_run 路径：创建 RecommendationRun
    后由 _spawn_run_subprocess(start_new_session=True) 派发到独立子进程执行。子进程与
    uvicorn 进程解耦，后端重启后子进程仍继续更新心跳，recover_interrupted_runs 按心跳
    超时判定不会误杀存活任务。本函数立即返回入队结果（recommended_count=0），实际推荐
    数量由子进程完成后写入 recommendation_runs，安全网据此检查断档。
    """
    from app.services.workbench_service import enqueue_workbench_run

    run_payload: dict[str, Any] = {
        "preset_id": payload.get("preset_id"),
        "run_name": payload.get("run_name", f"定时推荐 {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d')}"),
    }
    # Preserve every explicitly scheduled RunRequest input.  Defaults remain owned
    # by the canonical parser/preset merge, but a scheduler must never silently
    # discard an explicitly selected review, date, scope, or matrix policy.
    for key in (
        "data_source",
        "risk_template",
        "score_line",
        "output_count",
        "selected_skill_ids",
        "skills",
        "skip_aux_refresh",
        "disable_agent_review",
        "trade_date_override",
        "market_scope",
        "market_scope_value",
        "agent_review_limit",
        "agent_review_concurrency",
        "hide_recent_repeats",
        "dedup_same_day",
        "matrix_batch_id",
        "matrix_batch_name",
        "matrix_run",
    ):
        if key in payload:
            run_payload[key] = payload.get(key)

    # Scheduler emits the same immutable request shape as API callers.  Keep the
    # no-preset in-process fallback below, but never let it bypass request parsing.
    from app.services.runs.contracts import parse_run_request, validate_run_request_no_credentials

    validate_run_request_no_credentials(payload)
    if "selected_skill_ids" in run_payload:
        run_payload["skills"] = [
            {"skill_id": str(skill_id), "weight": 1.0, "sort_order": index}
            for index, skill_id in enumerate(run_payload.pop("selected_skill_ids") or [])
            if str(skill_id).strip()
        ]
    # Validate the durable shape, but keep only explicitly scheduled fields in
    # ``run_payload`` so Pydantic defaults never overwrite a preset during merge.
    # ``RunRequest`` owns the formal default; an explicitly supplied value must
    # survive unchanged so invalid values are rejected rather than silently
    # rewritten and experiment/backtest modes cross every dispatch boundary.
    validation_payload = dict(run_payload)
    if "run_mode" in payload:
        validation_payload["run_mode"] = payload["run_mode"]
    parse_run_request(validation_payload)
    if "run_mode" in payload:
        run_payload["run_mode"] = payload["run_mode"]

    # 无 preset_id 时无法入队独立子进程（enqueue 前置条件），退化为原 in-process 执行，
    # 保留历史行为，避免破坏潜在的非预设触发路径。
    if not run_payload.get("preset_id"):
        from app.services.workbench_service import run_workbench

        logger.warning("定时推荐缺少 preset_id，退化为进程内执行: %s", run_payload)
        result = await run_workbench(db, run_payload)
        run = result.get("run") or {}
        related_run_id = str(run.get("run_id") or "")
        recommended_count = int(run.get("recommended_count") or result.get("recommended_count") or 0)
        return {
            "related_run_id": related_run_id,
            "recommended_count": recommended_count,
            "summary_text": (
                f"关联 Run {related_run_id}，推荐 {recommended_count} 条（进程内）"
                if related_run_id
                else f"自动推荐 {recommended_count} 条（进程内）"
            ),
        }

    result = enqueue_workbench_run(db, run_payload)
    run = result.get("run") or {}
    related_run_id = str(run.get("run_id") or "")
    logger.info("定时推荐已入队独立子进程: run_id=%s", related_run_id)
    return {
        "related_run_id": related_run_id,
        "recommended_count": 0,
        "queued": True,
        "summary_text": (
            f"已入队 Run {related_run_id}，由独立子进程异步执行"
            if related_run_id
            else "已触发推荐（独立子进程）"
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Formal Run 安全网：检测断档并自动补跑
# ═══════════════════════════════════════════════════════════════

def check_formal_recommendation_gap(db) -> dict:
    """检查最近交易日是否有成功的 formal 推荐（completed + recommended_count>0）。

    返回 {needs_retry, trade_date, ok_count, total_count, reason}。
    """
    from sqlalchemy import text as _text
    r = db.execute(_text("""
        SELECT trade_date,
               SUM(CASE WHEN status='completed' AND COALESCE(recommended_count,0) > 0 THEN 1 ELSE 0 END) as ok_count,
               COUNT(*) as total_count
        FROM recommendation_runs
        WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 5 DAY)
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT 1
    """)).fetchone()

    if not r:
        return {"needs_retry": True, "trade_date": None, "ok_count": 0, "total_count": 0,
                "reason": "最近 5 天无 formal run"}

    trade_date, ok_count, total_count = str(r[0]), int(r[1] or 0), int(r[2] or 0)
    if ok_count > 0:
        return {"needs_retry": False, "trade_date": trade_date, "ok_count": ok_count,
                "total_count": total_count, "reason": f"{trade_date} 已有 {ok_count} 次成功推荐"}
    if total_count >= SAFETY_NET_MAX_ATTEMPTS:
        return {"needs_retry": False, "trade_date": trade_date, "ok_count": ok_count,
                "total_count": total_count, "reason": f"{trade_date} 已尝试 {total_count} 次，放弃"}
    return {"needs_retry": True, "trade_date": trade_date, "ok_count": ok_count,
            "total_count": total_count, "reason": f"{trade_date} 无成功推荐（{total_count} 次尝试）"}


async def _safety_net_run(db, payload: dict) -> dict:
    """安全网任务：检查 formal 推荐断档，若需要则自动补跑。"""
    check = check_formal_recommendation_gap(db)
    if not check["needs_retry"]:
        logger.info("安全网跳过: %s", check["reason"])
        return {"skipped": True, **check, "summary_text": f"安全网跳过: {check['reason']}"}

    logger.info("安全网触发补跑: %s", check["reason"])
    # 复用 post_sync_formal_recommendation 的 payload，确保参数一致
    task = db.get(ScheduledTask, "post_sync_formal_recommendation")
    if task and task.payload_json:
        run_payload = json.loads(task.payload_json)
    else:
        run_payload = {
            "preset_id": "preset_formal_opening",
            "run_name": "安全网补跑推荐",
            "data_source": DEFAULT_RECOMMENDATION_DATA_SOURCE,
            "risk_template": "balanced",
            "score_line": 72,
            "output_count": 6,
            "skip_aux_refresh": True,
        }
    run_payload["run_name"] = f"安全网补跑 {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%m%d_%H%M')}"

    result = await _run_recommendation(db, run_payload)
    return {"skipped": False, **check, **result}


# ═══════════════════════════════════════════════════════════════
#  低曝光策略补跑：对近30天样本不足的 enabled 策略单独跑 single_skill_id，
#  产出 experiment run，仅供 metrics_by_strategy 统计，不进记录中心/驾驶舱。
#  目的：打破「低曝光策略没样本 → 永远不被选 → 永远没样本」的死循环。
# ═══════════════════════════════════════════════════════════════

# 串行 enqueue 时每个策略之间的间隔，避免同时拉起多个全市场扫描子进程造成 CPU 尖峰/心跳超时。
EXPOSURE_ENQUEUE_STAGGER_SECONDS = 8


def _has_inflight_experiment_run(db, skill_id: str, within_minutes: int = 60) -> bool:
    """该策略近 within_minutes 分钟内是否已有 queued/running 的 experiment run。

    曝光补跑串行 enqueue 多个策略，每个策略起一个独立子进程跑全市场扫描（85~160s）。
    若上一次补跑的子进程尚未结束（或被后端重启打断后仍在窗口内），重复 enqueue 会造成
    同一策略多个重离子进程并发 → CPU 争用 → 心跳超时中断（实测 interrupted 多源于此）。
    跳过在途任务可避免堆积，让有限的补跑窗口真正产出样本。
    """
    if db is None:
        # 测试中以 None 注入（DB 全 mock）时无法查在途，直接放行（与旧行为一致）。
        return False
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=within_minutes)
    stmt = (
        select(RecommendationRun)
        .where(RecommendationRun.run_mode == "experiment")
        .where(RecommendationRun.status.in_(("queued", "running")))
        .where(RecommendationRun.started_at >= cutoff)
    )
    for run in db.scalars(stmt).all():
        try:
            skills = json.loads(run.selected_skills_json or "[]")
        except (ValueError, TypeError):
            continue
        ids = [s.get("skill_id") for s in skills if isinstance(s, dict)]
        if skill_id in ids:
            return True
    return False


async def _run_strategy_exposure(db, payload: dict, task_run_id: str | None = None) -> dict:
    """对近30天样本不足的策略串行补跑 single_skill_id（experiment 模式）。

    每个策略单独 enqueue 到独立子进程，run_mode=experiment，关闭 LLM 审查以加速。
    产出的推荐只供 metrics_by_strategy 统计胜率，不进记录中心/驾驶舱（list_runs 过滤 experiment）。

    低曝光判定口径（修复点）：只统计 run_mode IN ('formal','experiment') 的真实推荐样本，
    必须排除 'backtest' 合成回测。backtest run 同样带 skill_breakdown_json，若计入会让
    真实样本不足的策略被误判为「样本已够」而永远不被补跑（high-range 曾因 13 笔 backtest
    命中被误判 sample>=20，真实 formal+experiment 仅 16，长期卡在 n<20）。
    """
    from app.services.workbench_service import enqueue_workbench_run, sync_skills

    window_days = int(payload.get("window_days", 30))
    min_sample = int(payload.get("min_sample_threshold") or DEFAULT_EXPOSURE_MIN_SAMPLE)
    trade_date = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d")

    # 1. 拿全部 enabled 策略
    skills = sync_skills(db)
    enabled_ids = [s.skill_id for s in skills if s.enabled]
    name_by_sid = {s.skill_id: s.name for s in skills}

    # 2. 拿近 window_days 各策略「真实」样本数（formal+experiment，排除 backtest 合成回测）
    #    口径与 scripts/self_check.py 的 E 项曝光分布一致（按 recommended_at 近 N 天），
    #    避免误用 trade_date 窗口导致高覆盖策略被低估、错选入曝光补跑。
    from app.services.outcome_service import exposure_sample_counts

    sample_by_sid = exposure_sample_counts(
        db, window_days=window_days, run_mode=("formal", "experiment")
    )

    # 3. 筛低曝光策略：enabled 但样本 < 阈值
    low_exposure = []
    skipped = []
    for sid in sorted(enabled_ids):
        cnt = sample_by_sid.get(sid, 0)
        if cnt < min_sample:
            low_exposure.append({"skill_id": sid, "sample_count": cnt})
        else:
            skipped.append({"skill_id": sid, "sample_count": cnt})

    if not low_exposure:
        msg = f"曝光补跑跳过: 全部 {len(enabled_ids)} 个策略近{window_days}天样本均≥{min_sample}"
        logger.info(msg)
        return {
            "skipped": True,
            "window_days": window_days,
            "min_sample_threshold": min_sample,
            "exposed_strategies": [],
            "skipped_strategies": skipped,
            "summary_text": msg,
        }

    logger.info(
        "曝光补跑启动: 近%d天低曝光策略 %d/%d: %s",
        window_days, len(low_exposure), len(enabled_ids),
        [r["skill_id"] for r in low_exposure],
    )

    # 4. 串行 enqueue 每个低曝光策略
    exposed: list[dict] = []
    skipped_inflight: list[dict] = []
    total = len(low_exposure)
    for index, item in enumerate(low_exposure, start=1):
        sid = item["skill_id"]
        if task_run_id:
            _update_matrix_progress(
                db,
                task_run_id,
                summary=f"曝光补跑 {index}/{total}: {sid}（样本 {item['sample_count']}）",
            )
        # 跳过在途任务：避免与尚未结束的上一次补跑（或被重启打断仍在窗口内）重叠，
        # 防止同一策略多个重离子进程并发导致 CPU 争用与心跳超时中断。
        if _has_inflight_experiment_run(db, sid):
            logger.info("曝光补跑跳过在途任务: %s（近 60min 已有 queued/running experiment run）", sid)
            skipped_inflight.append({"skill_id": sid, "sample_count": item["sample_count"]})
            continue
        try:
            result = enqueue_workbench_run(
                db,
                {
                    "single_skill_id": sid,
                    "run_name": f"曝光补跑 {name_by_sid.get(sid, sid)} {trade_date}",
                    "run_mode": "experiment",
                    "skip_aux_refresh": True,
                    "disable_agent_review": True,
                },
            )
            run = result.get("run") or {}
            exposed.append({
                "skill_id": sid,
                "run_id": run.get("run_id"),
                "queued": bool(result.get("queued")),
            })
        except Exception as exc:
            logger.exception("曝光补跑策略 %s 入队失败: %s", sid, exc)
            db.rollback()
            exposed.append({"skill_id": sid, "error": str(exc)})
        # 错峰入队：每个策略起一个全市场扫描子进程，间隔拉起避免 CPU 尖峰。
        if index < total:
            time.sleep(EXPOSURE_ENQUEUE_STAGGER_SECONDS)

    inflight_sids = [r["skill_id"] for r in skipped_inflight]
    msg = (
        f"曝光补跑完成: {len(exposed)}/{total} 策略已入队"
        f"（跳过在途 {len(skipped_inflight)}: {', '.join(inflight_sids) or '无'}；"
        f"近{window_days}天样本<{min_sample}的: {', '.join(r['skill_id'] for r in low_exposure)}）"
    )
    return {
        "skipped": False,
        "window_days": window_days,
        "min_sample_threshold": min_sample,
        "exposed_strategies": exposed,
        "skipped_inflight_strategies": skipped_inflight,
        "skipped_strategies": skipped,
        "summary_text": msg,
    }


def ensure_formal_recommendation_safely() -> dict:
    """线程安全的包装：在后台线程中调用安全网检查并补跑。

    用于 startup 和外部调用。返回检查结果。
    """
    db = SessionLocal()
    try:
        check = check_formal_recommendation_gap(db)
        if not check["needs_retry"]:
            logger.info("启动安全网跳过: %s", check["reason"])
            return check

        logger.info("启动安全网触发补跑: %s", check["reason"])
        result = asyncio.run(_safety_net_run(db, {}))
        logger.info("启动安全网完成: skipped=%s", result.get("skipped", False))
        return result
    except Exception:
        logger.exception("启动安全网执行失败")
        return {"error": "safety_net_failed"}
    finally:
        db.close()


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
            "risk_template",
            "score_line",
            "output_count",
            "selected_skill_ids",
            "skip_aux_refresh",
            "disable_agent_review",
            "agent_review_limit",
            "agent_review_concurrency",
            "run_mode",
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

"""APScheduler 调度执行器 — 按 Cron 表达式执行 ScheduledTask。

在 FastAPI 启动时创建 BackgroundScheduler，从数据库加载已启用的任务。
支持任务类型:
  - data_sync: 同步行情数据（刷新报价池 + 回填日K线 + 辅助数据）
  - recommendation_run: 执行推荐流水线
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import SessionLocal
from app.models.tables import ScheduledTask
from app.services.cost_service import record_task_run

logger = logging.getLogger("scheduler")

_scheduler: BackgroundScheduler | None = None
_running_lock = threading.Lock()
_running_tasks: set[str] = set()


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


def _execute_task(task_id: str, *, force: bool = False) -> None:
    """执行单个调度任务（同步包装异步）。"""
    if _is_task_running(task_id):
        logger.warning("调度任务 %s 正在执行中，跳过重复触发", task_id)
        return

    logger.info("调度任务开始: %s", task_id)
    _mark_running(task_id)

    db = SessionLocal()
    try:
        task = db.get(ScheduledTask, task_id)
        if not task or (not task.enabled and not force):
            logger.info("调度任务 %s 已禁用或不存在，跳过", task_id)
            return

        payload = json.loads(task.payload_json or "{}")
        task_type = task.task_type

        if task_type == "recommendation_run":
            asyncio.run(_run_recommendation(db, payload))
        elif task_type in ("data_sync", "pool_refresh", "data_backfill"):
            _run_data_sync(db, payload)
        else:
            logger.warning("未知调度任务类型: %s", task_type)
            record_task_run(db, task_id, "failed", f"未知任务类型: {task_type}")
            return

        record_task_run(db, task_id, "completed")
        logger.info("调度任务完成: %s", task_id)
    except Exception as e:
        logger.exception("调度任务失败: %s", task_id)
        try:
            record_task_run(db, task_id, "failed", str(e))
        except Exception:
            pass
    finally:
        db.close()
        _mark_done(task_id)


def _run_data_sync(db, payload: dict) -> None:
    """统一数据同步：刷新报价池 + 回填日K线。"""
    from app.services.market_pool import refresh_quote_pool
    from app.services.workbench_service import invalidate_bootstrap_cache

    sync_scope = payload.get("sync_scope", "all")

    # 1. 刷新行情池
    if sync_scope in ("quotes", "all"):
        quotes, meta = refresh_quote_pool(db)
        invalidate_bootstrap_cache()
        logger.info("行情池刷新: %d 条行情, %s", len(quotes), meta)

    # 2. 回填日K线
    if sync_scope in ("daily_bars", "all"):
        _run_backfill_script(payload)

    # 3. 回填辅助数据
    if sync_scope in ("auxiliary", "all"):
        _run_auxiliary_script(payload)


def _run_backfill_script(payload: dict) -> None:
    """执行日K线回填脚本。"""
    import subprocess
    import sys
    from pathlib import Path

    from app.core.config import get_settings

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    script = scripts_dir / "backfill_daily_bars.py"
    if not script.exists():
        raise RuntimeError(f"回填脚本不存在: {script}")

    args = [
        sys.executable, str(script),
        "--scope", payload.get("scope", "eligible"),
    ]
    if payload.get("days"):
        args.extend(["--days", str(payload["days"])])
    if payload.get("rate_limit") is not None:
        args.extend(["--rate-limit", str(payload["rate_limit"])])
    if payload.get("force"):
        args.append("--force")

    timeout = get_settings().script_timeout_seconds or None
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"日K回填脚本失败: {result.stderr[:500]}")
    logger.info("backfill_daily_bars.py 完成:\n%s", result.stdout[-500:])


def _run_auxiliary_script(payload: dict) -> None:
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
    if payload.get("flow_top_n"):
        args.extend(["--flow-top-n", str(payload["flow_top_n"])])

    timeout = get_settings().script_timeout_seconds or None
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"辅助数据回填脚本失败: {result.stderr[:500]}")
    logger.info("backfill_auxiliary_data.py 完成:\n%s", result.stdout[-500:])


async def _run_recommendation(db, payload: dict) -> None:
    """执行推荐流水线。"""
    from app.services.workbench_service import run_workbench

    run_payload = {
        "preset_id": payload.get("preset_id"),
        "run_name": payload.get("run_name", f"定时推荐 {datetime.utcnow().strftime('%Y%m%d')}"),
    }
    for key in (
        "run_mode",
        "data_source",
        "risk_template",
        "output_count",
        "selected_skill_ids",
        "skip_aux_refresh",
    ):
        if key in payload:
            run_payload[key] = payload.get(key)
    result = await run_workbench(db, run_payload)
    logger.info("定时推荐完成: %d 条", result.get("recommended_count", 0))


def run_task_now(task_id: str) -> None:
    """立即执行一个调度任务，用于页面上的手动触发。"""
    _execute_task(task_id, force=True)


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

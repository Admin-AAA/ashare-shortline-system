"""APScheduler 调度执行器 — 按 Cron 表达式执行 ScheduledTask。

在 FastAPI 启动时创建 BackgroundScheduler，从数据库加载已启用的任务。
支持任务类型:
  - pool_refresh: 刷新行情池
  - recommendation_run: 执行推荐流水线
  - data_backfill: 回填辅助数据
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.tables import ScheduledTask
from app.services.cost_service import record_task_run

logger = logging.getLogger("scheduler")

_scheduler: BackgroundScheduler | None = None


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


def _execute_task(task_id: str) -> None:
    """执行单个调度任务（同步包装异步）。"""
    logger.info("调度任务开始: %s", task_id)
    db = SessionLocal()
    try:
        task = db.get(ScheduledTask, task_id)
        if not task or not task.enabled:
            logger.info("调度任务 %s 已禁用或不存在，跳过", task_id)
            return

        payload = json.loads(task.payload_json or "{}")
        task_type = task.task_type

        if task_type == "pool_refresh":
            _run_pool_refresh(db, payload)
        elif task_type == "recommendation_run":
            asyncio.run(_run_recommendation(db, payload))
        elif task_type == "data_backfill":
            _run_data_backfill(payload)
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


def _run_pool_refresh(db, payload: dict) -> None:
    """刷新行情池。"""
    from app.services.market_pool import refresh_quote_pool

    quotes, meta = refresh_quote_pool(db)
    logger.info("行情池刷新: %d 条行情, %s", len(quotes), meta)


async def _run_recommendation(db, payload: dict) -> None:
    """执行推荐流水线。"""
    from app.services.workbench_service import run_workbench

    settings = get_settings()
    result = await run_workbench(db, {
        "run_name": payload.get("run_name", f"定时推荐 {datetime.utcnow().strftime('%Y%m%d')}"),
        "run_mode": payload.get("run_mode", "formal"),
        "data_mode": payload.get("data_mode", "realtime"),
        "risk_template": payload.get("risk_template", "balanced"),
        "output_count": payload.get("output_count", 20),
        "selected_skill_ids": payload.get("selected_skill_ids"),
    })
    logger.info("定时推荐完成: %d 条", result.get("recommended_count", 0))


def _run_data_backfill(payload: dict) -> None:
    """回填数据（同步执行）。支持两种脚本：backfill_auxiliary_data.py 和 backfill_daily_bars.py。"""
    import subprocess
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"

    script_name = payload.get("script", "backfill_auxiliary_data.py")
    script = scripts_dir / script_name
    if not script.exists():
        raise RuntimeError(f"回填脚本不存在: {script}")

    args = [sys.executable, str(script)]

    if script_name == "backfill_daily_bars.py":
        args.extend(["--scope", payload.get("scope", "all-quotes")])
        if payload.get("days"):
            args.extend(["--days", str(payload["days"])])
        if payload.get("rate_limit") is not None:
            args.extend(["--rate-limit", str(payload["rate_limit"])])
        if payload.get("force"):
            args.append("--force")
    else:
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

    result = subprocess.run(args, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"数据回填脚本失败: {result.stderr[:500]}")
    logger.info("%s 完成:\n%s", script_name, result.stdout[-500:])


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

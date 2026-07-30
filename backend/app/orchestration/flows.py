"""Prefect flows are the only process boundary for background business work."""
from __future__ import annotations

import asyncio

from prefect import flow, task


@task(retries=2, retry_delay_seconds=[300, 900])
def execute_scheduled_task(task_id: str, trigger_source: str) -> None:
    from app.services.scheduler_service import _execute_task

    _execute_task(task_id, force=trigger_source == "manual", trigger_source=trigger_source)


@flow(name="scheduled-task")
def scheduled_task_flow(task_id: str, trigger_source: str = "schedule") -> None:
    execute_scheduled_task(task_id, trigger_source)


@task(retries=1, retry_delay_seconds=30)
def execute_recommendation_run(run_id: str) -> None:
    from app.db.session import SessionLocal
    from app.services.runs.pipeline import run_staged_pipeline
    from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
    from app.services.workbench_service import _mark_run_dispatched, _mark_run_failed

    db = SessionLocal()
    try:
        if not _mark_run_dispatched(db, run_id):
            db.rollback()
            return
        db.commit()
        record = SQLAlchemyRunRepository(db).get_run(run_id)
        if record is None:
            raise RuntimeError(f"Run {run_id} has no canonical request")
        asyncio.run(run_staged_pipeline(db, run_id=run_id, request=record.request))
    except Exception as exc:
        db.rollback()
        _mark_run_failed(db, run_id, exc)
        raise
    finally:
        db.close()


@flow(name="recommendation-run")
def recommendation_flow(run_id: str) -> None:
    execute_recommendation_run(run_id)


@flow(name="manual-preset-matrix")
def manual_preset_matrix_flow(task_run_id: str, payload: dict) -> None:
    from app.services.scheduler_service import _execute_manual_preset_matrix

    _execute_manual_preset_matrix(task_run_id, payload)


@flow(name="manual-data-health-remediation")
def manual_data_health_remediation_flow(task_run_id: str, payload: dict) -> None:
    from app.services.scheduler_service import _execute_manual_data_health_remediation

    _execute_manual_data_health_remediation(task_run_id, payload)


@flow(name="manual-strategy-exposure")
def manual_strategy_exposure_flow(task_run_id: str, payload: dict) -> None:
    from app.services.scheduler_service import _execute_manual_strategy_exposure

    _execute_manual_strategy_exposure(task_run_id, payload)


@task(retries=0)
def poll_intraday_lock() -> dict:
    """Prefect-scheduled intraday lock polling (replaces in-process interval job)."""
    from app.services.intraday_lock_service import poll_once
    from app.services.tushare_client import get_pro
    from app.db.session import SessionLocal
    import logging

    logger = logging.getLogger(__name__)
    try:
        db = SessionLocal()
        try:
            stats = poll_once(db, get_pro())
            if stats.get("triggered") or stats.get("errors"):
                logger.info("盘中锁利轮询: %s", stats)
            return stats
        finally:
            db.close()
    except Exception:
        logger.exception("盘中锁利轮询失败")
        return {"error": True}


@flow(name="intraday-lock-poll")
def intraday_lock_poll_flow() -> None:
    poll_intraday_lock()


@task(retries=0)
def recover_stale_runs() -> int:
    """Prefect-scheduled stale run recovery (replaces in-process interval job)."""
    from app.services.workbench_service import recover_interrupted_runs
    from app.db.session import SessionLocal
    import logging

    logger = logging.getLogger(__name__)
    try:
        db = SessionLocal()
        try:
            recovered = recover_interrupted_runs(db)
            if recovered:
                logger.warning("周期巡检回收 %d 个失联运行", recovered)
            return recovered
        finally:
            db.close()
    except Exception:
        logger.exception("周期巡检回收失联运行失败")
        return 0


@flow(name="stale-run-recovery")
def stale_run_recovery_flow() -> None:
    recover_stale_runs()

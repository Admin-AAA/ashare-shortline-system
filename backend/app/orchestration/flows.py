"""Prefect flows are the only process boundary for background business work."""
from __future__ import annotations

import asyncio

from prefect import flow, task


@task(retries=2, retry_delay_seconds=[10, 60])
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

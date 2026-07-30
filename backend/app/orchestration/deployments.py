"""Deployment synchronization from database-owned ScheduledTask rows."""
from __future__ import annotations

import json

from prefect.schedules import Cron

from app.db.session import SessionLocal
from app.models.tables import ScheduledTask
from app.orchestration.contracts import DeploymentSyncResult
from app.orchestration.flows import (
    manual_data_health_remediation_flow,
    manual_preset_matrix_flow,
    manual_strategy_exposure_flow,
    recommendation_flow,
    scheduled_task_flow,
)


SCHEDULED_DEPLOYMENT_PREFIX = "scheduled-task"
RECOMMENDATION_DEPLOYMENT_NAME = "recommendation-run/default"
MANUAL_MATRIX_DEPLOYMENT_NAME = "manual-preset-matrix/default"
MANUAL_REMEDIATION_DEPLOYMENT_NAME = "manual-data-health-remediation/default"
MANUAL_EXPOSURE_DEPLOYMENT_NAME = "manual-strategy-exposure/default"


def schedule_all_enabled_tasks() -> DeploymentSyncResult:
    db = SessionLocal()
    try:
        tasks = db.query(ScheduledTask).filter(ScheduledTask.enabled.is_(True)).all()
    finally:
        db.close()
    names: list[str] = []
    for item in tasks:
        name = f"{SCHEDULED_DEPLOYMENT_PREFIX}/{item.task_id}"
        deployment = scheduled_task_flow.to_deployment(
            name=item.task_id,
            schedules=[Cron(item.cron_expression, timezone="Asia/Shanghai")],
            parameters={"task_id": item.task_id, "trigger_source": "schedule"},
            tags=["ashare", "scheduled-task", item.task_type],
        )
        deployment.apply()
        names.append(name)
    recommendation_flow.to_deployment(name="default", tags=["ashare", "recommendation"]).apply()
    manual_preset_matrix_flow.to_deployment(name="default", tags=["ashare", "matrix"]).apply()
    manual_data_health_remediation_flow.to_deployment(name="default", tags=["ashare", "remediation"]).apply()
    manual_strategy_exposure_flow.to_deployment(name="default", tags=["ashare", "exposure"]).apply()
    return DeploymentSyncResult(len(tasks), tuple(sorted(names)))

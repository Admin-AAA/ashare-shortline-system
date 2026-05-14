"""运维管理 API：成本追踪、系统健康、调度任务、数据源监控。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.cost_service import (
    cost_summary,
    list_scheduled_tasks,
    record_task_run,
    system_health,
    upsert_scheduled_task,
)
from app.services.data_source_health import active_alerts, readiness_check

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/health")
def get_health(db: Session = Depends(get_db)):
    return system_health(db)


@router.get("/costs")
def get_costs(days: int = 30, db: Session = Depends(get_db)):
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    return cost_summary(db, days=days)


@router.get("/tasks")
def get_tasks(db: Session = Depends(get_db)):
    return list_scheduled_tasks(db)


@router.get("/source-health")
def get_source_health(db: Session = Depends(get_db)):
    """各数据源健康状态。"""
    from app.services.data_sources import list_data_sources
    return list_data_sources(db)


@router.get("/alerts")
def get_alerts(db: Session = Depends(get_db)):
    """当前活跃告警。"""
    return active_alerts(db)


@router.get("/readiness")
def get_readiness(db: Session = Depends(get_db)):
    """系统就绪检查。"""
    return readiness_check(db)


@router.post("/tasks")
def create_task(payload: dict, db: Session = Depends(get_db)):
    task_id = payload.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    return {
        "task_id": upsert_scheduled_task(
            db,
            task_id=task_id,
            name=payload.get("name", task_id),
            task_type=payload.get("task_type", ""),
            cron_expression=payload.get("cron_expression", ""),
            payload=payload.get("payload"),
            enabled=payload.get("enabled", True),
        ).task_id
    }


@router.post("/tasks/{task_id}/_record")
def record_task(task_id: str, payload: dict, db: Session = Depends(get_db)):
    record_task_run(db, task_id, payload.get("status", "unknown"), payload.get("error"))
    return {"ok": True}


@router.post("/tasks/_reload")
def reload_tasks():
    """热重载调度器任务（Cron 配置变更后调用）。"""
    from app.services.scheduler_service import reload_tasks
    count = reload_tasks()
    return {"ok": True, "active_jobs": count}

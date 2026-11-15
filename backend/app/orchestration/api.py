"""The only Prefect client boundary exposed to FastAPI, CLI, and domain services."""
from __future__ import annotations

import asyncio
from typing import Any

from prefect.client.orchestration import get_client
from prefect.deployments import run_deployment

from app.core.alerts import emit_alert
from app.core.prefect_bootstrap import require_resident_prefect_client
from app.orchestration.contracts import OrchestrationRunRef, OrchestrationRunView
from app.orchestration.deployments import (
    MANUAL_REMEDIATION_DEPLOYMENT_NAME,
    RECOMMENDATION_DEPLOYMENT_NAME,
    SCHEDULED_DEPLOYMENT_PREFIX,
)
from app.services.observability import current_correlation_id, log_observation

import logging

logger = logging.getLogger(__name__)


def _trigger(name: str, parameters: dict) -> Any:
    """统一触发口：先验客户端配置，失败留告警。

    不包装异常类型，避免改变 workbench / scheduler 里既有的失败处理语义；
    但每次 Prefect 触发失败都会在告警登记里留下可 grep 的记录。
    """
    require_resident_prefect_client()
    try:
        return run_deployment(name=name, parameters=parameters, timeout=0, _sync=True)
    except Exception as exc:  # noqa: BLE001 - 告警后原样抛，保留原始异常链
        emit_alert("prefect_trigger_failed", name,
                   f"Prefect 部署 {name} 触发失败：{type(exc).__name__}: {str(exc)[:220]}")
        raise


def trigger_scheduled_task(
    task_id: str,
    trigger_source: str = "manual",
    correlation_id: str | None = None,
) -> OrchestrationRunRef:
    name = f"{SCHEDULED_DEPLOYMENT_PREFIX}/{task_id}"
    correlation_id = correlation_id or current_correlation_id()
    log_observation(logger, stage="scheduler", reason_code="PREFECT_TRIGGER", identity=task_id)
    # _sync=True 强制同步路径：这些 trigger 在 sync def 内调用，但可能运行在
    # FastAPI async event loop 中（如 on_startup 启动事件）。async_dispatch 会
    # 在 async 上下文把 run_deployment 派发到 arun_deployment 返回未 await 的
    # coroutine —— 调度静默失败（'arun_deployment was never awaited'）。
    run = _trigger(
        name=name,
        parameters={"task_id": task_id, "trigger_source": trigger_source, "correlation_id": correlation_id},
    )
    return OrchestrationRunRef(str(run.id), name)


def trigger_recommendation(
    run_id: str,
    correlation_id: str | None = None,
) -> OrchestrationRunRef:
    correlation_id = correlation_id or current_correlation_id()
    log_observation(logger, stage="scheduler", reason_code="PREFECT_TRIGGER", identity=run_id)
    run = _trigger(
        name=RECOMMENDATION_DEPLOYMENT_NAME,
        parameters={"run_id": run_id, "correlation_id": correlation_id},
    )
    return OrchestrationRunRef(str(run.id), RECOMMENDATION_DEPLOYMENT_NAME)


def _trigger_manual(name: str, task_run_id: str, payload: dict) -> OrchestrationRunRef:
    run = _trigger(
        name=name,
        parameters={"task_run_id": task_run_id, "payload": payload},
    )
    return OrchestrationRunRef(str(run.id), name)


def trigger_manual_remediation(task_run_id: str, payload: dict) -> OrchestrationRunRef:
    return _trigger_manual(MANUAL_REMEDIATION_DEPLOYMENT_NAME, task_run_id, payload)


def get_orchestration_run(run_id: str) -> OrchestrationRunView:
    require_resident_prefect_client()

    async def _read() -> OrchestrationRunView:
        async with get_client() as client:
            item = await client.read_flow_run(run_id)
        return OrchestrationRunView(
            run_id=str(item.id),
            state=item.state_name or "Unknown",
            name=item.name,
            parameters=dict(item.parameters or {}),
        )

    return asyncio.run(_read())

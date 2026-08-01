"""The only Prefect client boundary exposed to FastAPI, CLI, and domain services."""
from __future__ import annotations

import asyncio

from prefect.client.orchestration import get_client
from prefect.deployments import run_deployment

from app.orchestration.contracts import OrchestrationRunRef, OrchestrationRunView
from app.orchestration.deployments import (
    MANUAL_EXPOSURE_DEPLOYMENT_NAME,
    MANUAL_MATRIX_DEPLOYMENT_NAME,
    MANUAL_REMEDIATION_DEPLOYMENT_NAME,
    RECOMMENDATION_DEPLOYMENT_NAME,
    SCHEDULED_DEPLOYMENT_PREFIX,
)


def trigger_scheduled_task(task_id: str, trigger_source: str = "manual") -> OrchestrationRunRef:
    name = f"{SCHEDULED_DEPLOYMENT_PREFIX}/{task_id}"
    # _sync=True 强制同步路径：这些 trigger 在 sync def 内调用，但可能运行在
    # FastAPI async event loop 中（如 on_startup 启动事件）。async_dispatch 会
    # 在 async 上下文把 run_deployment 派发到 arun_deployment 返回未 await 的
    # coroutine —— 调度静默失败（'arun_deployment was never awaited'）。
    run = run_deployment(
        name=name,
        parameters={"task_id": task_id, "trigger_source": trigger_source},
        timeout=0,
        _sync=True,
    )
    return OrchestrationRunRef(str(run.id), name)


def trigger_recommendation(run_id: str) -> OrchestrationRunRef:
    run = run_deployment(
        name=RECOMMENDATION_DEPLOYMENT_NAME,
        parameters={"run_id": run_id},
        timeout=0,
        _sync=True,
    )
    return OrchestrationRunRef(str(run.id), RECOMMENDATION_DEPLOYMENT_NAME)


def _trigger_manual(name: str, task_run_id: str, payload: dict) -> OrchestrationRunRef:
    run = run_deployment(
        name=name,
        parameters={"task_run_id": task_run_id, "payload": payload},
        timeout=0,
        _sync=True,
    )
    return OrchestrationRunRef(str(run.id), name)


def trigger_manual_matrix(task_run_id: str, payload: dict) -> OrchestrationRunRef:
    return _trigger_manual(MANUAL_MATRIX_DEPLOYMENT_NAME, task_run_id, payload)


def trigger_manual_remediation(task_run_id: str, payload: dict) -> OrchestrationRunRef:
    return _trigger_manual(MANUAL_REMEDIATION_DEPLOYMENT_NAME, task_run_id, payload)


def trigger_manual_exposure(task_run_id: str, payload: dict) -> OrchestrationRunRef:
    return _trigger_manual(MANUAL_EXPOSURE_DEPLOYMENT_NAME, task_run_id, payload)


def get_orchestration_run(run_id: str) -> OrchestrationRunView:
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

"""Deployment synchronization from database-owned ScheduledTask rows."""
from __future__ import annotations

import json
from pathlib import Path

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import DeploymentUpdate
from prefect.schedules import Cron

from app.core.prefect_bootstrap import require_resident_prefect_client
from app.db.session import SessionLocal
from app.models.tables import ScheduledTask
from app.orchestration.contracts import DeploymentSyncResult
from app.orchestration.flows import (
    execution_confirmation_poll_flow,
    intraday_lock_poll_flow,
    manual_data_health_remediation_flow,
    recommendation_flow,
    scheduled_task_flow,
    stale_run_recovery_flow,
)


SCHEDULED_DEPLOYMENT_PREFIX = "scheduled-task"
WORK_POOL_NAME = "ashare-local"
RECOMMENDATION_DEPLOYMENT_NAME = "recommendation-run/default"
MANUAL_REMEDIATION_DEPLOYMENT_NAME = "manual-data-health-remediation/default"
# 子 flow（推荐 run）走独立队列：父 scheduled-task flow 在等待子 run 时仍占 default
# 队列槽位（limit 2），两个父 flow 同时运行会把子 run 饿死成 Late（2026-10-15 实测死锁）。
# 独立队列不受 default 限额约束，只受 pool 上限约束。
RECOMMENDATION_WORK_QUEUE = "recommendation"
# 2026-11-16/17 数据健康连续两天挂掉的根因修复：高频轮询与业务日更必须分队列。
#
# default 队列并发上限只有 2，而 intraday_lock_poll / stale_run_recovery /
# execution_confirmation_poll 每 5 分钟各产生一条 run。这些 flow 单次可能跑
# 60~90 分钟（thsdk 游客账户重连超时），两个槽被占满后，每 5 分钟新增的 run
# 全部积压成 SCHEDULED；11-17 实测积压 123 条，17:35 盘后全量同步被挤在队尾
# 整天没启动 → 当天所有库表停在上一交易日 → 数据健康 blocked、推荐闸门关闭。
# 11-16 则是同一原因导致 17:35 / 18:40 / 21:00 连环迟到 2~5 小时。
#
# 因此：所有业务 ScheduledTask 与手动补数都走 recommendation 队列
# （priority=2、无队列级并发上限），只受 work pool 上限 6 约束；
# 只有下面 3 个高频 periodic flows 留在 default 队列，它们迟到即跳过、
# 堵死也不影响日更。推荐子 flow 本来就在 recommendation 队列，父子同队列
# 且无队列限额，不会出现 2026-10-15 那种「父等子、子被队列饿死」的死锁。
WORK_POOL_CONCURRENCY_LIMIT = 6

# Process worker spawns each flow run in its own subprocess. Without an
# explicit working directory the worker defaults to a fresh temp dir, so
# file-path entrypoints like ``app/orchestration/flows.py`` cannot resolve.
# Pin every deployment to the backend root (this file: backend/app/...).
_BACKEND_DIR = str(Path(__file__).resolve().parent.parent.parent)
_JOB_VARIABLES = {"working_dir": _BACKEND_DIR}


def schedule_all_enabled_tasks() -> DeploymentSyncResult:
    # 没有常驻 server 时 deployment.apply() 会写进一个用完即关的临时 server，
    # 返回「同步成功」但常驻 server 上什么都没变。必须先过客户端闸门。
    require_resident_prefect_client()
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
            work_pool_name=WORK_POOL_NAME,
            work_queue_name=RECOMMENDATION_WORK_QUEUE,
            schedules=[Cron(item.cron_expression, timezone="Asia/Shanghai")],
            parameters={"task_id": item.task_id, "trigger_source": "schedule"},
            tags=["ashare", "scheduled-task", item.task_type],
            job_variables=_JOB_VARIABLES,
        )
        deployment.apply()
        names.append(name)
    recommendation_flow.to_deployment(name="default", work_pool_name=WORK_POOL_NAME, work_queue_name=RECOMMENDATION_WORK_QUEUE, tags=["ashare", "recommendation"], job_variables=_JOB_VARIABLES).apply()
    manual_data_health_remediation_flow.to_deployment(name="default", work_pool_name=WORK_POOL_NAME, work_queue_name=RECOMMENDATION_WORK_QUEUE, tags=["ashare", "remediation"], job_variables=_JOB_VARIABLES).apply()
    # 下面 3 个高频 periodic infrastructure flows 故意留在 default 队列（limit=2）：
    # 它们迟到后 flow 内部会自行跳过，积压也不会再阻塞业务日更任务。
    # 不要给它们加 work_queue_name=RECOMMENDATION_WORK_QUEUE，否则又会把高优先队列占满。
    intraday_lock_poll_flow.to_deployment(
        name="default",
        work_pool_name=WORK_POOL_NAME,
        schedules=[Cron("*/5 9-11,13-14 * * mon-fri", timezone="Asia/Shanghai")],
        tags=["ashare", "periodic"],
        job_variables=_JOB_VARIABLES,
    ).apply()
    stale_run_recovery_flow.to_deployment(
        name="default",
        work_pool_name=WORK_POOL_NAME,
        schedules=[Cron("*/5 * * * mon-fri", timezone="Asia/Shanghai")],
        tags=["ashare", "periodic"],
        job_variables=_JOB_VARIABLES,
    ).apply()
    # 盘中买入确认：连续竞价时段每 5 分钟（9:30~11:30、13:00~14:55）。
    execution_confirmation_poll_flow.to_deployment(
        name="default",
        work_pool_name=WORK_POOL_NAME,
        schedules=[
            Cron("30-55/5 9 * * mon-fri", timezone="Asia/Shanghai"),
            Cron("*/5 10,13-14 * * mon-fri", timezone="Asia/Shanghai"),
            Cron("0-30/5 11 * * mon-fri", timezone="Asia/Shanghai"),
        ],
        tags=["ashare", "periodic"],
        job_variables=_JOB_VARIABLES,
    ).apply()
    return DeploymentSyncResult(len(tasks), tuple(sorted(names)))


def pause_disabled_task_deployments() -> tuple[str, ...]:
    """把 enabled=0 的任务留下的 deployment 暂停，返回本次新暂停的名字。

    schedule_all_enabled_tasks 只同步 enabled 任务，退役任务的 deployment 会带着
    active schedule 一直留在 server 上（实测 strategy_exposure_run 2026-10-19 退役后
    仍每工作日 08:03 触发）。_execute_task 会因 enabled=0 跳过，业务不会错，但
    ProcessWorker 每次都要为它拉起一个子进程（import 0.86s / RSS 114MB）后立刻退出。

    暂停而非删除：任务重新启用时 schedule_all_enabled_tasks 的 apply() 会把 paused 改回 False。
    """
    require_resident_prefect_client()
    db = SessionLocal()
    try:
        disabled = {
            item.task_id
            for item in db.query(ScheduledTask).filter(ScheduledTask.enabled.is_(False)).all()
        }
    finally:
        db.close()
    if not disabled:
        return ()
    paused: list[str] = []
    with get_client(sync_client=True) as client:
        for deployment in client.read_deployments():
            if deployment.name not in disabled or deployment.paused:
                continue
            client.update_deployment(deployment.id, DeploymentUpdate(paused=True))
            paused.append(deployment.name)
    return tuple(sorted(paused))

"""Synchronize deployments and start the local process worker."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app.core.deployment_identity import resolve_application_revision
from app.orchestration import schedule_all_enabled_tasks
from app.orchestration.deployments import RECOMMENDATION_WORK_QUEUE, WORK_POOL_CONCURRENCY_LIMIT

logger = logging.getLogger("run_prefect_worker")

# 本进程启动时刻。收尾只针对早于此刻就已 RUNNING 的 flow run——本 worker 启动后
# 自己提交的 flow run 必然晚于它，这样即使脚本被误在运行中调用也不会杀活任务。
_PROCESS_START = datetime.now(timezone.utc)


def _pin_application_revision() -> None:
    """Pin worker provenance to the code loaded at startup.

    Uses the same env -> cached git HEAD -> "unknown" resolver as the API so
    the worker-side execution_version identity can never diverge from the
    enqueue side purely because of launch-path differences. Resolving here also
    mirrors the result into ``os.environ`` for the spawned worker subprocess.
    """
    resolve_application_revision()


def _prefect_api_base() -> str:
    return (os.environ.get("PREFECT_API_URL") or "http://127.0.0.1:4200/api").rstrip("/")


def _post_json(url: str, payload: dict, timeout: float = 20.0):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode() or "null")


def _started_before_this_process(flow_run: dict) -> bool:
    """该 flow run 是否在本进程启动之前就已经在跑（即上次 worker 的残留）。

    时间判据比「检查有没有 ONLINE worker」可靠：worker 刚被 kill 时心跳可能尚未
    超时、Prefect 仍标它 ONLINE，那样会把该收的僵尸漏掉；而本 worker 启动后自己
    提交的 flow run，start_time 必然晚于本进程启动时刻。取不到时间时保守跳过。
    """
    raw = flow_run.get("start_time") or flow_run.get("expected_start_time")
    if not raw:
        return False
    try:
        started = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started < _PROCESS_START


def _reap_orphan_running_flow_runs() -> None:
    """把上一个 worker 残留的 RUNNING flow run 收尾，释放 work queue 并发槽。

    为什么必须做（L-145）：`launchctl kickstart -k` / SIGTERM 重启 worker 会直接杀掉
    正在执行的 flow run 子进程。DB 侧有 stale-run-recovery 兜底把 RecommendationRun
    标成 interrupted，但 **Prefect 侧的 flow run 没有任何人收尾，永久停在 RUNNING**。
    work queue `default` 的 concurrency_limit 是 2，于是只要攒够 2 个这样的僵尸，
    并发槽就被永久占满——worker 显示 ONLINE、心跳正常，却再也取不到任何活，此后
    所有入队的 run 全部停在 queued 不动。2026-09-24 实测：两个僵尸（06:51、06:55）
    堵死队列，39 个 flow run 积压，页面点「运行推荐」全部无响应。

    在 worker 启动**之前**执行是安全的前提：本机是单 worker 单 pool 部署，此刻不
    存在正在执行的 flow run，任何 RUNNING 都只能是上次残留。标 CRASHED 是终态、
    只释放槽位不丢业务数据（RecommendationRun 的真实状态由 DB 侧独立维护）。

    任何失败都只记日志、不阻塞 worker 启动——收不掉僵尸也要让 worker 起来。
    """
    base = _prefect_api_base()
    try:
        running = _post_json(
            f"{base}/flow_runs/filter",
            {"flow_runs": {"state": {"type": {"any_": ["RUNNING"]}}}, "limit": 200},
        ) or []
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("无法查询遗留 RUNNING flow run（跳过收尾）: %s", exc)
        return

    if not running:
        return

    stale = [f for f in running if _started_before_this_process(f)]
    skipped = len(running) - len(stale)
    if skipped:
        logger.info("跳过 %d 个本进程启动后才开始的 RUNNING flow run（是活任务，不能收尾）", skipped)
    if not stale:
        return

    logger.warning("发现 %d 个上次 worker 残留的 RUNNING flow run，收尾以释放并发槽", len(stale))
    for flow_run in stale:
        flow_run_id = flow_run.get("id")
        if not flow_run_id:
            continue
        try:
            _post_json(
                f"{base}/flow_runs/{flow_run_id}/set_state",
                {
                    "state": {
                        "type": "CRASHED",
                        "name": "Crashed",
                        "message": "worker 重启导致子进程被杀；启动时收尾以释放 work queue 并发槽",
                    },
                    "force": True,
                },
            )
            logger.warning("已收尾遗留 flow run %s (%s)", flow_run_id, flow_run.get("name"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("收尾 flow run %s 失败: %s", flow_run_id, exc)


def main() -> None:
    _pin_application_revision()
    subprocess.run(
        [sys.executable, "-m", "prefect", "work-pool", "create", "--type", "process", "ashare-local"],
        check=False,
    )
    # 防死锁：推荐子 run 走独立队列 + pool 上限放宽（见 deployments.RECOMMENDATION_WORK_QUEUE）
    subprocess.run(
        [sys.executable, "-m", "prefect", "work-queue", "create", RECOMMENDATION_WORK_QUEUE, "--pool", "ashare-local"],
        check=False,
    )
    subprocess.run(
        [sys.executable, "-m", "prefect", "work-pool", "set-concurrency-limit", "ashare-local", str(WORK_POOL_CONCURRENCY_LIMIT)],
        check=False,
    )
    _reap_orphan_running_flow_runs()
    schedule_all_enabled_tasks()
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "prefect", "worker", "start", "--pool", "ashare-local"])
    )


if __name__ == "__main__":
    main()

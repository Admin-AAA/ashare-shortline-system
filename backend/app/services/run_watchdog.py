"""失联运行巡检：每 5 分钟回收心跳超时的推荐 run，并把排队超时的 run 重新派发。

原先由 Prefect stale-run-recovery flow 承担，每 5 分钟拉起一个子进程（import 0.86s /
RSS 114MB，实测 288 次/天），而实际工作只是一条带 SKIP LOCKED 的查询。API 进程本来
就常驻，启动时也执行同一段回收逻辑，改为进程内 asyncio 定时任务：频次不变，不再产生
子进程，Prefect worker 挂掉时仍然生效。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL_SECONDS = 300.0


def recover_once() -> int:
    from app.db.session import SessionLocal
    from app.services.workbench_service import recover_interrupted_runs

    db = SessionLocal()
    try:
        recovered = recover_interrupted_runs(db)
        if recovered:
            logger.warning("周期巡检回收 %d 个失联运行", recovered)
        return recovered
    finally:
        db.close()


async def watchdog_loop(interval: float = WATCHDOG_INTERVAL_SECONDS) -> None:
    """常驻巡检。单次失败只记日志，下一轮继续；否则一次数据库抖动会让回收永久停摆。"""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(recover_once)
        except Exception:  # noqa: BLE001 - 常驻任务不能因单次失败退出
            logger.exception("周期巡检回收失联运行失败")

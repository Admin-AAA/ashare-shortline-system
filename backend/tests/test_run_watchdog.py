"""进程内失联运行巡检的循环行为（不依赖数据库）。"""
from __future__ import annotations

import asyncio

from app.services import run_watchdog


def _run_loop(seconds: float) -> None:
    async def _run() -> None:
        task = asyncio.create_task(run_watchdog.watchdog_loop(interval=0.01))
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


def test_watchdog_loop_calls_recover_on_every_interval(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(run_watchdog, "recover_once", lambda: calls.append(1) or 0)

    _run_loop(0.08)

    assert len(calls) >= 2


def test_watchdog_loop_keeps_running_after_failure(monkeypatch) -> None:
    """单次数据库抖动不能让回收永久停摆。"""
    calls: list[int] = []

    def _boom() -> int:
        calls.append(1)
        raise RuntimeError("db down")

    monkeypatch.setattr(run_watchdog, "recover_once", _boom)

    _run_loop(0.08)

    assert len(calls) >= 2

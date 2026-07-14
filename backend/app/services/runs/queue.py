"""推荐运行的进程外调度器。

该模块拥有进程内排队、单 worker 生命周期和子进程派发逻辑；调用方只注入
数据库会话工厂及运行状态转换回调，避免运行域反向依赖 ``workbench_service``。
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RunItem = tuple[str, dict[str, Any]]
SessionFactory = Callable[[], Any]
MarkRunning = Callable[[Any, str], None]
MarkFailed = Callable[[Any, str, Exception], None]


def queued_run_item(run_id: str, payload: dict[str, Any]) -> RunItem:
    """构造防御性复制的队列项，避免调用方后续修改请求参数。"""
    return str(run_id), dict(payload)


def spawn_run_subprocess(run_id: str) -> None:
    """在独立进程启动单次推荐运行，隔离 Web 服务重启与运行生命周期。"""
    backend_root = Path(__file__).resolve().parents[3]
    script_path = backend_root / "scripts" / "run_workbench_subprocess.py"
    log_dir = backend_root / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"run_{run_id}.log"

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{backend_root}{os.pathsep}{existing_pythonpath}".rstrip(os.pathsep)

    # Popen 已复制 stdout 文件描述符；with 块关闭父进程侧描述符，避免每次派发泄漏句柄。
    with log_path.open("a", encoding="utf-8") as log_file:
        subprocess.Popen(
            [sys.executable, str(script_path), str(run_id)],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            env=env,
        )


class BackgroundRunDispatcher:
    """单 worker 的运行派发器，状态持久化经回调由编排层注入。"""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        mark_running: MarkRunning,
        mark_failed: MarkFailed,
        spawn: Callable[[str], None] = spawn_run_subprocess,
    ) -> None:
        self._session_factory = session_factory
        self._mark_running = mark_running
        self._mark_failed = mark_failed
        self._spawn = spawn
        self._queue: queue.Queue[RunItem] = queue.Queue()
        self._lock = threading.Lock()
        self._worker_started = False

    def enqueue(self, run_id: str, payload: dict[str, Any]) -> None:
        self._queue.put(queued_run_item(run_id, payload))
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker_started:
            return
        with self._lock:
            if self._worker_started:
                return
            worker = threading.Thread(target=self._run, name="ashare-run-worker", daemon=True)
            worker.start()
            self._worker_started = True

    def _run(self) -> None:
        while True:
            run_id, _payload = self._queue.get()
            db = self._session_factory()
            try:
                self._mark_running(db, run_id)
                self._spawn(run_id)
            except Exception as exc:
                logger.exception("background run failed to spawn: %s", run_id)
                self._mark_failed(db, run_id, exc)
            finally:
                db.close()
                self._queue.task_done()

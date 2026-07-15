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

RunItem = str
SessionFactory = Callable[[], Any]
MarkRunning = Callable[[Any, str], bool]
MarkFailed = Callable[[Any, str, Exception], None]


def queued_run_item(run_id: str) -> RunItem:
    """构造只包含持久化身份的队列项。

    Canonical request JSON is durable state, not an in-process message.  Keeping the
    queue item to ``run_id`` prevents a stale payload copy from crossing the commit,
    thread, or subprocess boundary.
    """
    value = str(run_id).strip()
    if not value:
        raise ValueError("run_id 不能为空")
    return value


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

    def enqueue(self, run_id: str) -> None:
        """Queue only the durable run identity; the worker reloads its request."""
        self._queue.put(queued_run_item(run_id))
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

    def dispatch_one(self, run_id: str) -> None:
        """Commit the dispatch transition before spawning exactly one worker.

        The child process must never observe a pre-commit lifecycle row.  In
        particular, it needs the persisted ``running`` transition to acquire its
        lease, while spawn failures still retain a committed row that the state
        machine can move to stable ``failed``.
        """
        durable_run_id = queued_run_item(run_id)
        db = self._session_factory()
        try:
            can_spawn = self._mark_running(db, durable_run_id)
            # A concurrent cancel/CAS conflict means this worker no longer owns
            # the run.  Never spawn a child that could resurrect a terminal row.
            if can_spawn is False:
                db.rollback()
                logger.info("run %s was not claimed for dispatch; skipping spawn", durable_run_id)
                return

            # This is the process boundary: canonical request + queued row were
            # committed by the caller, and the dispatcher transition is committed
            # before Popen can let the worker acquire its lease.
            db.commit()
            self._spawn(durable_run_id)
        except Exception as exc:
            # Do not leave a partially flushed dispatcher mutation in this session
            # before asking the injected lifecycle handler to persist failure.
            db.rollback()
            logger.exception("background run failed to spawn: %s", durable_run_id)
            self._mark_failed(db, durable_run_id, exc)
        finally:
            db.close()

    def _run(self) -> None:
        while True:
            run_id = self._queue.get()
            try:
                self.dispatch_one(run_id)
            finally:
                self._queue.task_done()

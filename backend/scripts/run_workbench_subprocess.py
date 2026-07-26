#!/usr/bin/env python3
"""独立子进程执行 workbench run。

由 workbench_service 启动，在后端服务重启/重载后继续执行推荐任务，
避免 daemon 线程随主进程退出而中断。
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BASE_DIR / ".env")

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import RecommendationRun  # noqa: E402
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository  # noqa: E402
from app.services.runs.pipeline import run_staged_pipeline  # noqa: E402


async def _run(run_id: str) -> None:
    db = SessionLocal()
    try:
        # Repository loading is the subprocess cutover boundary.  It parses and
        # upgrades the persisted canonical request; params_json is never consulted.
        record = SQLAlchemyRunRepository(db).get_run(run_id)
        if record is None:
            raise RuntimeError(f"Run {run_id} not found or has no valid canonical request")
        await run_staged_pipeline(db, run_id=run_id, request=record.request)
    finally:
        db.close()


def _mark_failed(run_id: str, exc: BaseException) -> None:
    try:
        db = SessionLocal()
        try:
            run = db.get(RecommendationRun, run_id)
            if run is None:
                return
            if run.status not in {"queued", "running"}:
                return
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            repository = SQLAlchemyRunRepository(db)
            managed = repository.get_run(run_id)
            if managed is not None:
                from app.services.runs.coordinator import LegacyWorkbenchRunCoordinator

                LegacyWorkbenchRunCoordinator(repository).fail(run_id, now=now, message=str(exc))
                db.commit()
                return
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = now
            run.progress_phase = "failed"
            run.progress_message = str(exc)
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: run_workbench_subprocess.py <run_id>", file=sys.stderr)
        sys.exit(1)
    # Force line-buffered stdout so subprocess logs are visible in real time
    # (Python defaults to full buffering when stdout is not a tty).
    sys.stdout.reconfigure(line_buffering=True)
    run_id = sys.argv[1]
    try:
        asyncio.run(_run(run_id))
    except Exception as exc:
        _mark_failed(run_id, exc)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

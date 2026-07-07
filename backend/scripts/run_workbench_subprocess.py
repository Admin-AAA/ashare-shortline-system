#!/usr/bin/env python3
"""独立子进程执行 workbench run。

由 workbench_service 启动，在后端服务重启/重载后继续执行推荐任务，
避免 daemon 线程随主进程退出而中断。
"""
from __future__ import annotations

import asyncio
import json
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
from app.services.workbench_service import run_workbench  # noqa: E402


async def _run(run_id: str) -> None:
    db = SessionLocal()
    try:
        run_record = db.get(RecommendationRun, run_id)
        if run_record is None:
            raise RuntimeError(f"Run {run_id} not found")
        payload = json.loads(run_record.params_json or "{}")
        await run_workbench(db, payload, existing_run_id=run_id)
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
    run_id = sys.argv[1]
    try:
        asyncio.run(_run(run_id))
    except Exception as exc:
        _mark_failed(run_id, exc)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

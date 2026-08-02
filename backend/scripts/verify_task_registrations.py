"""验证调度任务注册接线（P5.2）：幂等注册后 scheduled_tasks 表包含关键任务。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import ScheduledTask  # noqa: E402
from app.services.scheduler_service import ensure_scheduled_task_registrations  # noqa: E402

KEY_TASKS = {
    "post_sync_formal_recommendation": "盘后流水线",
    "strategy_exposure_run": "策略曝光补跑",
    "intraday_lock_create_monitors": "锁利建监控",
    "intraday_lock_settle_signals": "锁利盘后回填",
    "formal_run_safety_net": "正式推荐安全网",
}


def main() -> int:
    ensure_scheduled_task_registrations()
    db = SessionLocal()
    try:
        ok = True
        for task_id, label in KEY_TASKS.items():
            row = db.get(ScheduledTask, task_id)
            status = "OK" if row is not None and row.enabled else "MISSING/DISABLED"
            if status != "OK":
                ok = False
            print(f"{label} ({task_id}): {status} cron={row.cron_expression if row else '-'}")
        return 0 if ok else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

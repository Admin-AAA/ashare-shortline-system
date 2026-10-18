"""Exit 0 if no recommendation run / scheduled task / Prefect flow is active, 1 if busy, 2 if unknown.

Used by safe_backend_restart.sh so the forced launchd restarts never kill a run.
"""
from __future__ import annotations

import json
import sys
import urllib.request

from sqlalchemy import text

TERMINAL_RUN = ("completed", "failed", "interrupted", "cancelled")
TERMINAL_TASK = ("completed", "failed", "interrupted", "cancelled", "skipped", "partial_failed", "partial_completed")


def main() -> int:
    busy: list[str] = []
    try:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            runs = db.execute(text(
                "select count(*) from recommendation_runs where status not in ('" + "','".join(TERMINAL_RUN) + "') "
                "and coalesce(heartbeat_at, started_at) > utc_timestamp() - interval 30 minute"
            )).scalar()
            tasks = db.execute(text(
                "select count(*) from scheduled_task_runs where status not in ('" + "','".join(TERMINAL_TASK) + "') "
                "and started_at > utc_timestamp() - interval 6 hour"
            )).scalar()
        finally:
            db.close()
        if runs:
            busy.append(f"recommendation_runs={runs}")
        if tasks:
            busy.append(f"scheduled_task_runs={tasks}")
    except Exception as exc:  # noqa: BLE001 - reported as unknown (exit 2)
        print(f"db check failed: {exc}")
        return 2
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:4200/api/flow_runs/filter",
            data=json.dumps({"flow_runs": {"state": {"type": {"any_": ["RUNNING", "PENDING", "CANCELLING"]}}}, "limit": 50}).encode(),
            headers={"content-type": "application/json"},
        )
        flows = json.load(urllib.request.urlopen(req, timeout=10))
        if flows:
            busy.append("prefect=" + ",".join(f.get("name", "?") for f in flows))
    except Exception as exc:  # noqa: BLE001 - reported as unknown (exit 2)
        print(f"prefect check failed: {exc}")
        return 2
    if busy:
        print("busy: " + "; ".join(busy))
        return 1
    print("idle")
    return 0


if __name__ == "__main__":
    sys.exit(main())

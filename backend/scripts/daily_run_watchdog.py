"""Independent watchdog for the 17:35 post_sync_formal_recommendation run.

Runs from its own launchd agent (not Prefect), so it still fires when the
Prefect server/worker are down (2026-10-13~15 silent outage). Mon-Fri: if no
ScheduledTaskRun for the task started since 17:00 today (Asia/Shanghai), or Prefect is
unreachable, emit ``[ALERT:DAILY_RUN_MISSING]`` / ``[ALERT:PREFECT_DOWN]`` to
backend/logs/app.log. Exit code 2 when an alert fired.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import urllib.request
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.alerts import emit_alert
from app.core.logger import setup_logging

TASK_ID = "post_sync_formal_recommendation"
CST = ZoneInfo("Asia/Shanghai")
PREFECT_HEALTH_URL = "http://127.0.0.1:4200/api/health"


def _prefect_up(url: str = PREFECT_HEALTH_URL) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def started_today_count(db, now_cst: dt.datetime) -> int:
    """DB stores naive UTC; count runs started since today 17:00 CST.

    Window starts at 17:00 (not midnight) so a morning catch-up of the previous
    day's missed run doesn't mask today's missing 17:35 run.
    """
    day_start_cst = now_cst.replace(hour=17, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_cst.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return int(
        db.execute(
            text("SELECT COUNT(*) FROM scheduled_task_runs WHERE task_id=:t AND started_at >= :s"),
            {"t": TASK_ID, "s": day_start_utc},
        ).scalar()
        or 0
    )


def evaluate(now_cst: dt.datetime, run_count: int, prefect_up: bool) -> list[tuple[str, str]]:
    alerts: list[tuple[str, str]] = []
    if now_cst.weekday() >= 5:
        return alerts
    day = now_cst.date().isoformat()
    if run_count == 0:
        alerts.append(("DAILY_RUN_MISSING", f"{TASK_ID} 今日({day}) 截至 {now_cst:%H:%M} CST 未启动（17:35 cron），调度可能静默中断"))
    if not prefect_up:
        alerts.append(("PREFECT_DOWN", f"Prefect API 不可达 {PREFECT_HEALTH_URL}（{day} {now_cst:%H:%M} CST）"))
    return alerts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print only, do not log alerts")
    args = parser.parse_args(argv)
    setup_logging()
    from app.db.session import SessionLocal

    now_cst = dt.datetime.now(CST)
    db = SessionLocal()
    try:
        count = started_today_count(db, now_cst)
    finally:
        db.close()
    up = _prefect_up()
    alerts = evaluate(now_cst, count, up)
    print(f"watchdog {now_cst:%Y-%m-%d %H:%M} CST runs_today={count} prefect_up={up} alerts={len(alerts)}")
    for kind, msg in alerts:
        print(f"[ALERT:{kind}] {msg}")
        if not args.dry_run:
            emit_alert(kind, now_cst.date().isoformat(), msg)
    return 2 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())

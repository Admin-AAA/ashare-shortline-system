"""Independent watchdog for the 17:35 post_sync_formal_recommendation run.

Runs from its own launchd agent (not Prefect), so it still fires when the
Prefect server/worker are down (2026-10-13~15 silent outage). Mon-Fri: if no
ScheduledTaskRun for the task started since 17:00 today (Asia/Shanghai), or Prefect is
unreachable, or every run today failed, emit ``[ALERT:DAILY_RUN_MISSING]`` /
``[ALERT:DAILY_RUN_FAILED]`` / ``[ALERT:PREFECT_DOWN]`` to
backend/logs/app.log. Exit code 2 when an alert fired.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.alerts import emit_alert
from app.core.logger import setup_logging

TASK_ID = "post_sync_formal_recommendation"
CST = ZoneInfo("Asia/Shanghai")
PREFECT_HEALTH_URL = "http://127.0.0.1:4200/api/health"
BACKEND_BASE_URL = "http://127.0.0.1:8765"
# 2026-11-17：watchdog 此前只告警不修复，17:35 漏跑后当天数据一整天不入库，
# 数据健康页直接 blocked。改为自动补跑一次，用日期 marker 做幂等，避免同一天
# 重复触发（launchd 每天 18:30 只跑一次，但手动重跑/补跑也要安全）。
AUTORETRY_STATE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "runtime" / "daily_run_watchdog_autoretry.json"
)


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


def status_counts_today(db, now_cst: dt.datetime) -> dict[str, int]:
    """Per-status count of today's (since 17:00 CST) runs of TASK_ID."""
    day_start_cst = now_cst.replace(hour=17, minute=0, second=0, microsecond=0)
    day_start_utc = day_start_cst.astimezone(dt.timezone.utc).replace(tzinfo=None)
    rows = db.execute(
        text(
            "SELECT status, COUNT(*) FROM scheduled_task_runs "
            "WHERE task_id=:t AND started_at >= :s GROUP BY status"
        ),
        {"t": TASK_ID, "s": day_start_utc},
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def _already_retried(day: str) -> bool:
    """同一交易日最多自动补跑一次，避免重复触发全量同步。"""
    try:
        state = json.loads(AUTORETRY_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(state, dict) and state.get("date") == day


def _remember_retry(day: str, run_id: str) -> None:
    AUTORETRY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTORETRY_STATE_PATH.write_text(
        json.dumps(
            {"date": day, "run_id": run_id, "at": dt.datetime.now(CST).isoformat()},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def trigger_catch_up(task_id: str = TASK_ID, timeout: float = 15.0) -> tuple[bool, str]:
    """经后端 API 补跑一次盘后流水线。

    不直接调 Prefect：watchdog 由独立 launchd agent 启动，plist 里没有
    PREFECT_API_URL，直接 run_deployment 会拉起临时 server 抢同一个 SQLite
    （见 scripts/launchd/README.md）。后端进程已固定连常驻 server，由它触发最稳。
    补跑本身按交易日/策略幂等，不会重复出票。
    """
    req = urllib.request.Request(
        f"{BACKEND_BASE_URL}/api/admin/tasks/{task_id}/run", data=b"", method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001 - 补跑失败不能压掉原告警
        return False, f"{type(exc).__name__}: {exc}"
    if not payload.get("ok"):
        return False, str(payload)[:200]
    return True, str(payload.get("orchestration_run_id") or "")


def evaluate(
    now_cst: dt.datetime,
    run_count: int,
    prefect_up: bool,
    status_counts: dict[str, int] | None = None,
) -> list[tuple[str, str]]:
    alerts: list[tuple[str, str]] = []
    if now_cst.weekday() >= 5:
        return alerts
    day = now_cst.date().isoformat()
    if run_count == 0:
        alerts.append(("DAILY_RUN_MISSING", f"{TASK_ID} 今日({day}) 截至 {now_cst:%H:%M} CST 未启动（17:35 cron），调度可能静默中断"))
    elif status_counts is not None:
        # 2026-10-15：17:35 run 以 KeyError 失败，但 runs_today=1 → 旧 watchdog alerts=0 漏报。
        ok = status_counts.get("completed", 0) + status_counts.get("running", 0)
        if ok == 0:
            alerts.append((
                "DAILY_RUN_FAILED",
                f"{TASK_ID} 今日({day}) 已启动 {run_count} 次但无 completed/running：{status_counts}，正式推荐未出票",
            ))
    if not prefect_up:
        alerts.append(("PREFECT_DOWN", f"Prefect API 不可达 {PREFECT_HEALTH_URL}（{day} {now_cst:%H:%M} CST）"))
    return alerts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print only, do not log alerts or trigger catch-up")
    parser.add_argument("--no-auto-retry", action="store_true", help="只告警，不自动补跑（诊断用）")
    args = parser.parse_args(argv)
    setup_logging()
    from app.db.session import SessionLocal

    now_cst = dt.datetime.now(CST)
    db = SessionLocal()
    try:
        count = started_today_count(db, now_cst)
        statuses = status_counts_today(db, now_cst)
    finally:
        db.close()
    up = _prefect_up()
    alerts = evaluate(now_cst, count, up, statuses)
    day = now_cst.date().isoformat()

    # 漏跑不能只告警：当天库表会一直停在上一交易日，数据健康页 blocked、
    # 推荐闸门关闭，直到有人手工补。补跑一次并留告警；同日 marker 保证不重复。
    auto_retry = "n/a"
    needs_retry = any(k in ("DAILY_RUN_MISSING", "DAILY_RUN_FAILED") for k, _ in alerts)
    if needs_retry and up and not args.no_auto_retry:
        if _already_retried(day):
            auto_retry = "skipped: 今日已自动补跑过"
        elif args.dry_run:
            auto_retry = "would trigger (dry-run)"
        else:
            ok, detail = trigger_catch_up()
            auto_retry = f"triggered run={detail}" if ok else f"failed: {detail}"
            if ok:
                _remember_retry(day, detail)
                emit_alert(
                    "DAILY_RUN_AUTORETRY", day,
                    f"{TASK_ID} 漏跑，watchdog 已自动补跑一次（run={detail}）；"
                    "请核对当日盘后数据与健康分是否恢复",
                )

    print(f"watchdog {now_cst:%Y-%m-%d %H:%M} CST runs_today={count} statuses={statuses} "
          f"prefect_up={up} alerts={len(alerts)} auto_retry={auto_retry}")
    for kind, msg in alerts:
        print(f"[ALERT:{kind}] {msg}")
        if not args.dry_run:
            emit_alert(kind, day, msg)
    return 2 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())

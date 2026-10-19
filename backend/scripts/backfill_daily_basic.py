"""回填 daily_basic（换手率/PE/市值）——经统一网关，按交易日幂等覆盖。

守卫：research: 熔断命名空间；盘中 9:15–15:05 与日更窗口 16:35–18:35 拒绝运行；
任一上游当日额度用到 60%（或报日限）即停。

  PYTHONPATH=. .venv/bin/python scripts/backfill_daily_basic.py --start 2026-10-12 --end 2026-10-16
"""
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ASHARE_BREAKER_NAMESPACE", "research")

import argparse  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.core.provider_quota import BACKFILL_BUDGET_FRACTION, backfill_budget_check, flush  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.daily_basic_ingest import check_daily_basic_staleness, ingest_daily_basic  # noqa: E402

PROVIDERS = ("promax", "quantdata")
BLOCKED_WINDOWS = ((time(9, 15), time(15, 5), "盘中"), (time(16, 35), time(18, 35), "日更窗口"))


def blocked_window(now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone(timedelta(hours=8)))
    if now.weekday() >= 5:
        return None
    t = now.time()
    for lo, hi, name in BLOCKED_WINDOWS:
        if lo <= t < hi:
            return name
    return None


def trade_dates(db, start: str, end: str) -> list[str]:
    rows = db.execute(text(
        "SELECT DISTINCT trade_date FROM daily_kbars WHERE trade_date >= :s AND trade_date <= :e "
        "ORDER BY trade_date"), {"s": start, "e": end}).scalars().all()
    return [str(r) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget-fraction", type=float, default=BACKFILL_BUDGET_FRACTION)
    ap.add_argument("--ignore-window", action="store_true", help="仅测试用")
    args = ap.parse_args()
    with SessionLocal() as db:
        dates = trade_dates(db, args.start, args.end)
        print("dates:", dates, flush=True)
        if args.dry_run:
            return
        for i, d in enumerate(dates):
            w = None if args.ignore_window else blocked_window()
            if w:
                print(f"STOP before {d}: 处于{w}，剩余未补 {dates[i:]}", flush=True)
                break
            ok, reason = backfill_budget_check(PROVIDERS, args.budget_fraction)
            if not ok:
                print(f"STOP before {d}: {reason}; 剩余未补 {dates[i:]}", flush=True)
                break
            try:
                out = ingest_daily_basic(db, d)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                print(f"STOP at {d}: {type(exc).__name__}: {exc}; 剩余未补 {dates[i:]}", flush=True)
                break
            print(d, out, flush=True)
        print("staleness:", check_daily_basic_staleness(db), flush=True)
    flush()


if __name__ == "__main__":
    main()

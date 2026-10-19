"""Re-pull cyq_perf for given trade dates and overwrite chip_* (writes NULL-free real values only).

  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py 2026-09-22 2026-09-23 ...
  PYTHONPATH=. .venv/bin/python scripts/backfill_chip_dates.py --zero-days-since 2026-09-01
"""
from __future__ import annotations

import argparse

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.data_gap_remediation_service import _backfill_chip_date


def zero_days(db, since: str) -> list[str]:
    rows = db.execute(text(
        "SELECT trade_date, COUNT(*) n, SUM(chip_cost_50 IS NULL OR chip_cost_50 = 0) z FROM daily_kbars "
        "WHERE trade_date >= :s GROUP BY trade_date HAVING z > 0.05 * n ORDER BY trade_date"), {"s": since}).all()
    return [str(r[0]) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--zero-days-since")
    args = ap.parse_args()
    with SessionLocal() as db:
        dates = list(args.dates) or (zero_days(db, args.zero_days_since) if args.zero_days_since else [])
        print("dates:", dates, flush=True)
        for d in dates:
            out = _backfill_chip_date(db, d)
            # whatever cyq_perf did not cover stays missing: 0 -> NULL (never 0-as-missing)
            res = db.execute(text(
                "UPDATE daily_kbars SET chip_cost_15=NULL, chip_cost_50=NULL, chip_cost_85=NULL, "
                "chip_weight_avg=NULL, chip_winner_rate=NULL, updated_at=UTC_TIMESTAMP() "
                "WHERE trade_date=:d AND chip_cost_50 = 0"), {"d": d})
            db.commit()
            print(d, out, "zero->NULL:", res.rowcount, flush=True)


if __name__ == "__main__":
    main()

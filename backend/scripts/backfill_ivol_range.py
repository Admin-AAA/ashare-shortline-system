"""Backfill ivol20 + ivol_dn20 for a date range via app.services.ivol_fill (writes only changed rows).

  PYTHONPATH=. .venv/bin/python scripts/backfill_ivol_range.py --start 2026-09-14 --end 2026-10-19
"""
from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from app.services.ivol_fill import check_ivol_coverage, fill_ivol_range


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()
    with SessionLocal() as db:
        for metric in ("ivol20", "ivol_dn20"):
            n = fill_ivol_range(db, args.start, args.end, metric)
            print(f"{metric} {args.start}..{args.end} updated={n}", flush=True)
            print(check_ivol_coverage(db, args.end, metric), flush=True)


if __name__ == "__main__":
    main()

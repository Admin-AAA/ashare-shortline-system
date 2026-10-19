"""Backfill daily_kbars.adj_factor where it is NULL or 0 (2026-10-19).

Root cause: the 17:35 post-close writer took adj_factor from stk_factor, which is often not yet published
at that time; tushare_client._f() turned the missing value into 0.0. Source: the adj_factor API via the
data gateway (get_pro()). Only rows with NULL/0 adj_factor are touched; only the adj_factor column changes.

Usage: PYTHONPATH=. .venv/bin/python scripts/backfill_adj_factor.py [--since 2025-01-01] [--dry-run]
"""
from __future__ import annotations

import argparse
import time

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.tushare_client import get_pro


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2000-01-01")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    dates = [r[0] for r in db.execute(text(
        "SELECT trade_date FROM daily_kbars WHERE trade_date >= :s AND (adj_factor IS NULL OR adj_factor = 0) "
        "GROUP BY trade_date ORDER BY trade_date"), {"s": args.since})]
    print(f"dates needing backfill: {len(dates)}", flush=True)
    pro = get_pro()
    total = 0
    for d in dates:
        try:
            df = pro.adj_factor(trade_date=d.replace("-", ""))
        except Exception as exc:  # noqa: BLE001
            print(f"{d}: fetch failed {exc}", flush=True)
            continue
        rows = [
            {"c": str(ts).split(".")[0], "d": d, "a": float(a)}
            for ts, a in zip(df.get("ts_code", []), df.get("adj_factor", []))
            if a is not None and a == a and float(a) > 0
        ]
        if not args.dry_run and rows:
            res = db.execute(text(
                "UPDATE daily_kbars SET adj_factor = :a, updated_at = UTC_TIMESTAMP() WHERE code = :c AND trade_date = :d "
                "AND (adj_factor IS NULL OR adj_factor = 0)"), rows)
            db.commit()
            total += res.rowcount or 0
        print(f"{d}: source rows {len(rows)} updated_total {total}", flush=True)
        time.sleep(0.2)
    left = db.execute(text("SELECT COUNT(*) FROM daily_kbars WHERE trade_date >= :s AND (adj_factor IS NULL OR adj_factor = 0)"), {"s": args.since}).scalar()
    print(f"done. updated {total}, still NULL/0: {left}")


if __name__ == "__main__":
    main()

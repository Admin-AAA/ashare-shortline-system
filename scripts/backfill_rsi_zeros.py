"""Backfill RSI for daily_kbars where RSI=0 (indicator not computed).

Root cause: backfill_indicators was not run for 2026-07-02 and 2026-07-07,
leaving all 5517 stocks with rsi_6=rsi_12=rsi_24=0.0. Feature_builder's guard
cleaned rsi_12→50 but pre-guard snapshots had rsi_6=0 leaking into
feature_snapshot_json.

This script:
1. For each affected trade_date, loads each stock's bar history
2. Computes RSI from closes series using indicator_computer.rsi()
3. Updates daily_kbars SET rsi_6/rsi_12/rsi_24

Usage: python scripts/backfill_rsi_zeros.py [--dry-run]
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import create_engine, text
from app.services.indicator_computer import rsi as compute_rsi

DB_URL = os.environ.get("ASHARE_DATABASE_URL", "")

AFFECTED_DATES = ["2026-07-02", "2026-07-07"]

# Need at least this many bars before the target date for RSI to be valid
MIN_BARS_FOR_RSI = 25


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print counts without updating")
    args = parser.parse_args()

    engine = create_engine(DB_URL)
    updated = 0
    skipped = 0

    with engine.connect() as conn:
        for trade_date in AFFECTED_DATES:
            # Find all codes with RSI=0 on this date
            zero_rows = conn.execute(text("""
                SELECT code FROM daily_kbars
                WHERE trade_date = :dt AND (rsi_6 = 0 OR rsi_6 IS NULL)
            """), {"dt": trade_date}).fetchall()

            print(f"{trade_date}: {len(zero_rows)} stocks with RSI=0")

            for (code,) in zero_rows:
                # Load bars up to and including this date, sorted ascending
                bars = conn.execute(text("""
                    SELECT trade_date, close FROM daily_kbars
                    WHERE code = :code AND trade_date <= :dt
                    ORDER BY trade_date ASC
                """), {"code": code, "dt": trade_date}).fetchall()

                if len(bars) < MIN_BARS_FOR_RSI:
                    skipped += 1
                    continue

                closes = [float(b[1]) for b in bars]

                rsi6 = compute_rsi(closes, 6)
                rsi12 = compute_rsi(closes, 12)
                rsi24 = compute_rsi(closes, 24)

                if rsi6 is not None and rsi12 is not None and rsi24 is not None:
                    if not args.dry_run:
                        conn.execute(text("""
                            UPDATE daily_kbars
                            SET rsi_6 = :r6, rsi_12 = :r12, rsi_24 = :r24
                            WHERE code = :code AND trade_date = :dt
                        """), {"r6": round(rsi6, 3), "r12": round(rsi12, 3),
                              "r24": round(rsi24, 3), "code": code, "dt": trade_date})
                    updated += 1
                else:
                    skipped += 1

            if not args.dry_run:
                conn.commit()

            # Verify
            remaining = conn.execute(text("""
                SELECT COUNT(*) FROM daily_kbars
                WHERE trade_date = :dt AND (rsi_6 = 0 OR rsi_6 IS NULL)
            """), {"dt": trade_date}).fetchone()[0]
            print(f"  → updated={updated}, skipped={skipped}, remaining_zero={remaining}")

    print(f"Done. Total updated: {updated}, skipped: {skipped}")


if __name__ == "__main__":
    main()

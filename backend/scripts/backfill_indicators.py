"""
Backfill pre-computed technical indicators for all existing daily bars.

Usage: python scripts/backfill_indicators.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import DailyBar, DailyIndicator
from app.services.indicator_computer import compute_daily_bar_indicators, compute_daily_indicators


def main() -> None:
    db = SessionLocal()
    try:
        # Get all unique codes
        codes = [
            row[0]
            for row in db.execute(
                select(DailyBar.code).distinct().order_by(DailyBar.code)
            ).all()
        ]
        total_codes = len(codes)
        print(f"Found {total_codes} unique stock codes with daily bars.")

        total_bars_updated = 0
        total_indicators_upserted = 0
        start_time = time.perf_counter()

        for idx, code in enumerate(codes):
            # Load all bars for this code sorted by trade_date ascending
            bars = db.scalars(
                select(DailyBar)
                .where(DailyBar.code == code)
                .order_by(DailyBar.trade_date.asc())
            ).all()

            if not bars:
                continue

            # Convert to dicts for indicator computation
            bar_dicts = [
                {
                    "code": bar.code,
                    "trade_date": bar.trade_date,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in bars
            ]

            # Compute indicators
            bar_indicators = compute_daily_bar_indicators(code, bar_dicts)
            daily_indicators = compute_daily_indicators(code, bar_dicts)

            # Update DailyBar columns
            for bar in bars:
                ind = bar_indicators.get(bar.trade_date, {})
                updated = False
                if bar.ma5 is None and ind.get("ma5") is not None:
                    bar.ma5 = ind["ma5"]
                    updated = True
                if bar.ma10 is None and ind.get("ma10") is not None:
                    bar.ma10 = ind["ma10"]
                    updated = True
                if bar.ma20 is None and ind.get("ma20") is not None:
                    bar.ma20 = ind["ma20"]
                    updated = True
                if bar.ma60 is None and ind.get("ma60") is not None:
                    bar.ma60 = ind["ma60"]
                    updated = True
                if bar.volume_ratio_5d is None and ind.get("volume_ratio_5d") is not None:
                    bar.volume_ratio_5d = ind["volume_ratio_5d"]
                    updated = True
                if updated:
                    total_bars_updated += 1

            # Upsert DailyIndicator rows
            for bar in bars:
                ind = daily_indicators.get(bar.trade_date, {})
                if not ind:
                    continue
                existing = db.scalar(
                    select(DailyIndicator).where(
                        DailyIndicator.code == code,
                        DailyIndicator.trade_date == bar.trade_date,
                    )
                )
                if existing is None:
                    db.add(DailyIndicator(code=code, trade_date=bar.trade_date, **ind))
                    total_indicators_upserted += 1
                else:
                    changed = False
                    for key, value in ind.items():
                        if getattr(existing, key) is None and value is not None:
                            setattr(existing, key, value)
                            changed = True
                    if changed:
                        total_indicators_upserted += 1

            # Commit and print progress every 100 codes
            if (idx + 1) % 100 == 0 or (idx + 1) == total_codes:
                db.commit()
                elapsed = time.perf_counter() - start_time
                print(
                    f"  Progress: {idx + 1}/{total_codes} codes | "
                    f"{total_bars_updated} bars updated | "
                    f"{total_indicators_upserted} indicators upserted | "
                    f"{elapsed:.1f}s elapsed"
                )

        elapsed = time.perf_counter() - start_time
        print()
        print(f"Backfill complete in {elapsed:.1f}s.")
        print(f"  Total codes: {total_codes}")
        print(f"  DailyBar rows updated: {total_bars_updated}")
        print(f"  DailyIndicator rows upserted: {total_indicators_upserted}")

    finally:
        db.close()


if __name__ == "__main__":
    main()

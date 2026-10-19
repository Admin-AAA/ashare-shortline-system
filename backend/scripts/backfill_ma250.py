"""Full-history ma250 backfill (unadjusted close, 250-bar SMA, <250 bars -> NULL). See app/services/ma250_fill.py.

Usage: PYTHONPATH=. .venv/bin/python scripts/backfill_ma250.py [--since YYYY-MM-DD]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.services.ma250_fill import fill_ma250  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    a = ap.parse_args()
    print(fill_ma250(SessionLocal(), since=a.since), flush=True)

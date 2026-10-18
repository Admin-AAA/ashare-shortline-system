"""Backfill stk_factor-derived columns (MACD/KDJ/RSI/BOLL/CCI) and adj_factor in daily_kbars (2026-10-19).

Root cause: the 17:35 post-close writer read stk_factor before Tushare published it, and _f() turned the
missing values into 0.0 (214 trading days, whole market). Rules:
- only rows whose column is NULL or 0 are touched; existing non-zero values are never overwritten;
- value from stk_factor(trade_date) via the data gateway; if the source has no value -> NULL (never 0);
- adj_factor: stk_factor.adj_factor, else derived close_hfq/close (provenance logged to
  data/backups/adj_factor_provenance_<date>.csv);
- updated_at is bumped (UTC) on changed rows so outcome auto re-projection picks them up.

Usage: PYTHONPATH=. .venv/bin/python scripts/backfill_stk_factor_fields.py [--fields factors|adj] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.tushare_client import get_pro

FACTOR_COLS = ("macd_dif", "macd_dea", "macd_bar", "kdj_k", "kdj_d", "kdj_j", "rsi_6", "rsi_12", "rsi_24",
               "boll_upper", "boll_mid", "boll_lower", "cci")
SOURCE_COL = {"macd_bar": "macd"}
PROVENANCE = Path(__file__).resolve().parent.parent / "data" / "backups" / "adj_factor_provenance_20261019.csv"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _factor_dates(db) -> list[str]:
    return [r[0] for r in db.execute(text(
        "SELECT trade_date FROM daily_kbars GROUP BY trade_date "
        "HAVING SUM(kdj_k = 0 OR cci = 0 OR boll_mid = 0) > COUNT(*) * 0.5 ORDER BY trade_date"))]


def _adj_dates(db) -> list[str]:
    return [r[0] for r in db.execute(text(
        "SELECT trade_date FROM daily_kbars WHERE adj_factor IS NULL OR adj_factor = 0 "
        "GROUP BY trade_date ORDER BY trade_date"))]


def run_factors(db, pro, dry: bool) -> None:
    dates = _factor_dates(db)
    print(f"factor dates: {len(dates)}", flush=True)
    sets = ", ".join(f"{c} = CASE WHEN {c} IS NULL OR {c} = 0 THEN :{c} ELSE {c} END" for c in FACTOR_COLS)
    zero_any = " OR ".join(f"{c} = 0" for c in FACTOR_COLS)
    for d in dates:
        df = pro.stk_factor(trade_date=d.replace("-", ""))
        rows = []
        for rec in (df.to_dict("records") if df is not None and not df.empty else []):
            row = {"code": str(rec.get("ts_code", "")).split(".")[0], "d": d}
            for c in FACTOR_COLS:
                v = _num(rec.get(SOURCE_COL.get(c, c)))
                row[c] = v  # None -> NULL
            rows.append(row)
        changed = 0
        if not dry:
            if rows:
                res = db.execute(text(
                    f"UPDATE daily_kbars SET {sets}, updated_at = UTC_TIMESTAMP() "
                    f"WHERE code = :code AND trade_date = :d AND ({zero_any})"), rows)
                changed = res.rowcount or 0
            # codes absent from the source: zero -> NULL (unavailable, never 0)
            nulls = ", ".join(f"{c} = NULLIF({c}, 0)" for c in FACTOR_COLS)
            res2 = db.execute(text(
                f"UPDATE daily_kbars SET {nulls}, updated_at = UTC_TIMESTAMP() WHERE trade_date = :d AND ({zero_any})"),
                {"d": d})
            db.commit()
            changed += res2.rowcount or 0
        print(f"{d}: source {len(rows)} changed {changed}", flush=True)
        time.sleep(0.2)


def run_adj(db, pro, dry: bool) -> None:
    dates = _adj_dates(db)
    print(f"adj dates: {len(dates)}", flush=True)
    PROVENANCE.parent.mkdir(parents=True, exist_ok=True)
    new = not PROVENANCE.exists()
    with PROVENANCE.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["code", "trade_date", "adj_factor", "source"])
        for d in dates:
            df = pro.stk_factor(trade_date=d.replace("-", ""))
            rows = []
            for rec in (df.to_dict("records") if df is not None and not df.empty else []):
                code = str(rec.get("ts_code", "")).split(".")[0]
                a = _num(rec.get("adj_factor"))
                src = "stk_factor.adj_factor"
                if not a or a <= 0:
                    hfq, close = _num(rec.get("close_hfq")), _num(rec.get("close"))
                    a = round(hfq / close, 4) if hfq and close else None
                    src = "derived:close_hfq/close"
                if a and a > 0:
                    rows.append({"c": code, "d": d, "a": a})
                    w.writerow([code, d, a, src])
            changed = 0
            if rows and not dry:
                res = db.execute(text(
                    "UPDATE daily_kbars SET adj_factor = :a, updated_at = UTC_TIMESTAMP() "
                    "WHERE code = :c AND trade_date = :d AND (adj_factor IS NULL OR adj_factor = 0)"), rows)
                db.commit()
                changed = res.rowcount or 0
            print(f"{d}: source {len(rows)} changed {changed}", flush=True)
            time.sleep(0.2)
    left = db.execute(text("SELECT COUNT(*), COUNT(DISTINCT trade_date) FROM daily_kbars WHERE adj_factor IS NULL OR adj_factor = 0")).one()
    print(f"adj still NULL/0: rows {left[0]} dates {left[1]}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", choices=["factors", "adj"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    db, pro = SessionLocal(), get_pro()
    (run_factors if a.fields == "factors" else run_adj)(db, pro, a.dry_run)
    print("done.", flush=True)


if __name__ == "__main__":
    main()

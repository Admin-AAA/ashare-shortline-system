#!/usr/bin/env python3
"""Backfill limit-up state through S4 and optional index daily data.

The former hand-written limit-up provider/upsert path has been removed.  Every
auxiliary refresh is delegated to ``run_auxiliary_application``; index_daily remains
a separate, non-S4 market-data maintenance operation.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.engine.trading_calendar import normalize_trade_date
from app.models.tables import DailyKbar, IndexDaily
from app.services.auxiliary.application import run_auxiliary_application
from app.services.tushare_client import fetch_index_daily

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_auxiliary")


def backfill_limit_up(db: Session, start_date: str, end_date: str, sleep_seconds: float = 0.0) -> dict[str, int]:
    """Run the frozen limit_up_board descriptor for each locally known trading day."""
    dates = list(
        db.scalars(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
            .distinct()
            .order_by(DailyKbar.trade_date)
        ).all()
    )
    stored = 0
    errors = 0
    for trade_date in dates:
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        try:
            result = run_auxiliary_application(
                db,
                instruments=(),
                trade_date=str(trade_date),
                refresh=True,
                datasets=("limit_up_board",),
            )
            item = result.orchestrated.result_for("limit_up_board")
            if item.errors:
                raise RuntimeError(", ".join(error.message for error in item.errors))
            stored += item.coverage.record_count
            db.commit()
        except Exception as exc:  # noqa: BLE001 - continue with the next trading day
            db.rollback()
            errors += 1
            logger.warning("limit_up_board %s failed: %s", trade_date, str(exc)[:160])
    return {"stored": stored, "errors": errors}


def backfill_index_daily(db: Session, start_date: str, end_date: str) -> dict[str, int]:
    indices = ("000001.SH", "000300.SH", "000905.SH", "000852.SH", "399001.SZ", "399006.SZ")
    stored = 0
    errors = 0
    for ts_code in indices:
        try:
            rows = fetch_index_daily(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            ) or []
            payload = [
                {
                    "ts_code": ts_code,
                    "trade_date": normalize_trade_date(row.get("trade_date")),
                    "close": float(row.get("close") or 0),
                    "pct_chg": float(row.get("pct_chg") or 0),
                    "open": float(row.get("open") or 0),
                    "high": float(row.get("high") or 0),
                    "low": float(row.get("low") or 0),
                    "volume": float(row.get("volume", row.get("vol", 0)) or 0),
                }
                for row in rows
                if normalize_trade_date(row.get("trade_date"))
            ]
            if payload:
                stmt = mysql_insert(IndexDaily).values(payload)
                db.execute(stmt.on_duplicate_key_update(
                    close=stmt.inserted.close,
                    pct_chg=stmt.inserted.pct_chg,
                    open=stmt.inserted.open,
                    high=stmt.inserted.high,
                    low=stmt.inserted.low,
                    volume=stmt.inserted.volume,
                ))
                db.commit()
                stored += len(payload)
        except Exception as exc:  # noqa: BLE001 - retain per-index error isolation
            db.rollback()
            errors += 1
            logger.warning("index_daily %s failed: %s", ts_code, str(exc)[:160])
    return {"stored": stored, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="经 S4 回填涨停板并可选回填指数日线")
    parser.add_argument("--tables", default="all", help="all / limit_up / index_daily（逗号分隔）")
    parser.add_argument("--recent-days", type=int, default=90)
    args = parser.parse_args()
    selected = {"limit_up", "index_daily"} if args.tables == "all" else set(args.tables.split(","))
    start_date = (date.today() - timedelta(days=args.recent_days)).isoformat()
    end_date = date.today().isoformat()

    db = SessionLocal()
    try:
        if "limit_up" in selected:
            logger.info("limit_up_board: %s", backfill_limit_up(db, start_date, end_date))
        if "index_daily" in selected:
            logger.info("index_daily: %s", backfill_index_daily(db, start_date, end_date))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

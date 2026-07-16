#!/usr/bin/env python3
"""Rebuild core market data, then refresh auxiliary data through the S4 boundary.

The script retains its explicit destructive guard for core/state maintenance.  It
never reads or writes legacy event projection tables: immutable events are handled
only by the S4 snapshot composition.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.tables import DailyKbar, Stock, StockQuote
from app.services.auxiliary.application import run_auxiliary_application
from app.services.auxiliary.production import AUXILIARY_DATASET_INVENTORY
from app.services.tushare_client import fetch_daily_kbars, fetch_stock_list
from app.services.validation.safety import assert_destructive_allowed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rebuild")
settings = get_settings()


def clear_all_tables(db: Session) -> None:
    """Clear rebuildable core rows and S4 mutable state rows after the explicit guard.

    Event projections deliberately do not appear here.  Their immutable raw capture
    and active-pointer lifecycle is owned by the S4 application composition.
    """
    core_tables = ("stocks", "daily_kbars", "stock_quotes")
    state_tables = tuple(
        dict.fromkeys(entry.table_name for entry in AUXILIARY_DATASET_INVENTORY if entry.is_state_dataset)
    )
    for table in (*core_tables, *state_tables):
        db.execute(text(f"DELETE FROM `{table}`"))
        logger.info("已清空: %s", table)
    db.commit()


def _parse_csv_date(value: object) -> str:
    raw = str(value).strip().replace("-", "")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else str(value)[:10]


def import_csv_files(db: Session, csv_dir: str) -> tuple[int, int]:
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    stock_count = 0
    kbar_count = 0
    for path in files:
        code = os.path.basename(path).split("_")[0]
        if len(code) != 6 or not code.isdigit():
            continue
        try:
            frame = pd.read_csv(path, dtype={"stock_code": str})
        except Exception as exc:  # noqa: BLE001 - one malformed CSV must not abort the rebuild
            logger.warning("读取 %s 失败: %s", path, exc)
            continue
        frame["trade_date"] = frame["trade_date"].astype(str)
        frame = frame[frame["trade_date"] >= "2024-05-01"]
        if frame.empty:
            continue
        if not db.scalar(select(Stock.code).where(Stock.code == code).limit(1)):
            market = "SH" if code.startswith(("6", "9")) else "SZ"
            db.add(Stock(code=code, name=str(frame.iloc[0].get("stock_code") or code), market=market))
            stock_count += 1
        rows = [
            {
                "code": code,
                "trade_date": _parse_csv_date(row["trade_date"]),
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("close") or 0),
                "volume": float(row.get("vol") or 0),
                "amount": float(row.get("amount") or 0) * 1000,
                "pct_chg": float(row.get("pct_chg") or 0),
                "source": "csv",
            }
            for _, row in frame.iterrows()
        ]
        if rows:
            db.execute(mysql_insert(DailyKbar).values(rows).prefix_with("IGNORE"))
            kbar_count += len(rows)
    db.commit()
    return stock_count, kbar_count


def fill_stock_list(db: Session) -> int:
    rows = fetch_stock_list() or []
    added = 0
    for row in rows:
        code = str(row.get("code") or "")
        if not code or db.scalar(select(Stock.code).where(Stock.code == code).limit(1)):
            continue
        db.add(
            Stock(
                code=code,
                name=str(row.get("name") or code),
                industry=row.get("industry"),
                area=row.get("area"),
                market=row.get("market"),
                list_date=row.get("list_date"),
                is_st="ST" in str(row.get("name") or ""),
            )
        )
        added += 1
    db.commit()
    return added


def fill_kbars_tushare(db: Session, top_n: int) -> int:
    codes = list(db.scalars(select(Stock.code).order_by(Stock.code).limit(max(1, top_n))).all())
    inserted = 0
    for code in codes:
        existing = set(db.scalars(select(DailyKbar.trade_date).where(DailyKbar.code == code)).all())
        rows = [row for row in (fetch_daily_kbars(code, days=250) or []) if row.get("trade_date") not in existing]
        if rows:
            db.execute(mysql_insert(DailyKbar).values(rows).prefix_with("IGNORE"))
            inserted += len(rows)
    db.commit()
    return inserted


def _auxiliary_trade_date(db: Session) -> str:
    value = db.scalar(select(func.max(StockQuote.trade_date))) or db.scalar(select(func.max(DailyKbar.trade_date)))
    if not value:
        raise RuntimeError("没有本地报价或日K，无法运行 S4 auxiliary refresh")
    return str(value)


def _auxiliary_codes(db: Session, trade_date: str, limit: int) -> list[str]:
    rows = db.scalars(
        select(DailyKbar.code)
        .where(DailyKbar.trade_date == trade_date)
        .order_by(DailyKbar.amount.desc(), DailyKbar.code.asc())
        .limit(max(1, limit))
    ).all()
    return [str(code) for code in rows if str(code or "").strip()]


def backfill_aux_all(db: Session, top_n: int = 300) -> None:
    """Route all eighteen frozen S4 datasets through the sole composition boundary."""
    trade_date = _auxiliary_trade_date(db)
    result = run_auxiliary_application(
        db,
        instruments=_auxiliary_codes(db, trade_date, top_n),
        trade_date=trade_date,
        refresh=True,
    )
    db.commit()
    for dataset, audit in result.audit_payload.items():
        logger.info("%s: records=%s errors=%s", dataset, audit["record_count"], list(audit["errors"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="重建核心行情池并经 S4 刷新辅助数据")
    parser.add_argument("--dangerously-clear-data-pool", action="store_true")
    parser.add_argument("--skip-csv", action="store_true")
    parser.add_argument("--skip-tushare-kbars", action="store_true")
    parser.add_argument("--skip-aux", action="store_true")
    parser.add_argument("--csv-dir", default="/Users/wangao/Downloads/用所选项目新建的文件夹")
    parser.add_argument("--tushare-top-n", type=int, default=5200)
    parser.add_argument("--aux-top-n", type=int, default=300)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.dangerously_clear_data_pool:
            assert_destructive_allowed(database_url=settings.database_url, requested=True)
            clear_all_tables(db)
        if not args.skip_csv:
            stock_count, kbar_count = import_csv_files(db, args.csv_dir)
            logger.info("CSV: %d stocks, %d kbars", stock_count, kbar_count)
        if not args.skip_tushare_kbars:
            logger.info("Tushare stocks: %d new", fill_stock_list(db))
            logger.info("Tushare kbars: %d new", fill_kbars_tushare(db, args.tushare_top_n))
        if not args.skip_aux:
            backfill_aux_all(db, args.aux_top_n)
        return 0
    except Exception:
        db.rollback()
        logger.exception("rebuild failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

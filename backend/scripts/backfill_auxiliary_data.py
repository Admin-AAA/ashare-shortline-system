#!/usr/bin/env python3
"""Refresh the frozen S4 auxiliary inventory through its sole application boundary.

This CLI deliberately does not contain provider-specific refreshers or write legacy
projection tables.  The caller owns the SQLAlchemy Session and transaction; the S4
application service owns composition and optional provider acquisition.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import DailyKbar, StockQuote
from app.services.auxiliary.application import run_auxiliary_application
from app.services.auxiliary.production import AUXILIARY_DATASET_BY_KEY, AUXILIARY_DATASET_INVENTORY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_auxiliary")


def _resolve_trade_date(db: Session, requested: str | None) -> str:
    if requested:
        return str(requested)[:10]
    quote_date = db.scalar(select(StockQuote.trade_date).order_by(StockQuote.trade_date.desc()).limit(1))
    if quote_date:
        return str(quote_date)
    kbar_date = db.scalar(select(DailyKbar.trade_date).order_by(DailyKbar.trade_date.desc()).limit(1))
    if kbar_date:
        return str(kbar_date)
    raise RuntimeError("无法从本地报价或日K确定辅助数据交易日；请传入 --trade-date")


def _target_codes(db: Session, trade_date: str, limit: int) -> list[str]:
    rows = db.scalars(
        select(StockQuote.code)
        .where(StockQuote.trade_date == trade_date)
        .order_by(StockQuote.amount_yi.desc(), StockQuote.code.asc())
        .limit(max(1, limit))
    ).all()
    codes = [str(code) for code in rows if str(code or "").strip()]
    if codes:
        return codes
    rows = db.scalars(
        select(DailyKbar.code)
        .where(DailyKbar.trade_date == trade_date)
        .order_by(DailyKbar.amount.desc(), DailyKbar.code.asc())
        .limit(max(1, limit))
    ).all()
    return [str(code) for code in rows if str(code or "").strip()]


def _datasets(values: Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return tuple(entry.key for entry in AUXILIARY_DATASET_INVENTORY)
    requested = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    unknown = sorted(set(requested) - set(AUXILIARY_DATASET_BY_KEY))
    if unknown:
        raise ValueError("未知 S4 auxiliary dataset: " + ", ".join(unknown))
    return requested


def main() -> int:
    parser = argparse.ArgumentParser(description="经 S4 frozen registry 刷新辅助数据")
    parser.add_argument("--trade-date", help="YYYY-MM-DD；默认采用本地最新日期")
    parser.add_argument("--top-n", type=int, default=300, help="逐标的数据集的目标股票数")
    parser.add_argument("--dataset", action="append", dest="datasets", choices=sorted(AUXILIARY_DATASET_BY_KEY))
    args = parser.parse_args()

    db = SessionLocal()
    try:
        trade_date = _resolve_trade_date(db, args.trade_date)
        codes = _target_codes(db, trade_date, args.top_n)
        result = run_auxiliary_application(
            db,
            instruments=codes,
            trade_date=trade_date,
            refresh=True,
            datasets=_datasets(args.datasets),
        )
        db.commit()
        for key, audit in result.audit_payload.items():
            logger.info("%s: records=%s sufficient=%s errors=%s", key, audit["record_count"], audit["coverage_sufficient"], list(audit["errors"]))
        return 0
    except Exception:
        db.rollback()
        logger.exception("S4 auxiliary refresh failed")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

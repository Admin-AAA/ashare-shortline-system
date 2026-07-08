"""修复 index_daily 日期格式，并补齐 limit_up_board 07-08 数据。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.tables import IndexDaily
from app.services.market_pool import refresh_limit_up_board

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_index_limit_up")


def _normalize_date(value: str) -> str:
    """将 20260525 统一为 2026-05-25。"""
    raw = str(value or "").strip()
    if not raw:
        return raw
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]


def fix_index_daily_dates() -> dict[str, int]:
    """修复 index_daily 中 20260525 格式的 trade_date。

    策略：先删除可能已存在的标准格式重复记录，再更新旧格式为标准格式。
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            select(IndexDaily.id, IndexDaily.ts_code, IndexDaily.trade_date)
            .where(func.length(IndexDaily.trade_date) == 8)
        ).all()

        fixed = 0
        skipped = 0
        for row in rows:
            new_date = _normalize_date(row.trade_date)
            if new_date == row.trade_date or not new_date:
                skipped += 1
                continue

            # 如果目标日期已存在，先删除（保留旧记录更新后的数据）
            existing = db.execute(
                select(IndexDaily).where(
                    IndexDaily.ts_code == row.ts_code,
                    IndexDaily.trade_date == new_date,
                )
            ).scalar_one_or_none()

            if existing:
                db.delete(existing)
                db.commit()

            old = db.execute(select(IndexDaily).where(IndexDaily.id == row.id)).scalar_one()
            old.trade_date = new_date
            db.commit()
            fixed += 1

        logger.info("index_daily 日期格式修复完成: fixed=%d skipped=%d", fixed, skipped)
        return {"fixed": fixed, "skipped": skipped}
    finally:
        db.close()


def backfill_limit_up_board(trade_date: str | None = None) -> int:
    """补齐 limit_up_board 指定日期数据。"""
    db = SessionLocal()
    try:
        rows = refresh_limit_up_board(db, trade_date=trade_date)
        db.commit()
        logger.info("limit_up_board 补齐完成: trade_date=%s rows=%d", trade_date, len(rows))
        return len(rows)
    finally:
        db.close()


def backfill_all_index_daily(end_date: str | None = None) -> dict[str, int]:
    """补齐所有默认指数日线的最新数据。"""
    from datetime import date

    from app.services.tushare_client import fetch_index_daily

    targets = ["000001.SH", "000300.SH", "000905.SH", "000852.SH", "399001.SZ", "399006.SZ"]
    end = end_date or date.today().strftime("%Y%m%d")
    counts: dict[str, int] = {}

    db = SessionLocal()
    try:
        for ts_code in targets:
            rows = fetch_index_daily(ts_code=ts_code, start_date="20260101", end_date=end)
            written = 0
            for r in rows:
                existing = db.execute(
                    select(IndexDaily).where(
                        IndexDaily.ts_code == r["ts_code"],
                        IndexDaily.trade_date == r["trade_date"],
                    )
                ).scalar_one_or_none()
                if existing:
                    continue
                db.add(IndexDaily(
                    ts_code=r["ts_code"],
                    trade_date=r["trade_date"],
                    close=r["close"],
                    pct_chg=r["pct_chg"],
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    volume=r["volume"],
                ))
                written += 1
            db.commit()
            counts[ts_code] = written
            logger.info("index_daily %s 补齐完成: %d 行", ts_code, written)
        return counts
    finally:
        db.close()


if __name__ == "__main__":
    index_result = fix_index_daily_dates()
    all_index_result = backfill_all_index_daily("20260708")
    limit_result = backfill_limit_up_board("2026-07-08")
    print(f"index_daily fixed: {index_result}")
    print(f"index_daily backfilled: {all_index_result}")
    print(f"limit_up_board backfilled: {limit_result} rows")

"""
辅助数据回填: 涨停板、资金流向、指数日线。

使用方法:
    python scripts/backfill_auxiliary.py --tables limit_up,index_daily --recent-days 90
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import (
    DailyKbar, IndexDaily, LimitUpBoard, CapitalFlow, DragonTiger,
)
from scripts.tushare_init import get_pro

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_aux")


def _normalize_trade_date(value: str) -> str:
    """统一交易日格式 → YYYY-MM-DD。"""
    v = str(value or "").strip()
    if not v:
        return ""
    if len(v) >= 10 and v[4] == "-" and v[7] == "-":
        return v[:10]
    digits = "".join(ch for ch in v if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return v[:10]


def backfill_limit_up(pro, db, start_date: str, end_date: str) -> dict:
    """回填涨停板数据。"""
    stored = 0
    skipped = 0
    errors = 0

    trading_dates = db.execute(
        select(DailyKbar.trade_date)
        .where(DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
        .distinct()
        .order_by(DailyKbar.trade_date)
    ).scalars().all()

    logger.info("涨停板回填: %d 个交易日", len(trading_dates))

    for td in trading_dates:
        td_str = td
        existing = db.scalar(
            select(func.count()).select_from(LimitUpBoard)
            .where(LimitUpBoard.trade_date == td_str)
        )
        if existing and existing > 10:
            skipped += 1
            continue

        try:
            r = pro.limit_list_d(trade_date=td_str.replace("-", ""))
            if r is None or r.empty:
                skipped += 1
                continue

            records = []
            for _, row in r.iterrows():
                try:
                    import pandas as pd
                    fd = 0.0 if pd.isna(row.get("fd_amount")) else float(row.get("fd_amount", 0) or 0)
                    limit_amt = 0.0 if pd.isna(row.get("limit_amount")) else float(row.get("limit_amount", 0) or 0)
                    pct = 0.0 if pd.isna(row.get("pct_chg")) else float(row.get("pct_chg", 0) or 0)
                    turnover = 0.0 if pd.isna(row.get("turnover_ratio")) else float(row.get("turnover_ratio", 0) or 0)
                    ot = 0 if pd.isna(row.get("open_times")) else int(row.get("open_times", 0) or 0)
                    lt = 0 if pd.isna(row.get("limit_times")) else int(row.get("limit_times", 0) or 0)
                    records.append({
                        "trade_date": _normalize_trade_date(td_str),
                        "code": str(row.get("ts_code", "")).split(".")[0],
                        "name": str(row.get("name", "")),
                        "pct_chg": pct,
                        "limit_type": str(row.get("limit", "")),
                        "limit_times": lt,
                        "open_times": ot,
                        "up_stat": str(row.get("up_stat", "")),
                        "first_time": str(row.get("first_time", "")),
                        "last_time": str(row.get("last_time", "")),
                        "fd_amount_yi": round(fd / 1e8, 4) if fd else 0.0,
                        "limit_amount_yi": round(limit_amt / 1e8, 4) if limit_amt else 0.0,
                        "turnover_ratio": turnover,
                        "industry": str(row.get("industry", "")),
                    })
                except Exception:
                    errors += 1

            if records:
                stmt = mysql_insert(LimitUpBoard).values(records)
                stmt = stmt.on_duplicate_key_update(id=LimitUpBoard.id)
                db.execute(stmt)
                db.commit()
                stored += len(records)
        except Exception as e:
            db.rollback()
            errors += 1
            logger.warning("  %s 失败: %s", td_str, str(e)[:60])

        if len(trading_dates) > 10 and trading_dates.index(td) % 10 == 0:
            logger.info("  涨停板 %d/%d | 入库%d 跳过%d",
                         trading_dates.index(td)+1, len(trading_dates), stored, skipped)

    logger.info("涨停板完成: 入库%d 跳过%d 错误%d", stored, skipped, errors)
    return {"stored": stored, "skipped": skipped, "errors": errors}


def backfill_index_daily(pro, db, start_date: str, end_date: str) -> dict:
    """回填指数日线。"""
    stored = 0
    errors = 0

    indices = {
        "000001.SH": "上证指数",
        "000300.SH": "沪深300",
        "000905.SH": "中证500",
        "000852.SH": "中证1000",
        "399001.SZ": "深证成指",
        "399006.SZ": "创业板指",
    }

    for ts_code, name in indices.items():
        last = db.scalar(
            select(func.max(IndexDaily.trade_date))
            .where(IndexDaily.ts_code == ts_code)
        )
        if last and last >= end_date:
            logger.info("  %s: 已最新 (%s)", name, last)
            continue

        try:
            r = pro.index_daily(ts_code=ts_code, start_date=start_date.replace("-", ""),
                                end_date=end_date.replace("-", ""))
            if r is None or r.empty:
                continue

            records = []
            for _, row in r.iterrows():
                try:
                    records.append({
                        "ts_code": ts_code,
                        "trade_date": _normalize_trade_date(str(row.get("trade_date", ""))),
                        "close": float(row.get("close", 0)),
                        "pct_chg": float(row.get("pct_chg", 0)),
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "volume": float(row.get("vol", 0)),
                    })
                except Exception:
                    errors += 1

            if records:
                stmt = mysql_insert(IndexDaily).values(records)
                stmt = stmt.on_duplicate_key_update(id=IndexDaily.id)
                db.execute(stmt)
                db.commit()
                stored += len(records)
            logger.info("  %s: +%d 条", name, len(r))
        except Exception as e:
            db.rollback()
            errors += 1
            logger.warning("  %s 失败: %s", name, str(e)[:60])

    logger.info("指数日线完成: 入库%d 错误%d", stored, errors)
    return {"stored": stored, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="辅助数据回填")
    parser.add_argument("--tables", default="all",
                        help="回填表: all / limit_up / index_daily (逗号分隔)")
    parser.add_argument("--recent-days", type=int, default=90,
                        help="最近N天 (默认90)")
    args = parser.parse_args()

    pro = get_pro()
    db = SessionLocal()

    try:
        start_date = (date.today() - timedelta(days=args.recent_days)).strftime("%Y-%m-%d")
        end_date = date.today().strftime("%Y-%m-%d")
        logger.info("辅助数据回填: %s ~ %s", start_date, end_date)

        tables = [t.strip() for t in args.tables.split(",")] if args.tables != "all" else ["limit_up", "index_daily"]

        for t in tables:
            if t == "limit_up":
                r = backfill_limit_up(pro, db, start_date, end_date)
                logger.info("  => limit_up: %s", r)
            elif t == "index_daily":
                r = backfill_index_daily(pro, db, start_date, end_date)
                logger.info("  => index_daily: %s", r)

        # 汇总
        logger.info("=" * 50)
        for tbl in ["limit_up_board", "index_daily"]:
            model = LimitUpBoard if tbl == "limit_up_board" else IndexDaily
            cnt = db.scalar(select(func.count()).select_from(model))
            dates = db.scalar(select(func.count(func.distinct(model.trade_date))))
            logger.info("  %s: %d 行, %d 天", tbl, cnt, dates)

    finally:
        db.close()


if __name__ == "__main__":
    main()

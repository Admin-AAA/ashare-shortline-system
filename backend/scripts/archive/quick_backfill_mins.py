"""
快速补缺：最近 N 个交易日的分钟K线（15min）
用法: python scripts/quick_backfill_mins.py [--days 3] [--top 200]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import math
import logging
from datetime import date, timedelta
from sqlalchemy import text, select, func

from scripts.tushare_init import get_pro
from app.db.session import SessionLocal
from app.models.tables import DailyKbar, MinuteKbar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quick_backfill_mins")

FREQ = "15min"


def get_recent_trading_dates(db, n_days: int):
    """Get last N trading dates from daily_kbars."""
    rows = db.execute(
        text("SELECT DISTINCT CAST(trade_date AS DATE) as d FROM daily_kbars "
             "WHERE CAST(trade_date AS DATE) >= :start "
             "ORDER BY d DESC LIMIT :lim"),
        {"start": (date.today() - timedelta(days=14)).isoformat(), "lim": n_days}
    ).all()
    return [row[0] for row in rows]


def get_existing_counts(db, target_date: str):
    """Check how many stocks already have minute data for this date."""
    cnt = db.scalar(
        text("SELECT COUNT(DISTINCT code) FROM minute_kbars WHERE trade_date = :d AND freq = :f"),
        {"d": target_date, "f": FREQ}
    ) or 0
    return cnt


def get_top_stocks(db, target_date: str, limit: int):
    """Get top stocks by volume on a given date."""
    rows = db.execute(
        text("SELECT code FROM daily_kbars WHERE CAST(trade_date AS DATE) = :d "
             "AND volume > 0 ORDER BY volume DESC LIMIT :lim"),
        {"d": target_date, "lim": limit}
    ).all()
    return [row[0] for row in rows]


def backfill_stock(pro, ts_code: str, target_date: str) -> list:
    """Fetch minute bars for one stock on one date. Returns list of rows or empty."""
    try:
        df = pro.stk_mins(ts_code=ts_code, freq=FREQ,
                         start_date=target_date, end_date=target_date)
    except Exception as e:
        logger.debug(f"  {ts_code} API error: {e}")
        return []

    if df is None or len(df) == 0:
        return []

    rows = []
    for _, row in df.iterrows():
        o = float(row["open"]) if not math.isnan(float(row["open"])) else 0.0
        h = float(row["high"]) if not math.isnan(float(row["high"])) else 0.0
        l = float(row["low"]) if not math.isnan(float(row["low"])) else 0.0
        c = float(row["close"]) if not math.isnan(float(row["close"])) else 0.0
        v = float(row["vol"]) if not math.isnan(float(row["vol"])) else 0.0
        a = float(row["amount"]) if not math.isnan(float(row["amount"])) else 0.0
        trade_time = str(row["trade_time"])
        rows.append((ts_code, FREQ, trade_time, target_date, o, h, l, c, v, a))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3, help="最近N个交易日")
    parser.add_argument("--top", type=int, default=200, help="每日前N只股票")
    parser.add_argument("--batch", type=int, default=100, help="批量INSERT行数")
    args = parser.parse_args()

    pro = get_pro()
    db = SessionLocal()

    try:
        dates = get_recent_trading_dates(db, args.days)
        logger.info(f"目标交易日: {dates}")

        total_inserted = 0
        for target_date in dates:
            existing = get_existing_counts(db, target_date)
            logger.info(f"\n📅 {target_date}: 已有 {existing} 只股票，目标 {args.top} 只")

            if existing >= args.top * 0.95:
                logger.info(f"  ✅ 覆盖率充足，跳过")
                continue

            stocks = get_top_stocks(db, target_date, args.top)
            logger.info(f"  待回填 {len(stocks)} 只股票")

            batch = []
            for i, code in enumerate(stocks):
                if i % 20 == 0:
                    logger.info(f"  进度: {i}/{len(stocks)}")

                rows = backfill_stock(pro, code, target_date)
                batch.extend(rows)

                if len(batch) >= args.batch:
                    _insert_batch(db, batch)
                    total_inserted += len(batch)
                    batch = []

            if batch:
                _insert_batch(db, batch)
                total_inserted += len(batch)

        logger.info(f"\n✅ 完成: 共写入 {total_inserted} 行分钟K线")

    finally:
        db.close()


def _insert_batch(db, rows):
    """INSERT IGNORE batch into minute_kbars — note: no 'source' column."""
    if not rows:
        return
    placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(rows))
    flat = []
    for r in rows:
        flat.extend(r)
    sql = f"INSERT IGNORE INTO minute_kbars (code, freq, trade_time, trade_date, open, high, low, close, volume, amount) VALUES {placeholders}"
    db.execute(text(sql), flat)
    db.commit()


if __name__ == "__main__":
    main()

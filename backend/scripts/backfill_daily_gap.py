"""
定向回填日K缺口：2026年5月（May 1-19）
用法: python scripts/backfill_daily_gap.py [--limit 100] [--dry-run]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
import math
from datetime import date
from sqlalchemy import text

from scripts.tushare_init import get_pro
from app.db.session import SessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_daily_gap")

GAP_START = "20260501"
GAP_END = "20260519"
BATCH_SIZE = 200
SOURCE = "tushare"


def code_to_ts(code: str) -> str:
    """Convert 000001 → 000001.SZ / 600000 → 600000.SH"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500, help="回填股票数量上限")
    parser.add_argument("--dry-run", action="store_true", help="只检查不回写")
    args = parser.parse_args()

    pro = get_pro()
    db = SessionLocal()

    try:
        # 1. 找出已覆盖数最少的日期
        existing = db.execute(
            text("SELECT COUNT(DISTINCT code) FROM daily_kbars WHERE trade_date >= :s AND trade_date <= :e"),
            {"s": GAP_START, "e": GAP_END}
        ).scalar() or 0
        logger.info(f"May 1-19 已有日K: {existing} 行（含多只股票）")

        # 2. 获取近期成交额最高的股票作为回填优先
        top_stocks = db.execute(
            text("""
                SELECT code FROM daily_kbars 
                WHERE trade_date >= '2026-05-20' 
                  AND volume > 0 
                  AND code NOT LIKE '000%'  -- 先排除指数类
                GROUP BY code 
                ORDER BY SUM(amount) DESC 
                LIMIT :lim
            """),
            {"lim": args.limit}
        ).all()
        codes = [row[0] for row in top_stocks if row[0] and len(str(row[0])) == 6]
        logger.info(f"目标股票: {len(codes)} 只")

        if args.dry_run:
            logger.info("DRY RUN — 不执行回填")
            test_code = codes[0]
            ts_code = code_to_ts(test_code)
            df = pro.daily(ts_code=ts_code, start_date=GAP_START, end_date=GAP_END)
            logger.info(f"  {test_code} ({ts_code}): {len(df)} 条日K")
            if len(df) > 0:
                logger.info(f"  样本: {df.head(2).to_string()}")
            return

        # 3. 逐股票回填
        total = 0
        for i, code in enumerate(codes):
            if i % 50 == 0:
                logger.info(f"进度: {i}/{len(codes)}")

            ts_code = code_to_ts(code)
            try:
                df = pro.daily(ts_code=ts_code, start_date=GAP_START, end_date=GAP_END)
            except Exception as e:
                logger.warning(f"  {ts_code} API错误: {e}")
                continue

            if df is None or len(df) == 0:
                continue

            batch = []
            for _, row in df.iterrows():
                r_code = str(row.get("ts_code", ""))[:6]
                t_date = str(row.get("trade_date", ""))[:10]
                if not t_date or t_date < GAP_START:
                    continue

                o = float(row["open"]) if not math.isnan(float(row["open"])) else 0.0
                h = float(row["high"]) if not math.isnan(float(row["high"])) else 0.0
                l = float(row["low"]) if not math.isnan(float(row["low"])) else 0.0
                c = float(row["close"]) if not math.isnan(float(row["close"])) else 0.0
                v = float(row["vol"]) if not math.isnan(float(row["vol"])) else 0.0
                a = float(row["amount"]) if not math.isnan(float(row["amount"])) else 0.0
                pct = float(row.get("pct_chg", 0)) if not math.isnan(float(row.get("pct_chg", 0) or 0)) else 0.0

                batch.append((r_code, t_date, o, h, l, c, v, a, pct, SOURCE))

            if batch:
                placeholders = ",".join(
                    ["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(batch)
                )
                flat = [x for row2 in batch for x in row2]
                sql = (
                    "INSERT IGNORE INTO daily_kbars (code, trade_date, open, high, low, "
                    "close, volume, amount, pct_chg, source) VALUES " + placeholders
                )
                # Use exec_driver_sql to bypass SQLAlchemy 2.0 parameter distillation
                conn = db.connection().connection
                conn.cursor().execute(sql, flat)
                conn.commit()
                total += len(batch)

        logger.info(f"✅ 完成: 共写入 {total} 行日K")

    finally:
        db.close()


if __name__ == "__main__":
    main()

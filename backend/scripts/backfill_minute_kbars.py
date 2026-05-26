"""
分钟K线数据大规模回填脚本 (MySQL版)。

策略：
  - 30min 频率: 单次调用覆盖 ~1000 个交易日 (约4年), 最经济
  - 按股票逐个拉取, 入库 minute_kbar 表
  - MySQL 用 INSERT ... ON DUPLICATE KEY UPDATE 去重

使用方法:
    python scripts/backfill_minute_kbars.py --freq 30min --limit 100
    python scripts/backfill_minute_kbars.py --freq 5min --limit 50
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, func
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import DailyKbar, MinuteKbar
from scripts.tushare_init import get_pro

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_minute")


def get_active_recent_stocks(db, min_date: str = "2026-05-01") -> list[str]:
    """获取最近有日K数据的活跃股票代码。"""
    rows = db.execute(
        select(DailyKbar.code)
        .where(DailyKbar.trade_date >= min_date)
        .distinct()
    ).scalars().all()
    logger.info("最近活跃股票: %d 只 (>= %s)", len(rows), min_date)
    return list(rows)


def stock_ts_code(code: str) -> str:
    """将纯数字代码转为 Tushare ts_code 格式。"""
    if code.startswith("6"):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


def backfill_stock_minute(pro, db, code: str, freq: str = "30min") -> dict:
    """回填单只股票的分钟数据。"""
    ts_code = stock_ts_code(code)
    result = {"code": code, "freq": freq, "fetched": 0, "stored": 0, "errors": 0}

    try:
        r = pro.stk_mins(ts_code=ts_code, freq=freq)
        if r is None or r.empty:
            return result

        result["fetched"] = len(r)

        # 批量构建记录
        records: list[dict[str, Any]] = []
        for _, row in r.iterrows():
            try:
                trade_time = row["trade_time"]
                if isinstance(trade_time, str):
                    dt = datetime.fromisoformat(trade_time) if len(str(trade_time)) == 19 else datetime.strptime(str(trade_time), "%Y-%m-%d %H:%M:%S")
                else:
                    dt = trade_time.to_pydatetime()

                records.append({
                    "code": code,
                    "freq": freq,
                    "trade_date": dt.strftime("%Y-%m-%d"),
                    "trade_time": dt,
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "volume": float(row["vol"]) if row.get("vol") else 0.0,
                    "amount": float(row["amount"]) if row.get("amount") else 0.0,
                })
            except Exception:
                result["errors"] += 1

        # MySQL bulk insert with ON DUPLICATE KEY UPDATE (id=id = no-op)
        if records:
            stmt = mysql_insert(MinuteKbar).values(records)
            stmt = stmt.on_duplicate_key_update(id=MinuteKbar.id)
            db.execute(stmt)
            db.commit()
            result["stored"] = len(records)

    except Exception as e:
        logger.error("  %s: %s", code, str(e)[:80])
        db.rollback()
        result["errors"] += 1

    return result


def main():
    parser = argparse.ArgumentParser(description="分钟K线数据回填")
    parser.add_argument("--freq", default="30min",
                        choices=["1min", "5min", "15min", "30min", "60min"],
                        help="K线频率 (默认 30min, 覆盖范围最广)")
    parser.add_argument("--limit", type=int, default=None,
                        help="限制回填股票数量 (默认全部)")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="每次API调用间隔秒数 (默认0.5)")
    args = parser.parse_args()

    pro = get_pro()
    db = SessionLocal()

    try:
        codes = get_active_recent_stocks(db, min_date="2026-05-01")
        if args.limit:
            codes = codes[:args.limit]

        logger.info("开始回填: freq=%s, stocks=%d, delay=%.1fs", args.freq, len(codes), args.delay)

        total = {"fetched": 0, "stored": 0, "errors": 0}
        t0 = time.time()

        for i, code in enumerate(codes):
            r = backfill_stock_minute(pro, db, code, freq=args.freq)
            total["fetched"] += r["fetched"]
            total["stored"] += r["stored"]
            total["errors"] += r["errors"]

            if (i + 1) % 20 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                eta = (len(codes) - i - 1) / rate if rate > 0 else 0
                logger.info(
                    "进度 %d/%d (%.1f%%) | 命中%d 入库%d 错误%d | %.1f只/s | ETA %.0fs",
                    i + 1, len(codes), (i + 1) / len(codes) * 100,
                    total["fetched"], total["stored"], total["errors"],
                    rate, eta,
                )

            time.sleep(args.delay)

        elapsed = time.time() - t0
        logger.info("=" * 60)
        logger.info("回填完成: %d只股票, 耗时%.0fs", len(codes), elapsed)
        logger.info("  拉取行数: %d | 入库: %d | 错误: %d",
                     total["fetched"], total["stored"], total["errors"])

        # 最终状态
        final_cnt = db.scalar(select(func.count()).select_from(MinuteKbar))
        final_30m = db.scalar(
            select(func.count()).select_from(MinuteKbar)
            .where(MinuteKbar.freq == args.freq)
        )
        final_dates = db.scalar(
            select(func.count(func.distinct(MinuteKbar.trade_date)))
            .where(MinuteKbar.freq == args.freq)
        )
        logger.info("minute_kbar: %d 总行, freq=%s: %d 行 %d 天",
                     final_cnt, args.freq, final_30m, final_dates)

    finally:
        db.close()


if __name__ == "__main__":
    main()

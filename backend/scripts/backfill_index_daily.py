"""
回填指数日线行情（沪深300 / 中证500）到 index_daily 表。

用于回测基准对比。覆盖最近 3 年数据，增量写入（去重）。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tables import IndexDaily
from app.services.tushare_client import fetch_index_daily

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_index")


def backfill(ts_codes: list[str] | None = None, lookback_days: int = 1095) -> dict[str, int]:
    """批量获取指数日线并写入 index_daily 表。"""
    from datetime import date, timedelta

    targets = ts_codes or ["000300.SH", "000905.SH"]
    end = date.today()
    start = end - timedelta(days=lookback_days)
    counts: dict[str, int] = {}

    db = SessionLocal()
    try:
        for ts_code in targets:
            rows = fetch_index_daily(
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if not rows:
                logger.warning("%s: 无数据", ts_code)
                counts[ts_code] = 0
                continue

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
            logger.info("%s: 写入 %d 行 (共 %d 行)", ts_code, written, len(rows))
            counts[ts_code] = written
    finally:
        db.close()
    return counts


if __name__ == "__main__":
    counts = backfill()
    total = sum(counts.values())
    logger.info("回填完成: %s (共 %d 行)", counts, total)

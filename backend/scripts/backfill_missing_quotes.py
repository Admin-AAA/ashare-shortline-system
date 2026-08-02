#!/usr/bin/env python3
"""补齐 stock_quotes 缺失的历史交易日报价。

窗口内 10 个交易日（2026-05-25 / 06-01 / 06-15 / 06-18 / 06-22 / 06-24~30）只有
daily_kbars 而无 stock_quotes。universe 加载按 as_of_date 精确匹配 stock_quotes，
缺报价日会直接报"统一行情池没有可用候选股票"而重放失败。

本脚本从 daily_kbars + stocks 映射生成缺失日期的 quote 行：
  price/open/high/low/preclose/pct/volume/amount_yi/turnover_rate  ← daily_kbars
  industry/name/is_st                                              ← stocks
  pe_ttm/total_mv_yi/free_mv_yi/chg60                              ← NULL（与现有历史 quote 一致）

只 INSERT 缺失 (code, trade_date)，不覆盖已有行，可重复运行。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.tables import DailyKbar, Stock, StockQuote  # noqa: E402

MISSING_DATES = [
    "2026-05-25", "2026-06-01", "2026-06-15", "2026-06-18", "2026-06-22",
    "2026-06-24", "2026-06-25", "2026-06-26", "2026-06-29", "2026-06-30",
]


def main() -> int:
    db = SessionLocal()
    total_written = 0
    try:
        for trade_date in MISSING_DATES:
            # 该日已有的 quote code 集合（幂等保护）
            existing = set(db.scalars(
                select(StockQuote.code).where(StockQuote.trade_date == trade_date)
            ).all())
            if existing:
                print(f"{trade_date}: 已有 {len(existing)} 条，跳过")
                continue

            # 前一日 K 线（preclose 来源）——取 daily_kbars 中小于该日的最近收盘
            bars = db.execute(
                select(
                    DailyKbar.code, DailyKbar.open, DailyKbar.high, DailyKbar.low,
                    DailyKbar.close, DailyKbar.volume, DailyKbar.amount,
                    DailyKbar.pct_chg, DailyKbar.turnover_rate,
                ).where(DailyKbar.trade_date == trade_date)
            ).all()
            if not bars:
                print(f"{trade_date}: 无日K数据，跳过")
                continue

            # preclose = 上一交易日 close。直接取 daily_kbars 里小于该日的最近一个交易日
            prev_day = db.scalar(
                select(DailyKbar.trade_date)
                .where(DailyKbar.trade_date < trade_date)
                .order_by(DailyKbar.trade_date.desc())
                .limit(1)
            )
            prev_close: dict[str, float] = {}
            if prev_day:
                prev_rows = db.execute(
                    select(DailyKbar.code, DailyKbar.close)
                    .where(DailyKbar.trade_date == prev_day)
                ).all()
                prev_close = {str(code): float(close or 0) for code, close in prev_rows}

            stocks = {
                row.code: row for row in db.scalars(select(Stock)).all()
            }

            rows = []
            for bar in bars:
                code = str(bar.code)
                close = float(bar.close or 0)
                st = stocks.get(code)
                rows.append({
                    "code": code,
                    "trade_date": trade_date,
                    "name": st.name if st else "",
                    "price": close,
                    "pct": float(bar.pct_chg or 0),
                    "volume": float(bar.volume or 0),
                    "amount_yi": round(float(bar.amount or 0) / 100000000, 3),
                    "turnover_rate": float(bar.turnover_rate or 0),
                    "pe_ttm": None,
                    "total_mv_yi": None,
                    "free_mv_yi": None,
                    "chg60": None,
                    "high": float(bar.high or 0),
                    "low": float(bar.low or 0),
                    "open": float(bar.open or 0),
                    "preclose": prev_close.get(code),
                    "industry": st.industry or "" if st else "",
                    "source": "tushare",
                    "source_count": 1,
                    "quality_score": 75,
                    "freshness_status": "inactive",
                })

            for row in rows:
                db.add(StockQuote(**row))
            db.commit()
            total_written += len(rows)
            print(f"{trade_date}: 写入 {len(rows)} 条")
        print(f"完成，共写入 {total_written} 条 quote")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

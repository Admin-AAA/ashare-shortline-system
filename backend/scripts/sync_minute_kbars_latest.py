"""
补齐最近 N 个交易日的分钟K线缺口（仅对市值前 M 只股票）。

使用方法:
    python scripts/sync_minute_kbars_latest.py [--days 3] [--top-n 200]
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.tables import MinuteKbar, StockQuote
from app.services.tushare_client import fetch_minute_kbars


def main():
    parser = argparse.ArgumentParser(description="补齐分钟K线缺口")
    parser.add_argument("--days", type=int, default=3, help="最近N天")
    parser.add_argument("--top-n", type=int, default=200, help="市值前N只股票")
    parser.add_argument("--freq", type=str, default="1min", help="K线频率")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Get missing trading dates
        end = date.today()
        start = end - timedelta(days=args.days * 2)
        last_dates = db.execute(
            select(MinuteKbar.trade_date)
            .where(
                MinuteKbar.trade_date >= start.strftime("%Y-%m-%d"),
                MinuteKbar.freq == args.freq,
            )
            .distinct()
            .order_by(MinuteKbar.trade_date.desc())
            .limit(args.days * 2)
        ).scalars().all()

        expected_dates = set()
        from app.services.data_sources import _previous_weekday
        d = end
        while len(expected_dates) < args.days:
            d = _previous_weekday(d) if hasattr(sys.modules.get("app.services.data_sources"), "_previous_weekday") else d - timedelta(days=1)
            d_str = d.strftime("%Y-%m-%d") if isinstance(d, date) else str(d)[:10]
            expected_dates.add(d_str)

        missing = sorted(expected_dates - set(last_dates), reverse=True)
        if not missing:
            print("分钟K线已是最新")
            db.close()
            return

        print(f"缺口交易日: {missing}")

        # Get top N stocks by market cap
        top_codes = db.execute(
            select(StockQuote.code)
            .where(StockQuote.freshness_status == "fresh")
            .order_by(StockQuote.total_mv_yi.is_(None), StockQuote.total_mv_yi.desc())
            .limit(args.top_n)
        ).scalars().all()

        print(f"目标股票: {len(top_codes)} 只 (市值 Top {args.top_n})")

        total = 0
        for code in top_codes:
            for td in missing:
                try:
                    if args.freq != "1min":
                        print(f"  {code} {td}: 当前 fetch_minute_kbars 仅支持 1min，跳过 {args.freq}")
                        continue
                    bars = fetch_minute_kbars(code, trade_date=td)
                    if not bars:
                        continue
                    from sqlalchemy.dialects.mysql import insert as mysql_insert
                    rows = []
                    for bar in bars:
                        rows.append({
                            "code": code,
                            "freq": args.freq,
                            "trade_time": bar.get("trade_time"),
                            "trade_date": bar.get("trade_date", td),
                            "open": bar.get("open", 0),
                            "high": bar.get("high", 0),
                            "low": bar.get("low", 0),
                            "close": bar.get("close", 0),
                            "volume": bar.get("volume", 0),
                            "amount": bar.get("amount", 0),
                        })
                    if rows:
                        stmt = mysql_insert(MinuteKbar).values(rows)
                        stmt = stmt.on_duplicate_key_update(id=MinuteKbar.id)
                        db.execute(stmt)
                        total += len(rows)
                except Exception as e:
                    print(f"  {code} {td}: {str(e)[:80]}")
                    continue

        db.commit()
        print(f"完成: {total} 条分钟K线 ({args.freq})")
    finally:
        db.close()


if __name__ == "__main__":
    main()

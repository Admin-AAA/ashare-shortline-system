"""
快速补充最近 N 个交易日的日K数据。增量模式 — 只拉取缺失的交易日。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal
from app.services.market_pool import (
    _latest_allowed_bar_date,
    _load_cached_bars_summary,
    _load_cached_quotes,
    _persist_normalized_bars,
)
from app.services.market_data import _fetch_kline, _select_candidates
from app.services.data_sources import enabled_sources


def main(days: int = 3):
    db = SessionLocal()
    try:
        quotes = _load_cached_quotes(db, skip_metrics=True)
        codes = [q["code"] for q in quotes]
        print(f"行情池 {len(codes)} 只股票")

        latest_allowed = _latest_allowed_bar_date()
        print(f"最新允许回填日期: {latest_allowed}")

        # 检查哪些股票缺少最近数据
        bars_summary = _load_cached_bars_summary(db, codes)
        candidates = []
        skipped = 0
        for code in codes:
            summary = bars_summary.get(code, {})
            latest = summary.get("latest_trade_date", "")
            bar_count = summary.get("bar_count", 0)
            if latest >= latest_allowed and bar_count >= 5:
                skipped += 1
                continue
            candidates.append(
                next((q for q in quotes if q["code"] == code), {"code": code})
            )

        print(f"已就绪 {skipped} 只，需回填 {len(candidates)} 只")

        if not candidates:
            print("所有股票数据已是最新，无需回填。")
            return

        sources = enabled_sources("kline_sources")
        print(f"K线数据源: {sources}")

        success = 0
        failed = 0
        for i, stock in enumerate(candidates):
            code = stock["code"]
            try:
                bars = _fetch_kline(
                    code=code,
                    start_date=None,
                    days=days,
                    sources=sources,
                    rate_limit=0.1,
                )
                if bars:
                    cleaned = [b for b in bars if b.get("trade_date") and b["trade_date"] <= latest_allowed]
                    if cleaned:
                        _persist_normalized_bars(db, code, cleaned)
                        latest = max(b["trade_date"] for b in cleaned)
                        success += 1
                    else:
                        failed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1

            if (i + 1) % 50 == 0:
                print(f"  进度 {i+1}/{len(candidates)}  成功={success}  失败={failed}")
            time.sleep(0.08)

        print(f"完成: 成功={success} 失败={failed}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    main(days=args.days)

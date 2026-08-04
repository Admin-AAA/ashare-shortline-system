"""用 Baostock 补齐 minute_kbars 的 5/15/30/60min 最近缺口。

全部 tushare 源 stk_mins 无权限（实测 2026-08），baostock 支持 5/15/30/60min 且数据到当天。
1min 全源都补不到（baostock 不支持），本脚本不处理 1min。

防封：底层 _fetch_baostock_minute_kbars 全局串行 + 每次查询间隔 >=3.5s + 失败重 login 重试。
速度估算：每 code 每天 4 个频率 = 4 次查询 ≈ 14s；300 code × 3 天 ≈ 3.5 小时。务必后台跑。

用法：
    .venv/bin/python scripts/backfill_minute_baostock.py \
        --dates 2026-07-31 2026-08-03 2026-08-04 --codes 300 --freqs 5min 15min 30min 60min
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from app.db.session import SessionLocal
from app.services.data_gap_remediation_service import (
    _backfill_minute_kbar_date,
    _minute_kbar_target_codes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
logger = logging.getLogger("backfill_minute_baostock")

# 每个 code 一次调用里串行补齐这些频率（共享 baostock 限速器）
DEFAULT_FREQS = ["5min", "15min", "30min", "60min"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dates", nargs="+", required=True, help="交易日 YYYY-MM-DD 列表")
    parser.add_argument("--codes", type=int, default=300, help="每日目标股票数（按成交额）")
    parser.add_argument("--freqs", nargs="+", default=DEFAULT_FREQS)
    args = parser.parse_args()

    total_rows = 0
    t0 = time.monotonic()
    for trade_date in args.dates:
        db = SessionLocal()
        try:
            codes = _minute_kbar_target_codes(db, trade_date, limit=args.codes)
        finally:
            db.close()
        logger.info("=== %s 目标 %d 只 × %d 频率 ===", trade_date, len(codes), len(args.freqs))

        for idx, code in enumerate(codes, 1):
            db = SessionLocal()
            try:
                for freq in args.freqs:
                    result = _backfill_minute_kbar_date(db, trade_date, [code], freq=freq)
                    total_rows += int(result.get("row_count") or 0)
            except Exception as exc:
                logger.warning("  %s 失败: %s", code, exc)
            finally:
                db.close()
            if idx % 20 == 0:
                elapsed = time.monotonic() - t0
                logger.info("  %s 进度 %d/%d 累计落库 %d 行 已用 %.0fs", trade_date, idx, len(codes), total_rows, elapsed)

    logger.info("全部完成，累计落库 %d 行，总耗时 %.0fs", total_rows, time.monotonic() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

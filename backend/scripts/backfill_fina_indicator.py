#!/usr/bin/env python3
"""
财务指标（fina_indicator）回填脚本 — 拉取真实 ROE/毛利率/同比等，替代 feature_builder 中的常数默认值。

数据源：Tushare fina_indicator（15000 积分可用）
目标表：stock_fina_indicator（按 code+end_date 唯一）

使用方法:
    # 指定股票（逗号分隔）
    python scripts/backfill_fina_indicator.py --codes 000001,600519

    # 全市场（从 stocks 表读取所有code，逐股票拉取，限速 90次/分）
    python scripts/backfill_fina_indicator.py --all

    # 指定回填期数（默认 4 期 = 最近一年）
    python scripts/backfill_fina_indicator.py --codes 000001 --periods 8

说明:
    - 单只股票一次返回该股所有历史期，--periods 限制返回最近 N 期
    - 缺失字段保留为 None（不伪装成 0），由 feature_builder 区分"无数据"
    - upsert by (code, end_date)，重复运行安全
    - 财报季（4/8/10 月披露后）增量补，非财报季无需频繁运行
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.tables import FinaIndicator, Stock
from app.services.tushare_client import fetch_fina_indicator
from app.services.market_pool import _persist_fina_indicator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_fina")


def _resolve_codes(db, codes_arg: str | None, all_flag: bool) -> list[str]:
    """解析待回填的股票代码列表。"""
    if codes_arg:
        return [c.strip() for c in codes_arg.split(",") if c.strip()]
    if all_flag:
        rows = db.execute(select(Stock.code).where(Stock.is_st.is_(False))).scalars().all()
        return sorted({r for r in rows if r and len(r) == 6})
    return []


def backfill_codes(db, codes: list[str], periods: int) -> dict:
    """逐股票拉取财务指标并入库。返回统计。"""
    stored = 0
    skipped = 0
    errors = 0
    total = len(codes)

    for i, code in enumerate(codes, start=1):
        try:
            rows = fetch_fina_indicator(code, limit=periods)
            if not rows:
                skipped += 1
                continue
            _persist_fina_indicator(db, rows)
            stored += len(rows)
        except Exception as e:
            db.rollback()
            errors += 1
            logger.warning("  %s 失败: %s", code, str(e)[:80])

        if total > 10 and (i % 50 == 0 or i == total):
            logger.info("  进度 %d/%d | 入库%d 跳过%d 错误%d", i, total, stored, skipped, errors)

    logger.info("完成: 入库%d 跳过%d 错误%d", stored, skipped, errors)
    return {"stored": stored, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="财务指标回填 (fina_indicator)")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--codes", help="指定股票代码（逗号分隔，如 000001,600519）")
    g.add_argument("--all", action="store_true", help="全市场回填（从 stocks 表读取）")
    parser.add_argument("--periods", type=int, default=4, help="每只股票拉取最近 N 期（默认 4）")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        codes = _resolve_codes(db, args.codes, args.all)
        if not codes:
            logger.warning("无可回填股票，退出")
            return
        logger.info("── 财务指标回填: %d 只股票, 每只最近 %d 期 ──", len(codes), args.periods)

        backfill_codes(db, codes, args.periods)

        # 汇总
        cnt = db.scalar(select(func.count()).select_from(FinaIndicator))
        distinct_codes = db.scalar(select(func.count(func.distinct(FinaIndicator.code))))
        latest_end = db.scalar(select(func.max(FinaIndicator.end_date)))
        logger.info("=" * 50)
        logger.info("  stock_fina_indicator: %d 行, %d 只股票, 最新期 %s", cnt, distinct_codes, latest_end)
    finally:
        db.close()


if __name__ == "__main__":
    main()

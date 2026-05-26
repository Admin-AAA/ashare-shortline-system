"""
回填资金流向 (moneyflow) 和概念板块 (ths_index) 数据。

使用方法:
    python scripts/backfill_moneyflow_concept.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.db.session import SessionLocal
from app.models.tables import CapitalFlow, ConceptSector, DailyKbar
from scripts.tushare_init import get_pro

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_moneyflow_concept")


def get_trading_dates(db, start_date: str, end_date: str) -> list[str]:
    trading_dates = db.execute(
        select(DailyKbar.trade_date)
        .where(DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
        .distinct()
        .order_by(DailyKbar.trade_date)
    ).scalars().all()
    return list(trading_dates)


def backfill_moneyflow(pro, db, start_date: str, end_date: str) -> dict:
    """回填个股资金流向。"""
    stored = 0
    skipped = 0
    errors = 0

    existing_dates = set(
        db.execute(
            select(CapitalFlow.trade_date)
            .distinct()
        ).scalars().all()
    )

    trading_dates = get_trading_dates(db, start_date, end_date)
    target_dates = [td for td in trading_dates if td not in existing_dates
                    or db.scalar(select(func.count()).select_from(CapitalFlow)
                                 .where(CapitalFlow.trade_date == td)) < 100]

    logger.info("资金流向回填: %d 个待补交易日", len(target_dates))
    if not target_dates and all(td in existing_dates for td in trading_dates):
        logger.info("  资金流向数据已完整，跳过")
        return {"stored": 0, "skipped": len(trading_dates), "errors": 0}

    for td in target_dates:
        try:
            compact = td.replace("-", "")
            r = pro.moneyflow(trade_date=compact)
            if r is None or r.empty:
                skipped += 1
                continue

            records = []
            for _, row in r.iterrows():
                try:
                    code = str(row.get("ts_code", "")).split(".")[0]
                    if len(code) != 6:
                        continue
                    buy_elg = float(row.get("buy_elg_amount", 0) or 0)
                    sell_elg = float(row.get("sell_elg_amount", 0) or 0)
                    buy_lg = float(row.get("buy_lg_amount", 0) or 0)
                    sell_lg = float(row.get("sell_lg_amount", 0) or 0)
                    buy_md = float(row.get("buy_md_amount", 0) or 0)
                    sell_md = float(row.get("sell_md_amount", 0) or 0)
                    buy_sm = float(row.get("buy_sm_amount", 0) or 0)
                    sell_sm = float(row.get("sell_sm_amount", 0) or 0)

                    super_large = (buy_elg - sell_elg) / 1e8
                    large = (buy_lg - sell_lg) / 1e8
                    medium = (buy_md - sell_md) / 1e8
                    small = (buy_sm - sell_sm) / 1e8
                    main_net = super_large + large

                    records.append({
                        "code": code,
                        "trade_date": td,
                        "main_net_inflow_yi": round(main_net, 4),
                        "super_large_net_inflow_yi": round(super_large, 4),
                        "large_net_inflow_yi": round(large, 4),
                        "medium_net_inflow_yi": round(medium, 4),
                        "small_net_inflow_yi": round(small, 4),
                    })
                except Exception:
                    errors += 1

            if records:
                stmt = mysql_insert(CapitalFlow).values(records)
                stmt = stmt.on_duplicate_key_update(id=CapitalFlow.id)
                db.execute(stmt)
                db.commit()
                stored += len(records)
                logger.info("  %s: 入库 %d 条", td, len(records))
        except Exception as e:
            db.rollback()
            errors += 1
            logger.warning("  %s 失败: %s", td, str(e)[:80])

    logger.info("资金流向完成: 入库%d 跳过%d 错误%d", stored, skipped, errors)
    return {"stored": stored, "skipped": skipped, "errors": errors}


def compute_cumulative_inflows(db):
    """回填 cumulative_inflow_3d/5d/10d_yi 和 main_net_inflow_ratio。"""
    logger.info("开始计算累计资金流向...")
    rows = db.execute(
        select(CapitalFlow).order_by(CapitalFlow.code, CapitalFlow.trade_date)
    ).scalars().all()

    by_code: dict[str, list] = defaultdict(list)
    for row in rows:
        by_code[row.code].append(row)

    updated = 0
    for code, items in by_code.items():
        items.sort(key=lambda x: x.trade_date)
        for i, item in enumerate(items):
            main_yi = item.main_net_inflow_yi or 0
            cum_3d = sum(items[j].main_net_inflow_yi or 0 for j in range(max(0, i - 2), i + 1))
            cum_5d = sum(items[j].main_net_inflow_yi or 0 for j in range(max(0, i - 4), i + 1))
            cum_10d = sum(items[j].main_net_inflow_yi or 0 for j in range(max(0, i - 9), i + 1))

            # main_net_inflow_ratio = main_net / total trade amount (approximate)
            # For now, set a placeholder
            ratio = main_yi / (abs(main_yi) + 0.01) * 0.05 if main_yi != 0 else 0

            item.cumulative_inflow_3d_yi = round(cum_3d, 4)
            item.cumulative_inflow_5d_yi = round(cum_5d, 4)
            item.cumulative_inflow_10d_yi = round(cum_10d, 4)
            item.main_net_inflow_ratio = round(ratio, 4)
            updated += 1

    db.commit()
    logger.info("累计资金流向更新: %d 条", updated)


def backfill_concept_sector(pro, db) -> dict:
    """回填概念板块列表（不含日频，仅全量快照）。"""
    stored = 0
    errors = 0
    today_str = date.today().isoformat()

    try:
        r = pro.ths_index(exchange="A")
        if r is None or r.empty:
            logger.warning("ths_index 无返回")
            return {"stored": 0, "errors": 0}

        records = []
        for idx, (_, row) in enumerate(r.iterrows(), 1):
            try:
                records.append({
                    "sector_code": str(row.get("ts_code", "")),
                    "trade_date": today_str,
                    "sector_name": str(row.get("name", "")),
                    "member_count": int(row.get("count", 0) or 0),
                    "exchange": str(row.get("exchange", "")),
                    "list_date": str(row.get("list_date", "")),
                    "type": str(row.get("type", "")),
                    "heat_rank": idx,
                })
            except Exception:
                errors += 1

        if records:
            stmt = mysql_insert(ConceptSector).values(records)
            stmt = stmt.on_duplicate_key_update(id=ConceptSector.id)
            db.execute(stmt)
            db.commit()
            stored = len(records)
            logger.info("概念板块: 入库 %d 条", stored)

    except Exception as e:
        db.rollback()
        errors += 1
        logger.warning("概念板块回填失败: %s", str(e)[:80])

    logger.info("概念板块完成: 入库%d 错误%d", stored, errors)
    return {"stored": stored, "errors": errors}


def main():
    db = SessionLocal()
    pro = get_pro()
    try:
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=60)).isoformat()
        logger.info("回填窗口: %s ~ %s", start_date, end_date)

        # 1. 资金流向
        r1 = backfill_moneyflow(pro, db, start_date, end_date)
        logger.info("=> moneyflow: %s", r1)

        # 2. 计算累计资金流向
        if r1.get("stored", 0) > 0:
            compute_cumulative_inflows(db)

        # 3. 概念板块
        r2 = backfill_concept_sector(pro, db)
        logger.info("=> concept_sector: %s", r2)

        # 4. 汇总
        logger.info("=" * 50)
        for tbl, model in [("capital_flow", CapitalFlow), ("concept_sector", ConceptSector)]:
            cnt = db.scalar(select(func.count()).select_from(model))
            dates = db.scalar(select(func.count(func.distinct(model.trade_date))))
            logger.info("  %s: %d 行, %d 天", tbl, cnt, dates)

    finally:
        db.close()


if __name__ == "__main__":
    main()

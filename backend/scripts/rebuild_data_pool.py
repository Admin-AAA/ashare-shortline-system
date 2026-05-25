#!/usr/bin/env python3
"""
数据池重建脚本 — CSV导入 → Tushare补齐 → 辅助数据回填。

用法:
    python scripts/rebuild_data_pool.py [--dangerously-clear-data-pool]
                                        [--skip-csv] [--skip-tushare-kbars]
                                        [--skip-aux] [--csv-dir PATH]
                                        [--tushare-top-n 5200]
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import text, select, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.core.config import get_settings
from app.models.tables import (
    Stock, DailyKbar, StockQuote, LimitUpBoard, ConceptSector, ConceptSectorStock,
    CapitalFlow, DragonTiger, NewsAnnouncement, MarginData, HkHolding,
    HolderNumber, StkLimit, MinuteKbar, AuctionData, BrokerRecommend,
    HolderTrade, InstitutionalSurvey, ShareFloat, PledgeStat, Repurchase,
)
from app.services.tushare_client import (
    get_pro, ts_code, fetch_daily_kbars, fetch_stock_list,
    fetch_moneyflow, fetch_limit_up_board, fetch_concept_sectors,
    fetch_concept_members, fetch_dragon_tiger, fetch_top_inst,
    fetch_margin, fetch_hk_hold, fetch_holder_number, fetch_stk_limit,
    fetch_auction, fetch_broker_recommend, fetch_holder_trade,
    fetch_survey, fetch_repurchase, fetch_pledge_stat, fetch_share_float,
)
from app.services.validation.safety import assert_destructive_allowed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rebuild")

settings = get_settings()


# ═════════════════════════════════════════════════════════════════════════════
# Step 0: 清空数据池
# ═════════════════════════════════════════════════════════════════════════════

DATA_TABLES = [
    "stocks", "daily_kbars", "stock_quotes", "limit_up_board",
    "concept_sector", "concept_sector_stock", "capital_flow", "dragon_tiger",
    "news_announcement", "margin_data", "hk_holdings", "holder_numbers",
    "stk_limits", "minute_kbars", "auction_data", "broker_recommends",
    "holder_trades", "institutional_surveys", "share_floats",
    "pledge_stats", "repurchases",
]


def clear_all_tables(db: Session) -> None:
    logger.info("── 清空数据池 ──")
    for t in DATA_TABLES:
        db.execute(text(f"DELETE FROM {t}"))
        logger.info("  已清空: %s", t)
    db.commit()
    logger.info("数据池已清空")


# ═════════════════════════════════════════════════════════════════════════════
# Step 1: CSV 文件导入
# ═════════════════════════════════════════════════════════════════════════════

def _parse_csv_date(val) -> str:
    """CSV 日期 → YYYY-MM-DD。"""
    s = str(val).strip().replace("-", "")
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return str(val)[:10]


def import_csv_files(db: Session, csv_dir: str) -> tuple[int, int]:
    """导入 CSV K线文件，返回 (stocks_count, kbars_count)。"""
    logger.info("── CSV 导入 ──")
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    if not files:
        logger.warning("CSV 目录为空")
        return 0, 0

    stock_count = 0
    kbar_count = 0
    cutoff = "2024-05-01"  # 只保留近2年数据

    for fi, fpath in enumerate(files):
        basename = os.path.basename(fpath)
        code = basename.split("_")[0]
        if len(code) != 6 or not code.isdigit():
            continue

        try:
            df = pd.read_csv(fpath, dtype={"stock_code": str})
        except Exception as e:
            logger.warning("读取 %s 失败: %s", basename, e)
            continue

        # 过滤近2年
        df["trade_date"] = df["trade_date"].astype(str)
        df = df[df["trade_date"] >= cutoff]
        if df.empty:
            continue

        # 插入 Stock（如果不存在）
        existing = db.scalar(select(func.count()).select_from(Stock).where(Stock.code == code))
        if not existing:
            name = str(df.iloc[0].get("stock_code", code)).split(".")[0]
            market = "SH" if code.startswith(("6", "9")) else "SZ"
            db.add(Stock(code=code, name=name, market=market))
            db.flush()
            stock_count += 1

        # 导入 K线
        bars = []
        for _, row in df.iterrows():
            td = _parse_csv_date(row["trade_date"])
            amount = float(row.get("amount") or 0) * 1000  # 千元 → 元
            bars.append({
                "code": code,
                "trade_date": td,
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("close") or 0),
                "volume": float(row.get("vol") or 0),
                "amount": amount,
                "pct_chg": float(row.get("pct_chg") or 0),
                "source": "csv",
            })

        if bars:
            stmt = mysql_insert(DailyKbar).values(bars).prefix_with("IGNORE")
            db.execute(stmt)
            kbar_count += len(bars)

        if (fi + 1) % 50 == 0:
            db.commit()
            logger.info("  CSV进度: %d/%d (累计 %d stocks, %d kbars)", fi + 1, len(files), stock_count, kbar_count)

    db.commit()
    logger.info("CSV导入完成: %d stocks, %d kbars", stock_count, kbar_count)
    return stock_count, kbar_count


# ═════════════════════════════════════════════════════════════════════════════
# Step 2: Tushare 补充股票列表 + K线
# ═════════════════════════════════════════════════════════════════════════════

def fill_stock_list(db: Session) -> int:
    """从 Tushare 拉取全 A 股列表，补全 stocks 表。"""
    logger.info("── Tushare 股票列表 ──")
    stocks = fetch_stock_list()
    if not stocks:
        logger.warning("Tushare 股票列表为空")
        return 0

    total = 0
    for i, s in enumerate(stocks):
        code = s["code"]
        existing = db.scalar(select(func.count()).select_from(Stock).where(Stock.code == code))
        if not existing:
            is_st = "ST" in s.get("name", "")
            db.add(Stock(
                code=code,
                name=s["name"],
                industry=s.get("industry"),
                area=s.get("area"),
                market=s.get("market"),
                list_date=s.get("list_date"),
                is_st=is_st,
            ))
            db.flush()
            total += 1
        if (i + 1) % 1000 == 0:
            db.commit()
            logger.info("  Stock列表进度: %d/%d (新增 %d)", i + 1, len(stocks), total)
    db.commit()
    logger.info("Stock列表完成: 总 %d, 新增 %d", len(stocks), total)
    return total


def fill_kbars_tushare(db: Session, top_n: int = 5200) -> int:
    """用 Tushare 为每只股票拉取缺失的日K线（近250个交易日）。"""
    logger.info("── Tushare K线补齐 ──")
    from app.models.tables import Stock as StockModel

    codes = [row[0] for row in db.execute(
        select(StockModel.code).order_by(StockModel.code)
    ).fetchall()]
    if not codes:
        logger.warning("stocks 表为空，无法拉K线")
        return 0

    codes = codes[:top_n]
    total = 0
    for i, code in enumerate(codes):
        try:
            existing_dates = {
                row[0] for row in db.execute(
                    select(DailyKbar.trade_date).where(DailyKbar.code == code)
                ).fetchall()
            }
            bars = fetch_daily_kbars(code, days=250)
            new_bars = [b for b in bars if b["trade_date"] not in existing_dates]
            if new_bars:
                stmt = mysql_insert(DailyKbar).values(new_bars).prefix_with("IGNORE")
                db.execute(stmt)
                total += len(new_bars)
        except Exception as e:
            logger.debug("K线 %s 失败: %s", code, e)

        if (i + 1) % 200 == 0:
            db.commit()
            logger.info("  K线进度: %d/%d (新增 %d bars)", i + 1, len(codes), total)
        time.sleep(0.05)
    db.commit()
    logger.info("K线补齐完成: 新增 %d bars", total)
    return total


# ═════════════════════════════════════════════════════════════════════════════
# Step 3: 辅助数据回填
# ═════════════════════════════════════════════════════════════════════════════

def _top_codes(db: Session, n: int = 300) -> list[str]:
    rows = db.execute(
        select(DailyKbar.code, func.max(DailyKbar.amount).label("max_amt"))
        .group_by(DailyKbar.code)
        .order_by(text("max_amt DESC"))
        .limit(n)
    ).fetchall()
    return [r[0] for r in rows]


def backfill_aux_all(db: Session, top_n: int = 300) -> None:
    """回填所有辅助数据。"""
    codes = _top_codes(db, top_n)
    if not codes:
        codes = [row[0] for row in db.execute(select(DailyKbar.code).limit(top_n)).fetchall()]
    if not codes:
        logger.warning("无可用股票代码")
        return
    logger.info("辅助数据目标股票数: %d", len(codes))

    # 融资融券
    logger.info("── 融资融券 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_margin(code, days=5)
            if rows:
                stmt = mysql_insert(MarginData).values(rows)
                db.execute(stmt.on_duplicate_key_update(
                    rzye=stmt.inserted.rzye, rqye=stmt.inserted.rqye,
                    rzmre=stmt.inserted.rzmre, rzche=stmt.inserted.rzche,
                    rzrqye=stmt.inserted.rzrqye,
                ))
                total += len(rows)
        except Exception as e:
            logger.debug("融资融券 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
    db.commit()
    logger.info("融资融券完成: %d 条", total)

    # 港股通
    logger.info("── 港股通 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            row = fetch_hk_hold(code)
            if row:
                stmt = mysql_insert(HkHolding).values([row])
                db.execute(stmt.on_duplicate_key_update(
                    vol=stmt.inserted.vol, ratio=stmt.inserted.ratio,
                ))
                total += 1
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            db.commit()
    db.commit()
    logger.info("港股通完成: %d 条", total)

    # 股东户数
    logger.info("── 股东户数 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_holder_number(code, limit=5)
            if rows:
                stmt = mysql_insert(HolderNumber).values(rows)
                db.execute(stmt.on_duplicate_key_update(holder_num=stmt.inserted.holder_num))
                total += len(rows)
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            db.commit()
    db.commit()
    logger.info("股东户数完成: %d 条", total)

    # 涨跌停价
    logger.info("── 涨跌停价 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_stk_limit(code, days=5)
            if rows:
                stmt = mysql_insert(StkLimit).values(rows)
                db.execute(stmt.on_duplicate_key_update(
                    up_limit=stmt.inserted.up_limit, down_limit=stmt.inserted.down_limit,
                ))
                total += len(rows)
        except Exception:
            pass
        if (i + 1) % 200 == 0:
            db.commit()
    db.commit()
    logger.info("涨跌停价完成: %d 条", total)

    # 集合竞价
    logger.info("── 集合竞价 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            row = fetch_auction(code)
            if row:
                stmt = mysql_insert(AuctionData).values([row])
                db.execute(stmt.on_duplicate_key_update(
                    vol=stmt.inserted.vol, price=stmt.inserted.price,
                    amount=stmt.inserted.amount, turnover_rate=stmt.inserted.turnover_rate,
                    volume_ratio=stmt.inserted.volume_ratio,
                ))
                total += 1
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            db.commit()
    db.commit()
    logger.info("集合竞价完成: %d 条", total)

    # 增减持
    logger.info("── 增减持 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_holder_trade(code)
            for r in rows:
                db.add(HolderTrade(**r))
                total += 1
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            db.commit()
    db.commit()
    logger.info("增减持完成: %d 条", total)

    # 机构调研
    logger.info("── 机构调研 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_survey(code)
            for r in rows:
                db.add(InstitutionalSurvey(**r))
                total += 1
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            db.commit()
    db.commit()
    logger.info("机构调研完成: %d 条", total)

    # 限售解禁
    logger.info("── 限售解禁 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_share_float(code)
            for r in rows:
                db.add(ShareFloat(**r))
                total += 1
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            db.commit()
    db.commit()
    logger.info("限售解禁完成: %d 条", total)

    # 质押
    logger.info("── 股权质押 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            row = fetch_pledge_stat(code)
            if row:
                stmt = mysql_insert(PledgeStat).values([row])
                db.execute(stmt.on_duplicate_key_update(
                    pledge_count=stmt.inserted.pledge_count,
                    unrest_pledge=stmt.inserted.unrest_pledge,
                    rest_pledge=stmt.inserted.rest_pledge,
                    pledge_ratio=stmt.inserted.pledge_ratio,
                ))
                total += 1
        except Exception:
            pass
        if (i + 1) % 200 == 0:
            db.commit()
    db.commit()
    logger.info("股权质押完成: %d 条", total)

    # 回购
    logger.info("── 回购 ──")
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_repurchase(code)
            for r in rows:
                db.add(Repurchase(**r))
                total += 1
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            db.commit()
    db.commit()
    logger.info("回购完成: %d 条", total)

    # 券商金股
    logger.info("── 券商金股 ──")
    total = 0
    today = date.today()
    for m_offset in range(3):
        m = (today.replace(day=1) - timedelta(days=m_offset * 31)).strftime("%Y%m")
        try:
            rows = fetch_broker_recommend(month=m)
            for r in rows:
                db.add(BrokerRecommend(**r))
                total += 1
        except Exception:
            pass
    db.commit()
    logger.info("券商金股完成: %d 条", total)

    # 概念板块 + 成分股
    logger.info("── 概念板块 ──")
    try:
        sectors = fetch_concept_sectors()
        td = date.today().isoformat()
        for sector in sectors:
            sector["trade_date"] = td
            stmt = mysql_insert(ConceptSector).values([sector])
            db.execute(stmt.on_duplicate_key_update(
                sector_name=stmt.inserted.sector_name,
                member_count=stmt.inserted.member_count,
            ))
        db.commit()
        logger.info("概念板块: %d 个", len(sectors))

        # 成分股（只拉前20个板块）
        mem_total = 0
        for sector in sectors[:20]:
            sc = sector["sector_code"]
            members = fetch_concept_members(sc)
            if members:
                for m in members:
                    m["trade_date"] = td
                stmt = mysql_insert(ConceptSectorStock).values(members)
                db.execute(stmt.on_duplicate_key_update(name=stmt.inserted.name))
                mem_total += len(members)
        db.commit()
        logger.info("概念成分股: %d 条", mem_total)
    except Exception as e:
        logger.warning("概念板块失败: %s", e)

    # 涨停板
    logger.info("── 涨停板 ──")
    try:
        rows = fetch_limit_up_board()
        if rows:
            stmt = mysql_insert(LimitUpBoard).values(rows)
            db.execute(stmt.on_duplicate_key_update(
                name=stmt.inserted.name, pct_chg=stmt.inserted.pct_chg,
                limit_times=stmt.inserted.limit_times,
            ))
            db.commit()
            logger.info("涨停板: %d 条", len(rows))
    except Exception as e:
        logger.warning("涨停板失败: %s", e)

    # 龙虎榜（近30天）
    logger.info("── 龙虎榜 ──")
    try:
        for d_offset in range(30):
            td_dt = date.today() - timedelta(days=d_offset)
            td_str = td_dt.strftime("%Y%m%d")
            rows = fetch_dragon_tiger(td_str)
            if rows:
                stmt = mysql_insert(DragonTiger).values(rows)
                db.execute(stmt.on_duplicate_key_update(
                    name=stmt.inserted.name, reason=stmt.inserted.reason,
                    buy_amount_yi=stmt.inserted.buy_amount_yi,
                    sell_amount_yi=stmt.inserted.sell_amount_yi,
                    net_amount_yi=stmt.inserted.net_amount_yi,
                ))
        db.commit()
        logger.info("龙虎榜: 已完成")
    except Exception as e:
        logger.warning("龙虎榜失败: %s", e)

    # 资金流向
    logger.info("── 资金流向 ──")
    cf_total = 0
    for i, code in enumerate(codes):
        try:
            row = fetch_moneyflow(code)
            if row:
                stmt = mysql_insert(CapitalFlow).values([row])
                db.execute(stmt.on_duplicate_key_update(
                    main_net_inflow_yi=stmt.inserted.main_net_inflow_yi,
                    super_large_net_inflow_yi=stmt.inserted.super_large_net_inflow_yi,
                    large_net_inflow_yi=stmt.inserted.large_net_inflow_yi,
                    medium_net_inflow_yi=stmt.inserted.medium_net_inflow_yi,
                    small_net_inflow_yi=stmt.inserted.small_net_inflow_yi,
                    main_net_inflow_ratio=stmt.inserted.main_net_inflow_ratio,
                    cumulative_inflow_3d_yi=stmt.inserted.cumulative_inflow_3d_yi,
                    cumulative_inflow_5d_yi=stmt.inserted.cumulative_inflow_5d_yi,
                    cumulative_inflow_10d_yi=stmt.inserted.cumulative_inflow_10d_yi,
                ))
                cf_total += 1
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            db.commit()
    db.commit()
    logger.info("资金流向完成: %d 条", cf_total)


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="数据池重建")
    parser.add_argument("--dangerously-clear-data-pool", action="store_true", help="显式清空数据池表后再重建")
    parser.add_argument("--skip-csv", action="store_true")
    parser.add_argument("--skip-tushare-kbars", action="store_true")
    parser.add_argument("--skip-aux", action="store_true")
    parser.add_argument("--csv-dir", default="/Users/wangao/Downloads/用所选项目新建的文件夹")
    parser.add_argument("--tushare-top-n", type=int, default=5200)
    parser.add_argument("--aux-top-n", type=int, default=300)
    args = parser.parse_args()

    t0 = time.monotonic()
    db = SessionLocal()

    try:
        if args.dangerously_clear_data_pool:
            assert_destructive_allowed(database_url=settings.database_url, requested=True)
            clear_all_tables(db)
        else:
            logger.info("跳过清空数据池；如需清空请显式传 --dangerously-clear-data-pool")

        if not args.skip_csv:
            n_stocks, n_kbars = import_csv_files(db, args.csv_dir)
            logger.info("CSV: %d stocks, %d kbars", n_stocks, n_kbars)

        if not args.skip_tushare_kbars:
            n = fill_stock_list(db)
            logger.info("Tushare Stock: %d 新增", n)
            n = fill_kbars_tushare(db, top_n=args.tushare_top_n)
            logger.info("Tushare K线: %d 新增", n)

        if not args.skip_aux:
            backfill_aux_all(db, top_n=args.aux_top_n)
    finally:
        db.close()

    elapsed = time.monotonic() - t0
    logger.info("全部完成，耗时 %.1f 分钟", elapsed / 60)


if __name__ == "__main__":
    main()

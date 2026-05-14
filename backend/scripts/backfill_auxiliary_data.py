#!/usr/bin/env python3
"""
辅助数据回填脚本 — 一次补齐概念板块、龙虎榜、资金流向、新闻数据。

使用 akshare (同花顺源) + eastmoney (新闻API)，带限流控制。
建议首次运行后，每日通过推荐流水线自动增量更新。

用法:
    python scripts/backfill_auxiliary_data.py [--skip-concepts] [--skip-dragon-tiger]
                                            [--skip-capital-flows] [--skip-news]
                                            [--flow-top-n 500]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# 确保 backend 在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.core.config import get_settings
from app.models.tables import (
    UnifiedQuote,
    ConceptSector,
    ConceptSectorStock,
    CapitalFlow,
    DragonTiger,
    NewsAnnouncement,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_aux")

settings = get_settings()

# ── 限流 ──
_LAST_CALL = 0.0
_MIN_INTERVAL = 2.5  # akshare 调用间隔（秒）


def _throttle():
    global _LAST_CALL
    elapsed = time.monotonic() - _LAST_CALL
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_CALL = time.monotonic()


# ═════════════════════════════════════════════════════════════════════════════
# 概念板块
# ═════════════════════════════════════════════════════════════════════════════


def backfill_concept_sectors(db: Session) -> int:
    """
    获取全量概念板块列表及其成分股。
    使用 akshare 同花顺源。
    """
    import akshare as ak

    logger.info("── 开始回填概念板块 ──")

    # 1. 获取概念板块列表
    _throttle()
    try:
        boards_df = ak.stock_board_concept_name_ths()
    except Exception as e:
        logger.error("获取概念板块列表失败: %s", e)
        return 0

    if boards_df is None or boards_df.empty:
        logger.warning("概念板块列表为空")
        return 0

    trade_date = date.today().isoformat()
    total_sectors = 0
    total_stocks = 0

    for rank, (_, b_row) in enumerate(boards_df.iterrows(), start=1):
        sector_code = str(b_row.get("code", ""))
        sector_name = str(b_row.get("name", ""))

        # 检查是否已有今日数据
        existing = db.scalar(
            select(func.count()).select_from(ConceptSector).where(
                and_(
                    ConceptSector.sector_code == sector_code,
                    ConceptSector.trade_date == trade_date,
                )
            )
        )
        if existing:
            continue

        # 写入概念板块记录
        sector = ConceptSector(
            sector_code=sector_code,
            sector_name=sector_name,
            trade_date=trade_date,
            pct_chg=0.0,
            leading_code="",
            leading_name="",
            leading_pct=0.0,
            total_amount_yi=0.0,
            stock_count=0,
            limit_up_count=0,
            heat_rank=rank,
        )
        db.add(sector)
        total_sectors += 1

        # 2. 获取成分股（只对前60个热门板块）
        if rank <= 60:
            _throttle()
            try:
                info_df = ak.stock_board_concept_info_ths(symbol=sector_name)
                if info_df is not None and not info_df.empty:
                    member_count = 0
                    for _, s_row in info_df.iterrows():
                        raw_code = str(s_row.get("code", "") or s_row.get("代码", ""))
                        code = raw_code.replace("SH", "").replace("SZ", "").replace("BJ", "").strip()
                        if not code or len(code) != 6:
                            continue
                        existing_member = db.scalar(
                            select(func.count()).select_from(ConceptSectorStock).where(
                                and_(
                                    ConceptSectorStock.sector_code == sector_code,
                                    ConceptSectorStock.code == code,
                                    ConceptSectorStock.trade_date == trade_date,
                                )
                            )
                        )
                        if not existing_member:
                            db.add(ConceptSectorStock(
                                sector_code=sector_code,
                                code=code,
                                name=str(s_row.get("name", "") or s_row.get("名称", "")),
                                trade_date=trade_date,
                                pct_chg=0.0,
                                market_cap_yi=0.0,
                                is_leading=False,
                            ))
                            member_count += 1
                    total_stocks += member_count
                    if member_count > 0:
                        logger.debug("  板块 %s: %d 只成分股", sector_name, member_count)
            except Exception as e:
                logger.debug("获取板块 '%s' 成分股失败: %s", sector_name, e)

        # 每 10 个板块提交一次
        if total_sectors % 10 == 0:
            db.commit()
            logger.info("  概念板块进度: %d/%d", total_sectors, len(boards_df))

    db.commit()
    logger.info("概念板块完成: %d 个板块, %d 条成分股关联", total_sectors, total_stocks)
    return total_sectors


# ═════════════════════════════════════════════════════════════════════════════
# 龙虎榜
# ═════════════════════════════════════════════════════════════════════════════


def backfill_dragon_tiger(db: Session, days_back: int = 30) -> int:
    """
    回填龙虎榜数据（默认最近30个交易日）。
    使用 akshare (东方财富龙虎榜源)。
    """
    import akshare as ak

    logger.info("── 开始回填龙虎榜 (最近 %d 天) ──", days_back)

    end_date = date.today()
    total = 0

    # 按天拉取（避免单次数据量过大）
    current = end_date - timedelta(days=days_back)
    while current <= end_date:
        date_str = current.strftime("%Y%m%d")
        _throttle()
        try:
            df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        except Exception as e:
            logger.debug("龙虎榜 %s 拉取失败: %s", date_str, e)
            current += timedelta(days=1)
            continue

        if df is None or df.empty:
            current += timedelta(days=1)
            continue

        seen_today = set()  # 当日去重（同股同日多次上榜只保留第一条）
        day_count = 0
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code or len(code) != 6:
                continue

            trade_d = row.get("上榜日")
            if hasattr(trade_d, "strftime"):
                trade_d = trade_d.strftime("%Y-%m-%d")
            else:
                trade_d = str(trade_d)[:10]

            dedup_key = (code, trade_d)
            if dedup_key in seen_today:
                continue
            seen_today.add(dedup_key)

            # 检查数据库是否已有
            existing = db.scalar(
                select(func.count()).select_from(DragonTiger).where(
                    and_(
                        DragonTiger.code == code,
                        DragonTiger.trade_date == trade_d,
                    )
                )
            )
            if existing:
                continue

            db.add(DragonTiger(
                code=code,
                name=str(row.get("名称", "")),
                trade_date=trade_d,
                reason=str(row.get("上榜原因", "")),
                buy_amount_yi=round(float(row.get("龙虎榜买入额", 0) or 0) / 1e8, 4),
                sell_amount_yi=round(float(row.get("龙虎榜卖出额", 0) or 0) / 1e8, 4),
                net_amount_yi=round(float(row.get("龙虎榜净买额", 0) or 0) / 1e8, 4),
                buy_seat_count=0,
                sell_seat_count=0,
                institution_net_buy_yi=0.0,
                top_buy_broker="",
                top_sell_broker="",
            ))
            day_count += 1

        db.commit()
        total += day_count
        if day_count > 0:
            logger.info("  龙虎榜 %s: %d 条", trade_d, day_count)
        current += timedelta(days=1)

    logger.info("龙虎榜完成: 共 %d 条", total)
    return total


# ═════════════════════════════════════════════════════════════════════════════
# 资金流向
# ═════════════════════════════════════════════════════════════════════════════


def _get_top_n_codes_by_market_cap(db: Session, n: int = 500) -> list[str]:
    """按自由流通市值获取 Top N 股票代码。"""
    quotes = db.scalars(
        select(UnifiedQuote).order_by(UnifiedQuote.id.desc()).limit(5200)
    ).all()

    import json
    scored = []
    for uq in quotes:
        q = json.loads(uq.quote_json or "{}")
        free_mv = float(q.get("free_mv_yi") or 0)
        scored.append((uq.code, free_mv))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored[:n]]


def backfill_capital_flows(db: Session, top_n: int = 500) -> int:
    """
    回填资金流向数据（市值前 N 股票）。
    使用 akshare。
    """
    import akshare as ak

    logger.info("── 开始回填资金流向 (Top %d 市值) ──", top_n)

    codes = _get_top_n_codes_by_market_cap(db, top_n)
    if not codes:
        logger.warning("无法获取市值排行，跳过资金流向")
        return 0

    trade_date = date.today().isoformat()
    total = 0
    skipped = 0
    failed = 0

    for i, code in enumerate(codes):
        # 检查是否已有今日数据
        existing = db.scalar(
            select(func.count()).select_from(CapitalFlow).where(
                and_(
                    CapitalFlow.code == code,
                    CapitalFlow.trade_date == trade_date,
                )
            )
        )
        if existing:
            skipped += 1
            if i % 50 == 0:
                logger.info("  资金流向进度: %d/%d (跳过 %d)", i, len(codes), skipped)
            continue

        market = "sh" if code.startswith(("6", "9")) else "sz"
        _throttle()
        try:
            df = ak.stock_individual_fund_flow(stock=code, market=market)
        except Exception as e:
            logger.debug("资金流向 %s 拉取失败: %s", code, e)
            failed += 1
            continue

        if df is None or df.empty:
            failed += 1
            continue

        latest = df.iloc[0]
        tdate = latest.get("日期", trade_date)
        if hasattr(tdate, "strftime"):
            tdate = tdate.strftime("%Y-%m-%d")
        else:
            tdate = str(tdate)[:10]

        # 计算累计
        cum_3d = cum_5d = cum_10d = 0.0
        for j in range(min(len(df), 10)):
            r = df.iloc[j]
            net = float(r.get("主力净流入-净额", 0) or 0)
            if j < 3:
                cum_3d += net
            if j < 5:
                cum_5d += net
            cum_10d += net

        db.add(CapitalFlow(
            code=code,
            trade_date=tdate,
            main_net_inflow_yi=round(float(latest.get("主力净流入-净额", 0) or 0) / 1e8, 4),
            super_large_net_inflow_yi=round(float(latest.get("超大单净流入-净额", 0) or 0) / 1e8, 4),
            large_net_inflow_yi=round(float(latest.get("大单净流入-净额", 0) or 0) / 1e8, 4),
            medium_net_inflow_yi=round(float(latest.get("中单净流入-净额", 0) or 0) / 1e8, 4),
            small_net_inflow_yi=round(float(latest.get("小单净流入-净额", 0) or 0) / 1e8, 4),
            main_net_inflow_ratio=round(float(latest.get("主力净流入-净占比", 0) or 0), 2),
            cumulative_inflow_3d_yi=round(cum_3d / 1e8, 4),
            cumulative_inflow_5d_yi=round(cum_5d / 1e8, 4),
            cumulative_inflow_10d_yi=round(cum_10d / 1e8, 4),
        ))
        total += 1

        if i % 50 == 0:
            db.commit()
            logger.info("  资金流向进度: %d/%d (成功 %d, 跳过 %d, 失败 %d)",
                        i + 1, len(codes), total, skipped, failed)

    db.commit()
    logger.info("资金流向完成: 成功 %d, 跳过 %d, 失败 %d", total, skipped, failed)
    return total


# ═════════════════════════════════════════════════════════════════════════════
# 新闻/公告
# ═════════════════════════════════════════════════════════════════════════════


def backfill_news(db: Session, top_n: int = 500) -> int:
    """
    回填公告和新闻数据（市值前 N 股票）。
    使用 eastmoney np-anotice API（已验证可用）。
    """
    from app.services.market_data import _http_get_json

    logger.info("── 开始回填新闻公告 (Top %d 市值) ──", top_n)

    codes = _get_top_n_codes_by_market_cap(db, top_n)
    if not codes:
        logger.warning("无法获取市值排行，跳过新闻")
        return 0

    total = 0
    fallback_counter = 0

    for i, code in enumerate(codes):
        items = []

        # 公告
        try:
            ann_url = (
                "https://np-anotice-stock.eastmoney.com/api/security/ann"
                f"?page_size=15&page_index=1&ann_type=A"
                f"&stock_list={code}"
                "&column=NOTICE_COLUMN"
            )
            ann_data = _http_get_json(ann_url, settings)
            ann_rows = ann_data.get("data", {}).get("list") or []
            for row in ann_rows:
                title = str(row.get("title_ch", ""))
                is_risk = any(
                    kw in title
                    for kw in ["风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报"]
                )
                article_id = str(row.get("notice_id", ""))
                if not article_id:
                    fallback_counter += 1
                    article_id = f"em_ann_{code}_{fallback_counter}"
                items.append({
                    "source": "eastmoney",
                    "article_id": article_id,
                    "code": code,
                    "announce_date": str(row.get("notice_date", ""))[:10],
                    "title": title,
                    "summary": str(row.get("summary", "")),
                    "category": "announcement",
                    "sentiment": "negative" if is_risk else "neutral",
                    "is_risk_warning": is_risk,
                })
        except Exception as e:
            logger.debug("公告 %s 失败: %s", code, e)

        # 新闻
        try:
            news_url = (
                "https://np-anotice-stock.eastmoney.com/api/security/notify"
                f"?page_size=15&page_index=1"
                f"&stock_list={code}"
                "&column=NOTICE_COLUMN"
            )
            news_data = _http_get_json(news_url, settings)
            news_rows = news_data.get("data", {}).get("list") or []
            for row in news_rows:
                title = str(row.get("title_ch", ""))
                is_negative = any(
                    kw in title
                    for kw in ["风险", "亏损", "下滑", "减持", "处罚", "跌停", "爆雷", "问询"]
                )
                is_positive = any(
                    kw in title
                    for kw in ["增长", "预增", "利好", "突破", "签约", "中标", "回购", "增持", "分红"]
                )
                if is_negative:
                    sentiment = "negative"
                elif is_positive:
                    sentiment = "positive"
                else:
                    sentiment = "neutral"
                article_id = str(row.get("notice_id", ""))
                if not article_id:
                    fallback_counter += 1
                    article_id = f"em_news_{code}_{fallback_counter}"
                items.append({
                    "source": "eastmoney",
                    "article_id": article_id,
                    "code": code,
                    "announce_date": str(row.get("notice_date", ""))[:10],
                    "title": title,
                    "summary": str(row.get("summary", "")),
                    "category": "news",
                    "sentiment": sentiment,
                    "is_risk_warning": is_negative,
                })
        except Exception as e:
            logger.debug("新闻 %s 失败: %s", code, e)

        # 写入数据库（检查 source + article_id 组合键）
        for item in items:
            existing = db.scalar(
                select(func.count()).select_from(NewsAnnouncement).where(
                    and_(
                        NewsAnnouncement.source == item["source"],
                        NewsAnnouncement.article_id == item["article_id"],
                    )
                )
            )
            if not existing:
                try:
                    db.add(NewsAnnouncement(**item))
                    db.flush()
                    total += 1
                except Exception:
                    db.rollback()
                    continue

        if i % 50 == 0:
            db.commit()
            logger.info("  新闻进度: %d/%d (累计 %d 条)", i, len(codes), total)

        # 新闻API不需要akshare限流，但加点延迟避免被封
        time.sleep(0.3)

    db.commit()
    logger.info("新闻公告完成: 共 %d 条", total)
    return total


# ═════════════════════════════════════════════════════════════════════════════
# 主入口
# ═════════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="回填辅助数据")
    parser.add_argument("--skip-concepts", action="store_true")
    parser.add_argument("--skip-dragon-tiger", action="store_true")
    parser.add_argument("--skip-capital-flows", action="store_true")
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--flow-top-n", type=int, default=500)
    parser.add_argument("--news-top-n", type=int, default=500)
    parser.add_argument("--dragon-tiger-days", type=int, default=30)
    args = parser.parse_args()

    t0 = time.monotonic()
    db = SessionLocal()

    try:
        if not args.skip_concepts:
            n = backfill_concept_sectors(db)
            logger.info("✓ 概念板块: %d 个板块", n)
        else:
            logger.info("⊘ 跳过概念板块")

        if not args.skip_dragon_tiger:
            n = backfill_dragon_tiger(db, days_back=args.dragon_tiger_days)
            logger.info("✓ 龙虎榜: %d 条", n)
        else:
            logger.info("⊘ 跳过龙虎榜")

        if not args.skip_capital_flows:
            n = backfill_capital_flows(db, top_n=args.flow_top_n)
            logger.info("✓ 资金流向: %d 只股票", n)
        else:
            logger.info("⊘ 跳过资金流向")

        if not args.skip_news:
            n = backfill_news(db, top_n=args.news_top_n)
            logger.info("✓ 新闻公告: %d 条", n)
        else:
            logger.info("⊘ 跳过新闻")

    finally:
        db.close()

    elapsed = time.monotonic() - t0
    logger.info("全部完成，耗时 %.1f 分钟", elapsed / 60)


if __name__ == "__main__":
    main()

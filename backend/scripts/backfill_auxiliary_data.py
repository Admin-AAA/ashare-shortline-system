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
    DailyKbar,
    MarketSnapshot,
    StockQuote,
    ConceptSector,
    ConceptSectorStock,
    CapitalFlow,
    DragonTiger,
    NewsAnnouncement,
    MarginData,
    HkHolding,
    HolderNumber,
    StkLimit,
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


def _safe_float(val) -> float | None:
    """转 float，NaN/Inf 返回 None。"""
    import math
    try:
        v = float(val or 0)
    except (ValueError, TypeError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _safe_yi(val) -> float | None:
    """转 float 后转为亿单位，NaN/Inf 返回 None。"""
    v = _safe_float(val)
    return round(v / 1e8, 4) if v else None


def _normalize_trade_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]


def _compact_trade_date(value) -> str:
    return _normalize_trade_date(value).replace("-", "")


def _resolve_snapshot_trade_date(db: Session, requested: str | None = None) -> str:
    if requested:
        return _normalize_trade_date(requested)

    latest_snapshot = db.scalar(select(MarketSnapshot.trade_date).order_by(MarketSnapshot.trade_date.desc()).limit(1))
    if latest_snapshot:
        return _normalize_trade_date(latest_snapshot)

    latest_quote = db.scalar(select(func.max(StockQuote.trade_date)))
    if latest_quote:
        return _normalize_trade_date(latest_quote)

    latest_bar = db.scalar(select(func.max(DailyKbar.trade_date)))
    if latest_bar:
        return _normalize_trade_date(latest_bar)

    return date.today().isoformat()


def _parse_trade_day(value) -> date | None:
    normalized = _normalize_trade_date(value)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _date_on_or_before(value, target_trade_date: str) -> bool:
    day = _parse_trade_day(value)
    if day is None:
        return False
    return day <= date.fromisoformat(target_trade_date)


# ═════════════════════════════════════════════════════════════════════════════
# 概念板块
# ═════════════════════════════════════════════════════════════════════════════


def backfill_concept_sectors(db: Session, trade_date: str | None = None) -> int:
    """
    获取全量概念板块列表及其成分股。
    使用 akshare 同花顺源。
    """
    import akshare as ak

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填概念板块 (%s) ──", target_trade_date)

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
                    ConceptSector.trade_date == target_trade_date,
                )
            )
        )
        if existing:
            continue

        # 写入概念板块记录
        sector = ConceptSector(
            sector_code=sector_code,
            sector_name=sector_name,
            trade_date=target_trade_date,
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
                                    ConceptSectorStock.trade_date == target_trade_date,
                                )
                            )
                        )
                        if not existing_member:
                            db.add(ConceptSectorStock(
                                sector_code=sector_code,
                                code=code,
                                name=str(s_row.get("name", "") or s_row.get("名称", "")),
                                trade_date=target_trade_date,
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


def backfill_dragon_tiger(db: Session, days_back: int = 30, trade_date: str | None = None) -> int:
    """
    回填龙虎榜数据（默认最近30个交易日）。
    使用 Tushare top_list（自建服务器），eastmoney 备用。
    """
    from app.services.tushare_client import fetch_moneyflow

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填龙虎榜 (截至 %s，回看 %d 天) ──", target_trade_date, days_back)

    end_date = date.fromisoformat(target_trade_date)
    total = 0

    current = end_date - timedelta(days=days_back)
    while current <= end_date:
        date_str = current.strftime("%Y%m%d")
        df = None

        # 优先 Tushare
        try:
            pro = get_pro()
            df = pro.top_list(trade_date=date_str)
        except Exception as e:
            logger.debug("Tushare龙虎榜 %s 拉取失败: %s，回退eastmoney", date_str, e)

        # 回退 eastmoney/akshare
        if df is None or df.empty:
            try:
                import akshare as ak
                _throttle()
                df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
            except Exception as e:
                logger.debug("akshare龙虎榜 %s 拉取失败: %s", date_str, e)
                current += timedelta(days=1)
                continue

        if df is None or df.empty:
            current += timedelta(days=1)
            continue

        seen_today = set()
        day_count = 0
        for _, row in df.iterrows():
            # 兼容 Tushare 和 akshare 字段名
            code = str(row.get("ts_code", "") or row.get("代码", ""))
            if "." in code:
                code = code.split(".")[0]
            if not code or len(code) != 6:
                continue

            name = str(row.get("name", "") or row.get("名称", ""))
            reason = str(row.get("reason", "") or row.get("上榜原因", ""))

            trade_d = row.get("trade_date") or row.get("上榜日")
            if hasattr(trade_d, "strftime"):
                trade_d = trade_d.strftime("%Y-%m-%d")
            else:
                trade_d = str(trade_d)[:10].replace("-", "")[:8]
                if len(trade_d) == 8:
                    trade_d = f"{trade_d[:4]}-{trade_d[4:6]}-{trade_d[6:8]}"

            dedup_key = (code, trade_d)
            if dedup_key in seen_today:
                continue
            seen_today.add(dedup_key)

            existing = db.scalar(
                select(func.count()).select_from(DragonTiger).where(
                    and_(DragonTiger.code == code, DragonTiger.trade_date == trade_d)
                )
            )
            if existing:
                continue

            # 从 Tushare 字段提取金额
            buy_amt = float(row.get("l_buy", 0) or row.get("龙虎榜买入额", 0) or 0)
            sell_amt = float(row.get("l_sell", 0) or row.get("龙虎榜卖出额", 0) or 0)
            net_amt = float(row.get("net_amount", 0) or row.get("龙虎榜净买额", 0) or 0)

            db.add(DragonTiger(
                code=code,
                name=name,
                trade_date=trade_d,
                reason=reason,
                buy_amount_yi=_safe_yi(buy_amt),
                sell_amount_yi=_safe_yi(sell_amt),
                net_amount_yi=_safe_yi(net_amt),
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


def _get_top_n_codes_by_market_cap(db: Session, n: int = 500, trade_date: str | None = None) -> list[str]:
    """按自由流通市值获取 Top N 股票代码。"""
    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    quotes = db.scalars(
        select(StockQuote)
        .where(StockQuote.trade_date == target_trade_date)
        .order_by(StockQuote.free_mv_yi.is_(None), StockQuote.free_mv_yi.desc())
        .limit(n)
    ).all()
    if len(quotes) >= n:
        return [q.code for q in quotes if q.code]

    fallback_rows = db.scalars(
        select(StockQuote)
        .where(StockQuote.trade_date <= target_trade_date)
        .order_by(StockQuote.trade_date.desc(), StockQuote.free_mv_yi.is_(None), StockQuote.free_mv_yi.desc())
    ).all()
    latest_by_code: dict[str, StockQuote] = {}
    for row in fallback_rows:
        if row.code and row.code not in latest_by_code:
            latest_by_code[row.code] = row
    ranked = sorted(
        latest_by_code.values(),
        key=lambda row: (row.free_mv_yi is None, -(float(row.free_mv_yi or 0))),
    )
    return [q.code for q in ranked[:n] if q.code]


def backfill_capital_flows(db: Session, top_n: int = 500, trade_date: str | None = None) -> int:
    """
    回填资金流向数据（市值前 N 股票）。
    使用 Tushare moneyflow（自建服务器），eastmoney 备用。
    """
    from app.services.tushare_client import get_pro

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填资金流向 (%s Top %d 市值) ──", target_trade_date, top_n)

    codes = _get_top_n_codes_by_market_cap(db, top_n, target_trade_date)
    if not codes:
        logger.warning("无法获取市值排行，跳过资金流向")
        return 0

    total = 0
    skipped = 0
    failed = 0

    for i, code in enumerate(codes):
        existing = db.scalar(
            select(func.count()).select_from(CapitalFlow).where(
                and_(CapitalFlow.code == code, CapitalFlow.trade_date == target_trade_date)
            )
        )
        if existing:
            skipped += 1
            if i % 50 == 0:
                logger.info("  资金流向进度: %d/%d (跳过 %d)", i, len(codes), skipped)
            continue

        try:
            flow_data = fetch_moneyflow(code, days=10, trade_date=target_trade_date)
        except Exception as e:
            logger.debug("Tushare资金流向 %s 失败: %s", code, e)
            flow_data = None

        if flow_data is None or _normalize_trade_date(flow_data.get("trade_date")) != target_trade_date:
            failed += 1
            continue

        db.add(CapitalFlow(**flow_data))
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


def backfill_news(db: Session, top_n: int = 500, trade_date: str | None = None) -> int:
    """
    回填公告和新闻数据（市值前 N 股票）。
    优先使用 Tushare anns_d（自建服务器），失败时回退 eastmoney np-anotice API。
    """
    from app.services.market_data import _http_get_json

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填新闻公告 (%s Top %d 市值) ──", target_trade_date, top_n)

    codes = _get_top_n_codes_by_market_cap(db, top_n, target_trade_date)
    if not codes:
        logger.warning("无法获取市值排行，跳过新闻")
        return 0

    # 尝试 Tushare 公告接口（批量）
    tushare_ann_count = 0
    try:
        from scripts.tushare_init import get_pro
        pro = get_pro()
        td_compact = target_trade_date.replace("-", "")
        df = pro.anns_d(trade_date=td_compact)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("ts_code", "")).split(".")[0]
                if not code or len(code) != 6 or code not in codes:
                    continue
                title = str(row.get("title", ""))
                is_risk = any(
                    kw in title
                    for kw in ["风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询", "通报"]
                )
                article_id = f"tushare_ann_{code}_{td_compact}_{hash(title) & 0x7FFFFFFF}"
                existing = db.scalar(
                    select(func.count()).select_from(NewsAnnouncement).where(
                        and_(NewsAnnouncement.source == "tushare", NewsAnnouncement.article_id == article_id)
                    )
                )
                if not existing:
                    db.add(NewsAnnouncement(
                        source="tushare",
                        article_id=article_id,
                        code=code,
                        announce_date=target_trade_date,
                        title=title,
                        summary=str(row.get("desc", ""))[:500],
                        category="announcement",
                        sentiment="negative" if is_risk else "neutral",
                        is_risk_warning=is_risk,
                    ))
                    tushare_ann_count += 1
            db.commit()
            logger.info("  Tushare 公告: %d 条", tushare_ann_count)
    except Exception as e:
        logger.debug("Tushare anns_d 不可用: %s，回退 eastmoney", e)

    # eastmoney 作为备用/补充
    total = tushare_ann_count
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
        items = [
            {**item, "announce_date": _normalize_trade_date(item.get("announce_date"))}
            for item in items
            if _date_on_or_before(item.get("announce_date"), target_trade_date)
        ]
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
# Tushare 专属数据（融资融券 / 港股通持股 / 股东户数 / 涨跌停价）
# ═════════════════════════════════════════════════════════════════════════════


def backfill_margin(db: Session, codes: list[str], days: int = 5, trade_date: str | None = None) -> int:
    """回填融资融券数据（Tushare margin）。"""
    from app.services.tushare_client import fetch_margin
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_margin(code, days=days, trade_date=target_trade_date)
            if not rows:
                continue
            rows = [row for row in rows if _date_on_or_before(row.get("trade_date"), target_trade_date)]
            for row in rows:
                row["code"] = code
            if not rows:
                continue
            stmt = mysql_insert(MarginData).values(rows)
            db.execute(stmt.on_duplicate_key_update(
                rzye=stmt.inserted.rzye,
                rqye=stmt.inserted.rqye,
                rzmre=stmt.inserted.rzmre,
                rzche=stmt.inserted.rzche,
                rzrqye=stmt.inserted.rzrqye,
                created_at=datetime.now(),
            ))
            total += len(rows)
        except Exception as e:
            logger.debug("融资融券 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info("  融资融券进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_hk_hold(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填港股通持股数据（Tushare hk_hold）。"""
    from app.services.tushare_client import fetch_hk_hold
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            row = fetch_hk_hold(code, trade_date=target_trade_date)
            if not row or not _date_on_or_before(row.get("trade_date"), target_trade_date):
                continue
            stmt = mysql_insert(HkHolding).values([row])
            db.execute(stmt.on_duplicate_key_update(
                vol=stmt.inserted.vol,
                ratio=stmt.inserted.ratio,
                created_at=datetime.now(),
            ))
            total += 1
        except Exception as e:
            logger.debug("港股通持股 %s 失败: %s", code, e)
        if (i + 1) % 200 == 0:
            db.commit()
            logger.info("  港股通持股进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_holder_number(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填股东户数数据（Tushare stk_holdernumber）。"""
    from app.services.tushare_client import fetch_holder_number
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_holder_number(code, limit=4, trade_date=target_trade_date)
            if not rows:
                continue
            rows = [row for row in rows if _date_on_or_before(row.get("end_date"), target_trade_date)]
            for row in rows:
                row["code"] = code
            if not rows:
                continue
            stmt = mysql_insert(HolderNumber).values(rows)
            db.execute(stmt.on_duplicate_key_update(
                holder_num=stmt.inserted.holder_num,
                created_at=datetime.now(),
            ))
            total += len(rows)
        except Exception as e:
            logger.debug("股东户数 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info("  股东户数进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_stk_limit(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填涨跌停价格（Tushare stk_limit）。"""
    from app.services.tushare_client import fetch_stk_limit
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_stk_limit(code, days=3, trade_date=target_trade_date)
            if not rows:
                continue
            rows = [row for row in rows if _date_on_or_before(row.get("trade_date"), target_trade_date)]
            for row in rows:
                row["code"] = code
            if not rows:
                continue
            stmt = mysql_insert(StkLimit).values(rows)
            db.execute(stmt.on_duplicate_key_update(
                up_limit=stmt.inserted.up_limit,
                down_limit=stmt.inserted.down_limit,
                created_at=datetime.now(),
            ))
            total += len(rows)
        except Exception as e:
            logger.debug("涨跌停价 %s 失败: %s", code, e)
        if (i + 1) % 200 == 0:
            db.commit()
            logger.info("  涨跌停价进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


# ═════════════════════════════════════════════════════════════════════════════
# Tushare 新增数据（集合竞价 / 券商金股 / 增减持 / 调研 / 解禁 / 质押 / 回购）
# ═════════════════════════════════════════════════════════════════════════════


def backfill_minute_kbars(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填当日分钟K线（腾讯 ifzq API）。"""
    from app.services.tushare_client import fetch_minute_kbars
    from app.models.tables import MinuteKbar
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填分钟K线 (%s) ──", target_trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            bars = fetch_minute_kbars(code, trade_date=target_trade_date)
            bars = [bar for bar in bars if _normalize_trade_date(bar.get("trade_date")) == target_trade_date]
            if bars:
                stmt = mysql_insert(MinuteKbar).values(bars).prefix_with("IGNORE")
                db.execute(stmt)
                total += len(bars)
        except Exception as e:
            logger.debug("分钟K线 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info("  分钟K线进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_auction(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填集合竞价数据。"""
    from app.services.tushare_client import fetch_auction
    from app.models.tables import AuctionData

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填集合竞价 (%s) ──", target_trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            row = fetch_auction(code, trade_date=target_trade_date)
            if row and _normalize_trade_date(row.get("trade_date")) == target_trade_date:
                existing = db.scalar(
                    select(func.count()).select_from(AuctionData).where(
                        and_(AuctionData.code == code, AuctionData.trade_date == row["trade_date"])
                    )
                )
                if not existing:
                    db.add(AuctionData(**row))
                    db.flush()
                    total += 1
        except Exception as e:
            logger.debug("集合竞价 %s 失败: %s", code, e)
        if (i + 1) % 200 == 0:
            db.commit()
            logger.info("  集合竞价进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_broker_recommend(db: Session, months: int = 3, trade_date: str | None = None) -> int:
    """回填券商金股。"""
    from app.services.tushare_client import fetch_broker_recommend
    from app.models.tables import BrokerRecommend

    target_day = date.fromisoformat(_resolve_snapshot_trade_date(db, trade_date))
    logger.info("── 开始回填券商金股 (截至 %s) ──", target_day.isoformat())
    total = 0
    for m_offset in range(months):
        m = (target_day.replace(day=1) - timedelta(days=m_offset * 31)).strftime("%Y%m")
        try:
            rows = fetch_broker_recommend(month=m)
            for row in rows:
                existing = db.scalar(
                    select(func.count()).select_from(BrokerRecommend).where(
                        and_(
                            BrokerRecommend.code == row["code"],
                            BrokerRecommend.month == row["month"],
                            BrokerRecommend.broker == row["broker"],
                        )
                    )
                )
                if not existing:
                    db.add(BrokerRecommend(**row))
                    db.flush()
                    total += 1
        except Exception as e:
            logger.debug("券商金股 %s 失败: %s", m, e)
    db.commit()
    return total


def backfill_holder_trades(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填股东增减持数据。"""
    from app.services.tushare_client import fetch_holder_trade
    from app.models.tables import HolderTrade

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填增减持 (截至 %s) ──", target_trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_holder_trade(code)
            rows = [row for row in rows if _date_on_or_before(row.get("ann_date"), target_trade_date)]
            for row in rows:
                db.add(HolderTrade(**row))
                total += 1
        except Exception as e:
            logger.debug("增减持 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info("  增减持进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_surveys(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填机构调研数据。"""
    from app.services.tushare_client import fetch_survey
    from app.models.tables import InstitutionalSurvey

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填机构调研 (截至 %s) ──", target_trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_survey(code)
            rows = [row for row in rows if _date_on_or_before(row.get("surv_date"), target_trade_date)]
            for row in rows:
                db.add(InstitutionalSurvey(**row))
                total += 1
        except Exception as e:
            logger.debug("机构调研 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info("  机构调研进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_share_floats(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填限售解禁数据。"""
    from app.services.tushare_client import fetch_share_float
    from app.models.tables import ShareFloat

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填限售解禁 (截至 %s 可知) ──", target_trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_share_float(code)
            rows = [
                row for row in rows
                if not row.get("ann_date") or _date_on_or_before(row.get("ann_date"), target_trade_date)
            ]
            for row in rows:
                db.add(ShareFloat(**row))
                total += 1
        except Exception as e:
            logger.debug("限售解禁 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info("  限售解禁进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_pledge_stats(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填股权质押数据。"""
    from app.services.tushare_client import fetch_pledge_stat
    from app.models.tables import PledgeStat

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填股权质押 (截至 %s) ──", target_trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            row = fetch_pledge_stat(code)
            if row and _date_on_or_before(row.get("end_date"), target_trade_date):
                existing = db.scalar(
                    select(func.count()).select_from(PledgeStat).where(
                        and_(PledgeStat.code == code, PledgeStat.end_date == row["end_date"])
                    )
                )
                if not existing:
                    db.add(PledgeStat(**row))
                    db.flush()
                    total += 1
        except Exception as e:
            logger.debug("股权质押 %s 失败: %s", code, e)
        if (i + 1) % 200 == 0:
            db.commit()
            logger.info("  股权质押进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
    return total


def backfill_repurchases(db: Session, codes: list[str], trade_date: str | None = None) -> int:
    """回填回购数据。"""
    from app.services.tushare_client import fetch_repurchase
    from app.models.tables import Repurchase

    target_trade_date = _resolve_snapshot_trade_date(db, trade_date)
    logger.info("── 开始回填回购 (截至 %s) ──", target_trade_date)
    total = 0
    for i, code in enumerate(codes):
        try:
            rows = fetch_repurchase(code)
            rows = [
                row for row in rows
                if not row.get("ann_date") or _date_on_or_before(row.get("ann_date"), target_trade_date)
            ]
            for row in rows:
                db.add(Repurchase(**row))
                total += 1
        except Exception as e:
            logger.debug("回购 %s 失败: %s", code, e)
        if (i + 1) % 100 == 0:
            db.commit()
            logger.info("  回购进度: %d/%d (累计 %d 条)", i + 1, len(codes), total)
    db.commit()
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
    parser.add_argument("--skip-tushare-aux", action="store_true", help="跳过 Tushare 专属数据（融资融券/港股通/股东户数/涨跌停价）")
    parser.add_argument("--skip-tushare-new", action="store_true", help="跳过 Tushare 新增数据（集合竞价/增减持/调研/解禁/质押/回购/券商金股）")
    parser.add_argument("--flow-top-n", type=int, default=500)
    parser.add_argument("--news-top-n", type=int, default=500)
    parser.add_argument("--dragon-tiger-days", type=int, default=30)
    parser.add_argument("--tushare-aux-top-n", type=int, default=300, help="Tushare 辅助数据覆盖股票数")
    parser.add_argument("--tushare-aux-days", type=int, default=5, help="融资融券回看天数")
    parser.add_argument("--trade-date", default=None, help="快照交易日，YYYY-MM-DD 或 YYYYMMDD；为空则使用最新 market snapshot")
    args = parser.parse_args()

    t0 = time.monotonic()
    db = SessionLocal()

    try:
        target_trade_date = _resolve_snapshot_trade_date(db, args.trade_date)
        logger.info("辅助数据回填目标交易日: %s", target_trade_date)

        if not args.skip_concepts:
            n = backfill_concept_sectors(db, trade_date=target_trade_date)
            logger.info("✓ 概念板块: %d 个板块", n)
        else:
            logger.info("⊘ 跳过概念板块")

        if not args.skip_dragon_tiger:
            n = backfill_dragon_tiger(db, days_back=args.dragon_tiger_days, trade_date=target_trade_date)
            logger.info("✓ 龙虎榜: %d 条", n)
        else:
            logger.info("⊘ 跳过龙虎榜")

        if not args.skip_capital_flows:
            n = backfill_capital_flows(db, top_n=args.flow_top_n, trade_date=target_trade_date)
            logger.info("✓ 资金流向: %d 只股票", n)
        else:
            logger.info("⊘ 跳过资金流向")

        if not args.skip_news:
            n = backfill_news(db, top_n=args.news_top_n, trade_date=target_trade_date)
            logger.info("✓ 新闻公告: %d 条", n)
        else:
            logger.info("⊘ 跳过新闻")

        if not args.skip_tushare_aux:
            logger.info("── Tushare 专属辅助数据回填 ──")
            top_codes = _get_top_n_codes_by_market_cap(db, n=args.tushare_aux_top_n, trade_date=target_trade_date)
            logger.info("  目标股票数: %d", len(top_codes))

            n = backfill_margin(db, top_codes, days=args.tushare_aux_days, trade_date=target_trade_date)
            logger.info("✓ 融资融券: %d 条", n)

            n = backfill_hk_hold(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 港股通持股: %d 条", n)

            n = backfill_holder_number(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 股东户数: %d 条", n)

            n = backfill_stk_limit(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 涨跌停价: %d 条", n)
        else:
            logger.info("⊘ 跳过 Tushare 专属数据")

        if not args.skip_tushare_new:
            logger.info("── Tushare 新增数据回填 ──")
            top_codes = _get_top_n_codes_by_market_cap(db, n=args.tushare_aux_top_n, trade_date=target_trade_date)
            logger.info("  目标股票数: %d", len(top_codes))

            n = backfill_minute_kbars(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 分钟K线: %d 条", n)

            n = backfill_auction(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 集合竞价: %d 条", n)

            n = backfill_holder_trades(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 增减持: %d 条", n)

            n = backfill_surveys(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 机构调研: %d 条", n)

            n = backfill_share_floats(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 限售解禁: %d 条", n)

            n = backfill_pledge_stats(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 股权质押: %d 条", n)

            n = backfill_repurchases(db, top_codes, trade_date=target_trade_date)
            logger.info("✓ 回购: %d 条", n)

            n = backfill_broker_recommend(db, months=3, trade_date=target_trade_date)
            logger.info("✓ 券商金股: %d 条", n)
        else:
            logger.info("⊘ 跳过 Tushare 新增数据")

    finally:
        db.close()

    elapsed = time.monotonic() - t0
    logger.info("全部完成，耗时 %.1f 分钟", elapsed / 60)


if __name__ == "__main__":
    main()

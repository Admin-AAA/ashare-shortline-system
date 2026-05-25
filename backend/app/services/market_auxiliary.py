"""
辅助数据获取（Tushare-only）。
涨停板、概念板块、资金流向、龙虎榜均通过自建 Tushare 服务器获取。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.core.config import get_settings
from app.services.market_data import _split_sources

logger = logging.getLogger(__name__)


def _limit_up_sources(settings: Any) -> list[str]:
    return _split_sources(settings.limit_up_sources)


def _concept_sector_sources(settings: Any) -> list[str]:
    return _split_sources(settings.concept_sector_sources)


def _capital_flow_sources(settings: Any) -> list[str]:
    return _split_sources(settings.capital_flow_sources)


def _dragon_tiger_sources(settings: Any) -> list[str]:
    return _split_sources(settings.dragon_tiger_sources)


def _news_sources(settings: Any) -> list[str]:
    return _split_sources(settings.news_sources)


# ── Dispatch ────────────────────────────────────────────────────────────

def _normalize_trade_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]


def _compact_trade_date(value: Any) -> str:
    return _normalize_trade_date(value).replace("-", "")


def _fetch_limit_up_board(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "tushare":
        return _fetch_tushare_limit_up_board(settings, trade_date=trade_date)
    return []


def _fetch_concept_sectors(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "tushare":
        return _fetch_tushare_concept_sectors(settings, trade_date=trade_date)
    return []


def _fetch_capital_flow(source: str, code: str, settings: Any, trade_date: str | None = None) -> dict[str, Any] | None:
    if source == "tushare":
        return _fetch_tushare_moneyflow(code, settings, trade_date=trade_date)
    return None


def _fetch_dragon_tiger(source: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "tushare":
        return _fetch_tushare_dragon_tiger(settings, trade_date=trade_date)
    return []


def _fetch_news(source: str, code: str, settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    if source == "eastmoney":
        return _fetch_eastmoney_news(code, settings)
    return []


# ── Tushare 实现 ────────────────────────────────────────────────────────

def _fetch_tushare_moneyflow(code: str, settings: Any, trade_date: str | None = None) -> dict[str, Any] | None:
    try:
        from app.services.tushare_client import get_pro

        pro = get_pro()
        ts_code = f"{code}.{'SH' if code.startswith(('6', '9')) else 'SZ'}"
        target = _compact_trade_date(trade_date)
        if target:
            end_day = date.fromisoformat(_normalize_trade_date(target))
            start = (end_day - timedelta(days=30)).strftime("%Y%m%d")
            try:
                df = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=target)
            except Exception:
                df = pro.moneyflow(ts_code=ts_code, trade_date=target)
        else:
            df = pro.moneyflow(ts_code=ts_code, limit=10)
        if df is None or df.empty:
            return None

        df = df.sort_values("trade_date", ascending=False)
        if target:
            df = df[df["trade_date"].astype(str) <= target]
            if df.empty or str(df.iloc[0].get("trade_date", "")) != target:
                return None
        latest = df.iloc[0]
        row_trade_date = _normalize_trade_date(latest.get("trade_date", target))

        cum_3d = 0.0
        cum_5d = 0.0
        cum_10d = 0.0
        for i in range(min(len(df), 10)):
            row = df.iloc[i]
            net_mf = float(row.get("net_mf_amount") or 0)
            if i < 3:
                cum_3d += net_mf
            if i < 5:
                cum_5d += net_mf
            cum_10d += net_mf

        return {
            "code": code,
            "trade_date": row_trade_date,
            "main_net_inflow_yi": round(float(latest.get("net_mf_amount") or 0) / 1e8, 4),
            "super_large_net_inflow_yi": round(
                float(latest.get("buy_elg_amount") or 0) / 1e8
                - float(latest.get("sell_elg_amount") or 0) / 1e8,
                4,
            ),
            "large_net_inflow_yi": round(
                float(latest.get("buy_lg_amount") or 0) / 1e8
                - float(latest.get("sell_lg_amount") or 0) / 1e8,
                4,
            ),
            "medium_net_inflow_yi": round(
                float(latest.get("buy_md_amount") or 0) / 1e8
                - float(latest.get("sell_md_amount") or 0) / 1e8,
                4,
            ),
            "small_net_inflow_yi": round(
                float(latest.get("buy_sm_amount") or 0) / 1e8
                - float(latest.get("sell_sm_amount") or 0) / 1e8,
                4,
            ),
            "main_net_inflow_ratio": round(float(latest.get("net_mf_vol") or 0), 2),
            "cumulative_inflow_3d_yi": round(cum_3d / 1e8, 4),
            "cumulative_inflow_5d_yi": round(cum_5d / 1e8, 4),
            "cumulative_inflow_10d_yi": round(cum_10d / 1e8, 4),
        }
    except Exception as e:
        logger.debug("Tushare资金流向不可用 (%s): %s", code, e)
        return None


def _fetch_tushare_dragon_tiger(settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from app.services.tushare_client import fetch_dragon_tiger

        return fetch_dragon_tiger(_compact_trade_date(trade_date) or None)
    except Exception as e:
        logger.warning("Tushare龙虎榜不可用: %s", e)
        return []


def _fetch_tushare_limit_up_board(settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from app.services.tushare_client import fetch_limit_up_board

        rows = fetch_limit_up_board(_compact_trade_date(trade_date) or None)
        for row in rows:
            row.setdefault("fd_amount_yi", float(row.pop("fd_amount", 0) or 0))
        return rows
    except Exception as e:
        logger.warning("Tushare涨停板不可用: %s", e)
        return []


def _fetch_tushare_concept_sectors(settings: Any, trade_date: str | None = None) -> list[dict[str, Any]]:
    try:
        from app.services.tushare_client import fetch_concept_sectors, fetch_latest_trade_date

        rows = fetch_concept_sectors()
        target_trade_date = _normalize_trade_date(trade_date) or _normalize_trade_date(fetch_latest_trade_date())
        for rank, row in enumerate(rows, 1):
            row["trade_date"] = target_trade_date
            row["heat_rank"] = rank
        return rows
    except Exception as e:
        logger.warning("Tushare概念板块不可用: %s", e)
        return []


# ── 东方财富新闻 ──────────────────────────────────────────────────────


def _fetch_eastmoney_news(code: str, settings: Any) -> list[dict[str, Any]]:
    """东方财富个股公告+新闻。"""
    from app.services.market_data import _http_get_json

    items: list[dict[str, Any]] = []

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
            items.append({
                "source": "eastmoney",
                "article_id": str(row.get("notice_id", "")),
                "code": code,
                "announce_date": str(row.get("notice_date", ""))[:10],
                "title": title,
                "summary": str(row.get("summary", "")),
                "category": "announcement",
                "sentiment": "negative" if is_risk else "neutral",
                "is_risk_warning": is_risk,
            })
    except Exception:
        pass

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
            items.append({
                "source": "eastmoney",
                "article_id": str(row.get("notice_id", "")),
                "code": code,
                "announce_date": str(row.get("notice_date", ""))[:10],
                "title": title,
                "summary": str(row.get("summary", "")),
                "category": "news",
                "sentiment": sentiment,
                "is_risk_warning": is_negative,
            })
    except Exception:
        pass

    return items

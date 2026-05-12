from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.core.config import get_settings
from app.services.market_data import (
    HTTP_HEADERS,
    MarketDataError,
    _http_get_json,
    _normalize_code,
    _split_sources,
    _eastmoney_market_id,
    _thsdk_client,
    _thsdk_rows,
    _num,
)

logger = logging.getLogger(__name__)


# ── 源定义 ─────────────────────────────────────────────────────────────

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

def _fetch_limit_up_board(source: str, settings: Any) -> list[dict[str, Any]]:
    if source == "eastmoney":
        return _fetch_eastmoney_limit_up_board(settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_limit_up_board(settings)
    return []


def _fetch_concept_sectors(source: str, settings: Any) -> list[dict[str, Any]]:
    if source == "eastmoney":
        return _fetch_eastmoney_concept_sectors(settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_concept_sectors(settings)
    return []


def _fetch_capital_flow(source: str, code: str, settings: Any) -> dict[str, Any] | None:
    if source == "eastmoney":
        return _fetch_eastmoney_capital_flow(code, settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_capital_flow(code, settings)
    return None


def _fetch_dragon_tiger(source: str, settings: Any) -> list[dict[str, Any]]:
    if source == "eastmoney":
        return _fetch_eastmoney_dragon_tiger(settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_dragon_tiger(settings)
    return []


def _fetch_news(source: str, code: str, settings: Any) -> list[dict[str, Any]]:
    if source == "eastmoney":
        return _fetch_eastmoney_news(code, settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_news(code, settings)
    return []


# ── 东方财富 HTTP 抓取函数 ──────────────────────────────────────────────

def _fetch_eastmoney_limit_up_board(settings: Any) -> list[dict[str, Any]]:
    """东方财富涨停板数据。API不可用时安全返回空列表。"""
    try:
        url = (
            "https://push2ex.eastmoney.com/getTopicZTPool"
            "?ut=7eea3edcaed734bea9cbfc09c98d1b28"
            "&Market=1&isnew=0&type=zt&pagesize=200&page=1"
        )
        data = _http_get_json(url, settings)
        pool = data.get("data")
        if not pool:
            return []
        rows = pool.get("pool") or []
        trade_date = date.today().isoformat()
        result = []
        for row in rows:
            code = _normalize_code(str(row.get("c", "")))
            if not code or len(code) != 6:
                continue
            result.append({
                "code": code,
                "name": str(row.get("n", "")),
                "trade_date": trade_date,
                "consecutive_days": int(row.get("lbc") or 1),
                "limit_up_time": str(row.get("zttm", "")),
                "first_limit_up_time": str(row.get("fbt", "")),
                "is_sealed": int(row.get("zbc") or 0) > 0,
                "seal_amount_yi": float(row.get("fba") or 0) / 1e8,
                "limit_up_reason": str(row.get("hybk", "")) or str(row.get("reason", "")),
                "concept_sector": str(row.get("hybk", "")),
                "turnover_amount_yi": float(row.get("amount") or 0) / 1e8,
                "turnover_rate": float(row.get("turnover") or 0),
                "seal_count": int(row.get("fbt_count") or 0),
                "open_count": int(row.get("zbc") or 0),
            })
        return result
    except Exception as e:
        logger.warning("东方财富涨停板API不可用: %s", e)
        return []


def _fetch_eastmoney_concept_sectors(settings: Any) -> list[dict[str, Any]]:
    """东方财富概念板块数据。API不可用时安全返回空列表。"""
    try:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            "?pn=1&pz=200&po=1&np=1&fltt=2&invt=2"
            "&fid=f3&fs=m:90+t:3"
            "&fields=f2,f3,f4,f12,f14,f20,f102,f104,f128,f136,f140"
        )
        data = _http_get_json(url, settings)
        rows = data.get("data", {}).get("diff") or []
        trade_date = date.today().isoformat()
        result = []
        for rank, row in enumerate(rows, start=1):
            result.append({
                "sector_code": str(row.get("f12", "")),
                "sector_name": str(row.get("f14", "")),
                "trade_date": trade_date,
                "pct_chg": float(row.get("f3") or 0),
                "leading_code": str(row.get("f128", "")),
                "leading_name": str(row.get("f136", "")),
                "leading_pct": float(row.get("f140") or 0),
                "total_amount_yi": float(row.get("f20") or 0) / 1e8,
                "stock_count": int(row.get("f102") or 0),
                "limit_up_count": int(row.get("f104") or 0),
                "heat_rank": rank,
            })
        return result
    except Exception as e:
        logger.warning("东方财富概念板块API不可用: %s", e)
        return []


def _fetch_eastmoney_concept_sector_stocks(sector_code: str, settings: Any) -> list[dict[str, Any]]:
    """东方财富概念板块成分股。API不可用时安全返回空列表。"""
    try:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn=1&pz=300&po=1&np=1&fltt=2&invt=2"
            f"&fid=f2&fs=b:{sector_code}"
            "&fields=f2,f3,f12,f14,f20"
        )
        data = _http_get_json(url, settings)
        rows = data.get("data", {}).get("diff") or []
        trade_date = date.today().isoformat()
        result = []
        for row in rows:
            code = _normalize_code(str(row.get("f12", "")))
            if not code or len(code) != 6:
                continue
            result.append({
                "sector_code": sector_code,
                "code": code,
                "name": str(row.get("f14", "")),
                "trade_date": trade_date,
                "pct_chg": float(row.get("f3") or 0),
                "market_cap_yi": float(row.get("f20") or 0) / 1e8,
                "is_leading": False,
            })
        return result
    except Exception as e:
        logger.warning("东方财富概念成分股API不可用: %s", e)
        return []


def _fetch_eastmoney_capital_flow(code: str, settings: Any, days: int = 10) -> dict[str, Any] | None:
    """东方财富个股资金流向量价。API不可用时安全返回None。"""
    try:
        market_id = _eastmoney_market_id({"code": code})
        secid = f"{market_id}.{code}"
        url = (
            "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f7"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62"
            f"&lmt={days}&klt=101"
        )
        data = _http_get_json(url, settings)
        lines = data.get("data", {}).get("klines") or []
        if not lines:
            return None

        latest_parts = lines[0].split(",")
        trade_date = latest_parts[0] if latest_parts[0] else date.today().isoformat()

        cum_3d = 0.0
        cum_5d = 0.0
        cum_10d = 0.0
        for i, line in enumerate(lines):
            parts = line.split(",")
            main_inflow = float(parts[1] or 0) / 1e8
            if i < 3:
                cum_3d += main_inflow
            if i < 5:
                cum_5d += main_inflow
            cum_10d += main_inflow

        total_amount = float(latest_parts[2] or 0)
        main_ratio = (float(latest_parts[1] or 0) / total_amount * 100) if total_amount else 0.0

        return {
            "code": code,
            "trade_date": trade_date,
            "main_net_inflow_yi": round(float(latest_parts[1] or 0) / 1e8, 4),
            "super_large_net_inflow_yi": round(float(latest_parts[3] or 0) / 1e8, 4),
            "large_net_inflow_yi": round(float(latest_parts[4] or 0) / 1e8, 4),
            "medium_net_inflow_yi": round(float(latest_parts[5] or 0) / 1e8, 4),
            "small_net_inflow_yi": round(float(latest_parts[6] or 0) / 1e8, 4),
            "main_net_inflow_ratio": round(main_ratio, 2),
            "cumulative_inflow_3d_yi": round(cum_3d, 4),
            "cumulative_inflow_5d_yi": round(cum_5d, 4),
            "cumulative_inflow_10d_yi": round(cum_10d, 4),
        }
    except Exception as e:
        logger.debug("东方财富资金流向API不可用 (%s): %s", code, e)
        return None


def _fetch_eastmoney_dragon_tiger(settings: Any) -> list[dict[str, Any]]:
    """东方财富龙虎榜数据。API不可用时安全返回空列表。"""
    try:
        url = (
            "https://datacenter-web.eastmoney.com/api/data/v1/get"
            "?reportName=RPT_DAILY_DRAGON_TIGER"
            "&columns=ALL&sortColumns=NET_BUY_AMT&sortTypes=-1"
            "&pageSize=300&pageNumber=1"
            "&quoteType=0&source=WEB"
        )
        data = _http_get_json(url, settings)
        rows = data.get("result", {}).get("data") or []
        trade_date = date.today().isoformat()
        result = []
        for row in rows:
            code = _normalize_code(str(row.get("SECURITY_CODE", "")))
            if not code or len(code) != 6:
                continue
            org_buy = float(row.get("ORG_BUY_AMT") or 0) / 1e8
            org_sell = float(row.get("ORG_SELL_AMT") or 0) / 1e8
            result.append({
                "code": code,
                "name": str(row.get("SECURITY_NAME_ABBR", "")),
                "trade_date": trade_date,
                "reason": str(row.get("CHANGE_REASON", "") or row.get("NEW_CHANGE_REASON", "")),
                "buy_amount_yi": round(float(row.get("BUY_AMT") or 0) / 1e8, 4),
                "sell_amount_yi": round(float(row.get("SELL_AMT") or 0) / 1e8, 4),
                "net_amount_yi": round(float(row.get("NET_BUY_AMT") or 0) / 1e8, 4),
                "buy_seat_count": int(row.get("TOTAL_BUYER_COUNT") or 0),
                "sell_seat_count": int(row.get("TOTAL_SELLER_COUNT") or 0),
                "institution_net_buy_yi": round(org_buy - org_sell, 4),
                "top_buy_broker": str(row.get("BUYER_TRADER_NAME") or ""),
                "top_sell_broker": str(row.get("SELLER_TRADER_NAME") or ""),
            })
        return result
    except Exception as e:
        logger.warning("东方财富龙虎榜API不可用: %s", e)
        return []


def _fetch_eastmoney_news(code: str, settings: Any) -> list[dict[str, Any]]:
    """东方财富个股公告+新闻。API不可用时安全返回空列表。"""
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


# ── THS SDK 抓取函数 ───────────────────────────────────────────────────

def _fetch_thsdk_limit_up_board(settings: Any) -> list[dict[str, Any]]:
    """通过 THS SDK 获取涨停板数据。SDK不可用时安全返回空列表。"""
    try:
        with _thsdk_client(settings) as ths:
            # 先获取全市场列表
            list_resp = ths.stock_cn_lists()
            if not list_resp:
                return []
            # 尝试用涨停板query_key获取行情
            from app.services.market_data import _ths_code_from_row, _normalize_code, _ths_market_prefix
            from collections import defaultdict
            listed = _thsdk_rows(getattr(list_resp, "data", None))
            ths_codes = []
            name_by_code: dict[str, str] = {}
            for row in listed:
                ths_code = _ths_code_from_row(row)
                code = _normalize_code(ths_code)
                if not ths_code or _ths_market_prefix(ths_code) not in {"USHA", "USZA", "USTM"} or not code:
                    continue
                ths_codes.append(ths_code)
                name = str(row.get("名称", "") or row.get("name", ""))
                if name:
                    name_by_code[code] = name
                if len(ths_codes) >= 5200:
                    break

            by_market: dict[str, list[str]] = defaultdict(list)
            for tc in ths_codes:
                by_market[_ths_market_prefix(tc)].append(tc)

            trade_date = date.today().isoformat()
            result: list[dict[str, Any]] = []
            from app.services.market_data import _batched
            for market, codes in by_market.items():
                for batch in _batched(codes, 600):
                    try:
                        resp = ths.market_data_cn(batch, query_key="汇总")
                        if not resp:
                            continue
                        for row in _thsdk_rows(getattr(resp, "data", None)):
                            ths_code = _ths_code_from_row(row, default_market=market)
                            code = _normalize_code(ths_code)
                            if not code:
                                continue
                            pct = float(row.get("涨跌幅", 0) or row.get("pct_chg", 0) or 0)
                            if pct < 9.8:
                                continue
                            result.append({
                                "code": code,
                                "name": name_by_code.get(code, str(row.get("名称", "") or row.get("name", ""))),
                                "trade_date": trade_date,
                                "consecutive_days": 1,
                                "limit_up_time": "",
                                "is_sealed": True,
                                "seal_amount_yi": None,
                                "limit_up_reason": "",
                                "concept_sector": "",
                                "turnover_amount_yi": float(row.get("成交额", 0) or row.get("amount", 0) or 0) / 1e8,
                                "turnover_rate": float(row.get("换手率", 0) or row.get("turnover_rate", 0) or 0),
                                "seal_count": 0,
                                "open_count": 0,
                            })
                    except Exception:
                        continue
            return result
    except Exception as e:
        logger.warning("THS SDK涨停板数据不可用: %s", e)
        return []


def _fetch_thsdk_concept_sectors(settings: Any) -> list[dict[str, Any]]:
    """通过 THS SDK 获取概念板块数据。SDK不可用时安全返回空列表。"""
    try:
        with _thsdk_client(settings) as ths:
            resp = ths.market_data_cn([], query_key="概念板块")
            if not resp:
                return []
            rows = _thsdk_rows(getattr(resp, "data", None))
            if not rows:
                return []
            trade_date = date.today().isoformat()
            result = []
            for rank, row in enumerate(rows, start=1):
                result.append({
                    "sector_code": str(row.get("板块代码", "") or row.get("sector_code", "")),
                    "sector_name": str(row.get("板块名称", "") or row.get("sector_name", "")),
                    "trade_date": trade_date,
                    "pct_chg": _num(row.get("涨跌幅", 0) or row.get("pct_chg", 0)),
                    "leading_code": str(row.get("领涨股代码", "") or ""),
                    "leading_name": str(row.get("领涨股", "") or ""),
                    "leading_pct": _num(row.get("领涨股涨幅", 0)),
                    "total_amount_yi": _num(row.get("成交额", 0)) / 1e8,
                    "stock_count": int(row.get("成分股数量", 0) or 0),
                    "limit_up_count": int(row.get("涨停数", 0) or 0),
                    "heat_rank": rank,
                })
            return result
    except Exception as e:
        logger.warning("THS SDK概念板块数据不可用: %s", e)
        return []


def _fetch_thsdk_capital_flow(code: str, settings: Any) -> dict[str, Any] | None:
    """通过 THS SDK 获取个股资金流向。SDK不可用时返回None。"""
    try:
        from app.services.market_data import _ths_code_from_quote_or_code
        ths_code = _ths_code_from_quote_or_code(code)
        if not ths_code:
            return None
        with _thsdk_client(settings) as ths:
            resp = ths.market_data_cn([ths_code], query_key="汇总")
            if not resp:
                return None
            rows = _thsdk_rows(getattr(resp, "data", None))
            if not rows:
                return None
            row = rows[0]
            trade_date = date.today().isoformat()
            return {
                "code": code,
                "trade_date": trade_date,
                "main_net_inflow_yi": round(_num(row.get("主力净流入", 0)) / 1e8, 4),
                "super_large_net_inflow_yi": round(_num(row.get("超大单净流入", 0)) / 1e8, 4),
                "large_net_inflow_yi": round(_num(row.get("大单净流入", 0)) / 1e8, 4),
                "medium_net_inflow_yi": round(_num(row.get("中单净流入", 0)) / 1e8, 4),
                "small_net_inflow_yi": round(_num(row.get("小单净流入", 0)) / 1e8, 4),
                "main_net_inflow_ratio": _num(row.get("主力净流入占比", 0)),
                "cumulative_inflow_3d_yi": None,
                "cumulative_inflow_5d_yi": None,
                "cumulative_inflow_10d_yi": None,
            }
    except Exception as e:
        logger.debug("THS SDK资金流向不可用 (%s): %s", code, e)
        return None


def _fetch_thsdk_dragon_tiger(settings: Any) -> list[dict[str, Any]]:
    """通过 THS SDK 获取龙虎榜数据。SDK不可用时安全返回空列表。"""
    try:
        with _thsdk_client(settings) as ths:
            resp = ths.market_data_cn([], query_key="龙虎榜")
            if not resp:
                return []
            rows = _thsdk_rows(getattr(resp, "data", None))
            if not rows:
                return []
            trade_date = date.today().isoformat()
            result = []
            for row in rows:
                code = _normalize_code(str(row.get("证券代码", "") or row.get("code", "")))
                if not code or len(code) != 6:
                    continue
                result.append({
                    "code": code,
                    "name": str(row.get("证券名称", "") or row.get("name", "")),
                    "trade_date": trade_date,
                    "reason": str(row.get("上榜原因", "") or row.get("reason", "")),
                    "buy_amount_yi": round(_num(row.get("买入金额", 0)) / 1e8, 4),
                    "sell_amount_yi": round(_num(row.get("卖出金额", 0)) / 1e8, 4),
                    "net_amount_yi": round(_num(row.get("净买入", 0)) / 1e8, 4),
                    "buy_seat_count": int(row.get("买入席位数", 0) or 0),
                    "sell_seat_count": int(row.get("卖出席位数", 0) or 0),
                    "institution_net_buy_yi": round(_num(row.get("机构净买入", 0)) / 1e8, 4),
                    "top_buy_broker": str(row.get("买入席位Top1", "") or ""),
                    "top_sell_broker": str(row.get("卖出席位Top1", "") or ""),
                })
            return result
    except Exception as e:
        logger.warning("THS SDK龙虎榜数据不可用: %s", e)
        return []


def _fetch_thsdk_news(code: str, settings: Any) -> list[dict[str, Any]]:
    """通过 THS SDK 获取个股公告。SDK不可用时安全返回空列表。"""
    try:
        from app.services.market_data import _ths_code_from_quote_or_code
        ths_code = _ths_code_from_quote_or_code(code)
        if not ths_code:
            return []
        with _thsdk_client(settings) as ths:
            resp = ths.market_data_cn([ths_code], query_key="公告")
            if not resp:
                return []
            rows = _thsdk_rows(getattr(resp, "data", None))
            if not rows:
                return []
            items = []
            for row in rows:
                title = str(row.get("公告标题", "") or row.get("title", ""))
                is_risk = any(
                    kw in title
                    for kw in ["风险", "警示", "退市", "ST", "处罚", "监管", "立案", "亏损", "减持", "问询"]
                )
                items.append({
                    "article_id": str(row.get("公告ID", "") or row.get("id", "")),
                    "code": code,
                    "announce_date": str(row.get("公告日期", "") or row.get("date", ""))[:10],
                    "title": title,
                    "summary": str(row.get("摘要", "") or row.get("summary", "")),
                    "category": "announcement",
                    "sentiment": "negative" if is_risk else "neutral",
                    "is_risk_warning": is_risk,
                })
            return items
    except Exception as e:
        logger.debug("THS SDK新闻公告不可用 (%s): %s", code, e)
        return []

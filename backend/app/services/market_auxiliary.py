from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
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

# ── akshare 限流控制 ──
_AK_LAST_CALL = 0.0
_AK_MIN_INTERVAL = 3.0  # 秒


def _ak_throttle():
    """确保 akshare 调用间隔不小于 _AK_MIN_INTERVAL，避免被封。"""
    global _AK_LAST_CALL
    elapsed = time.monotonic() - _AK_LAST_CALL
    if elapsed < _AK_MIN_INTERVAL:
        time.sleep(_AK_MIN_INTERVAL - elapsed)
    _AK_LAST_CALL = time.monotonic()


def _ak_retry(fn, name: str, max_retries: int = 3):
    """带重试的 akshare 调用，间隔指数退避。"""
    import time as _time
    last_error = None
    for attempt in range(max_retries):
        try:
            _ak_throttle()
            return fn()
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = _AK_MIN_INTERVAL * (2 ** attempt)
                logger.debug("akshare %s 第%d次失败，%0.1f秒后重试: %s", name, attempt + 1, delay, str(e)[:80])
                _time.sleep(delay)
    raise last_error


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
    if source == "akshare":
        return _fetch_ak_limit_up_board(settings)
    if source == "eastmoney":
        return _fetch_eastmoney_limit_up_board(settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_limit_up_board(settings)
    return []


def _fetch_concept_sectors(source: str, settings: Any) -> list[dict[str, Any]]:
    if source == "akshare":
        return _fetch_ak_concept_sectors(settings)
    if source == "eastmoney":
        return _fetch_eastmoney_concept_sectors(settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_concept_sectors(settings)
    return []


def _fetch_capital_flow(source: str, code: str, settings: Any) -> dict[str, Any] | None:
    if source == "akshare":
        return _fetch_ak_capital_flow(code, settings)
    if source == "eastmoney":
        return _fetch_eastmoney_capital_flow(code, settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_capital_flow(code, settings)
    return None


def _fetch_dragon_tiger(source: str, settings: Any) -> list[dict[str, Any]]:
    if source == "akshare":
        return _fetch_ak_dragon_tiger(settings)
    if source == "eastmoney":
        return _fetch_eastmoney_dragon_tiger(settings)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_dragon_tiger(settings)
    return []


def _fetch_news(source: str, code: str, settings: Any) -> list[dict[str, Any]]:
    if source == "eastmoney":
        return _fetch_eastmoney_news(code, settings)
    if source == "akshare":
        return _fetch_ak_news(code, settings)
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
        logger.info("东方财富涨停板API不可用: %s", e)
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
        logger.info("东方财富概念板块API不可用: %s", e)
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
        logger.info("东方财富概念成分股API不可用: %s", e)
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
        logger.info("东方财富龙虎榜API不可用: %s", e)
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


# ── akshare 抓取函数 ─────────────────────────────────────────────────────


def _fetch_ak_concept_sectors(settings: Any) -> list[dict[str, Any]]:
    """通过 akshare (同花顺源) 获取概念板块列表。"""
    try:
        import akshare as ak

        df = _ak_retry(lambda: ak.stock_board_concept_name_ths(), "概念板块列表")
        if df is None or df.empty:
            return []
        trade_date = date.today().isoformat()
        result = []
        for rank, (_, row) in enumerate(df.iterrows(), start=1):
            result.append({
                "sector_code": str(row.get("code", "")),
                "sector_name": str(row.get("name", "")),
                "trade_date": trade_date,
                "pct_chg": 0.0,  # 概念板块列表不含涨跌幅
                "leading_code": "",
                "leading_name": "",
                "leading_pct": 0.0,
                "total_amount_yi": 0.0,
                "stock_count": 0,
                "limit_up_count": 0,
                "heat_rank": rank,
            })
        return result
    except Exception as e:
        logger.warning("akshare概念板块不可用: %s", e)
        return []


def _fetch_ak_concept_sector_stocks(sector_name: str, settings: Any) -> list[dict[str, Any]]:
    """通过 akshare (同花顺源) 获取概念板块成分股。
    DEPRECATED: akshare 1.18.60 起 stock_board_concept_info_ths 改为返回板块摘要（项目/值），
    不再返回成分股列表。THS 网站成分股通过 JS 动态加载，无可用 JSON API。
    Eastmoney push2 API 在本机 IP 被永久封禁。
    此函数此后一律返回空列表。
    """
    return []


def _fetch_ak_capital_flow(code: str, settings: Any) -> dict[str, Any] | None:
    """通过 akshare 获取个股资金流向。
    DEPRECATED: 东方财富源在本机已永久被封，此函数此后一律返回 None。
    """
    return None


def _fetch_ak_capital_flow_deprecated(code: str, settings: Any) -> dict[str, Any] | None:
    """[已废弃] 通过 akshare (东方财富源) 获取个股资金流向。"""
    try:
        import akshare as ak

        market = "sh" if code.startswith(("6", "9")) else "sz"
        df = _ak_retry(lambda: ak.stock_individual_fund_flow(stock=code, market=market), f"资金流向({code})")
        if df is None or df.empty:
            return None

        latest = df.iloc[0]
        trade_date = str(latest.get("日期", date.today()))
        if hasattr(trade_date, "strftime"):
            trade_date = trade_date.strftime("%Y-%m-%d")

        cum_3d = 0.0
        cum_5d = 0.0
        cum_10d = 0.0
        for i in range(min(len(df), 10)):
            row = df.iloc[i]
            net = float(row.get("主力净流入-净额", 0) or 0)
            if i < 3:
                cum_3d += net
            if i < 5:
                cum_5d += net
            cum_10d += net

        return {
            "code": code,
            "trade_date": trade_date,
            "main_net_inflow_yi": round(float(latest.get("主力净流入-净额", 0) or 0) / 1e8, 4),
            "super_large_net_inflow_yi": round(float(latest.get("超大单净流入-净额", 0) or 0) / 1e8, 4),
            "large_net_inflow_yi": round(float(latest.get("大单净流入-净额", 0) or 0) / 1e8, 4),
            "medium_net_inflow_yi": round(float(latest.get("中单净流入-净额", 0) or 0) / 1e8, 4),
            "small_net_inflow_yi": round(float(latest.get("小单净流入-净额", 0) or 0) / 1e8, 4),
            "main_net_inflow_ratio": round(float(latest.get("主力净流入-净占比", 0) or 0), 2),
            "cumulative_inflow_3d_yi": round(cum_3d / 1e8, 4),
            "cumulative_inflow_5d_yi": round(cum_5d / 1e8, 4),
            "cumulative_inflow_10d_yi": round(cum_10d / 1e8, 4),
        }
    except Exception as e:
        logger.debug("akshare资金流向不可用 (%s): %s", code, e)
        return None


def _fetch_ak_dragon_tiger(settings: Any) -> list[dict[str, Any]]:
    """通过 akshare 获取龙虎榜数据。"""
    try:
        import akshare as ak

        today = date.today()
        start = (today - timedelta(days=3)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        df = _ak_retry(lambda: ak.stock_lhb_detail_em(start_date=start, end_date=end), "龙虎榜")
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code or len(code) != 6:
                continue
            trade_d = row.get("上榜日")
            if hasattr(trade_d, "strftime"):
                trade_d = trade_d.strftime("%Y-%m-%d")
            else:
                trade_d = str(trade_d)[:10]
            result.append({
                "code": code,
                "name": str(row.get("名称", "")),
                "trade_date": trade_d,
                "reason": str(row.get("上榜原因", "")),
                "buy_amount_yi": round(float(row.get("龙虎榜买入额", 0) or 0) / 1e8, 4),
                "sell_amount_yi": round(float(row.get("龙虎榜卖出额", 0) or 0) / 1e8, 4),
                "net_amount_yi": round(float(row.get("龙虎榜净买额", 0) or 0) / 1e8, 4),
                "buy_seat_count": 0,
                "sell_seat_count": 0,
                "institution_net_buy_yi": 0.0,
                "top_buy_broker": "",
                "top_sell_broker": "",
            })
        return result
    except Exception as e:
        logger.warning("akshare龙虎榜不可用: %s", e)
        return []


def _fetch_ak_limit_up_board(settings: Any) -> list[dict[str, Any]]:
    """通过 akshare 获取涨停板数据。"""
    try:
        import akshare as ak

        today = date.today()
        df = _ak_retry(lambda: ak.stock_zt_pool_em(date=today.strftime("%Y%m%d")), "涨停板")
        if df is None or df.empty:
            return []
        trade_date = today.isoformat()
        result = []
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code or len(code) != 6:
                continue
            result.append({
                "code": code,
                "name": str(row.get("名称", "")),
                "trade_date": trade_date,
                "consecutive_days": int(row.get("连板数", 0) or 1),
                "limit_up_time": str(row.get("封板时间", "")),
                "first_limit_up_time": str(row.get("首次封板时间", "")),
                "is_sealed": str(row.get("封板状态", "")) == "封板",
                "seal_amount_yi": float(row.get("封单金额", 0) or 0) / 1e8,
                "limit_up_reason": str(row.get("涨停原因", "")),
                "concept_sector": str(row.get("所属行业", "")),
                "turnover_amount_yi": float(row.get("成交额", 0) or 0) / 1e8,
                "turnover_rate": float(row.get("换手率", 0) or 0),
                "seal_count": 0,
                "open_count": int(row.get("炸板次数", 0) or 0),
            })
        return result
    except Exception as e:
        logger.warning("akshare涨停板不可用: %s", e)
        return []


def _fetch_ak_news(code: str, settings: Any) -> list[dict[str, Any]]:
    """通过 akshare 获取个股新闻。"""
    try:
        import akshare as ak

        df = _ak_retry(lambda: ak.stock_news_em(symbol=code), f"新闻({code})")
        if df is None or df.empty:
            return []
        items: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            title = str(row.get("新闻标题", "") or row.get("title", "") or "")
            summary = str(row.get("新闻内容", "") or row.get("summary", "") or "")
            pub_time = str(row.get("发布时间", "") or row.get("datetime", "") or "")
            news_link = str(row.get("新闻链接", "") or row.get("url", "") or "")
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
            article_id = news_link or (pub_time[:16] + "_" + title[:20] if pub_time else title[:30])
            items.append({
                "source": "akshare",
                "article_id": article_id,
                "code": code,
                "announce_date": pub_time[:10] if len(pub_time) >= 10 else date.today().isoformat(),
                "title": title,
                "summary": summary[:500] if summary else "",
                "category": "news",
                "sentiment": sentiment,
                "is_risk_warning": is_negative,
            })
        return items
    except Exception as e:
        logger.debug("akshare新闻不可用 (%s): %s", code, e)
        return []

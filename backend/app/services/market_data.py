from __future__ import annotations

import math
import statistics
import threading
import time
from collections import Counter, defaultdict
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.core.config import get_settings


class MarketDataError(RuntimeError):
    pass


HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_TUSHARE_CALL_TIMES: deque[float] = deque()
_TUSHARE_RATE_LOCK = threading.Lock()


def get_market_universe() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    quote_source, quotes, quote_errors = _fetch_first_candidate_quotes(_quote_sources(settings), settings)
    total_quote_count = len(quotes)
    raw_target_count = int(getattr(settings, "market_candidate_count", 300))
    full_market_mode = raw_target_count <= 0
    if full_market_mode:
        target_count = total_quote_count
        lookahead_count = total_quote_count
    else:
        target_count = max(1, raw_target_count)
        lookahead_count = min(
            total_quote_count,
            max(target_count, target_count * max(1, int(getattr(settings, "market_candidate_lookahead_multiplier", 4)))),
        )
    all_candidates = _select_candidates(quotes, total_quote_count)
    filter_summary = _candidate_filter_summary(quotes)
    candidates = all_candidates[:lookahead_count]
    if not candidates:
        raise MarketDataError("真实行情源返回了空股票池，已停止运行。")

    kline_sources = _kline_sources(settings)
    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()
    disabled_kline_sources: set[str] = set()
    for quote in candidates:
        if len(stocks) >= target_count:
            break
        bars, source, errors = _fetch_first_kline(quote, kline_sources, settings, disabled_kline_sources)
        disabled_kline_sources.update(_global_failed_kline_sources(errors))
        if not bars:
            kline_errors[quote["code"]] = errors
            continue
        stock = _build_stock_from_bars(quote, bars, quote_source=quote_source, kline_source=source)
        stocks.append(stock)
        kline_usage[source] += 1

    if not stocks:
        detail = "; ".join(f"{code}: {' | '.join(errors[:2])}" for code, errors in list(kline_errors.items())[:8])
        raise MarketDataError(f"所有候选股票都无法取得真实日K，已停止运行。{detail}")

    _apply_relative_features(stocks)
    snapshot = _build_snapshot(
        stocks=stocks,
        quote_source=quote_source,
        quote_errors=quote_errors,
        kline_sources=kline_sources,
        kline_usage=kline_usage,
        kline_errors=kline_errors,
        disabled_kline_sources=disabled_kline_sources,
    )
    snapshot.setdefault("breadth", {}).update(
        {
            "full_market_mode": full_market_mode,
            "unified_quote_total": total_quote_count,
            "base_filter_eligible_count": len(all_candidates),
            "candidate_filter": filter_summary,
            "actual_scan_count": len(stocks),
            "kline_missing_count": len(kline_errors),
            "kline_skip_reasons": _summarize_kline_skip_reasons(kline_errors),
            "candidate_limit_config": {
                "market_candidate_count": raw_target_count,
                "lookahead_multiplier": int(getattr(settings, "market_candidate_lookahead_multiplier", 4)),
                "lookahead_count": lookahead_count,
                "target_scan_count": target_count,
            },
        }
    )
    return stocks, snapshot


def get_market_snapshot() -> dict[str, Any]:
    settings = get_settings()
    quote_source, quotes, quote_errors = _fetch_first_candidate_quotes(_quote_sources(settings), settings)
    filter_summary = _candidate_filter_summary(quotes)
    quotes = _select_candidates(quotes, min(settings.market_scan_size, 300))
    if not quotes:
        raise MarketDataError("真实行情源返回了空市场快照。")
    trade_date = _latest_trade_date_from_quotes(quotes)
    up_count = sum(1 for item in quotes if item.get("pct", 0) > 0)
    down_count = sum(1 for item in quotes if item.get("pct", 0) < 0)
    industries = _top_industries_from_quotes(quotes)
    breadth = _breadth_from_counts(
        sample_count=len(quotes),
        up_count=up_count,
        down_count=down_count,
        limit_up_count=sum(1 for item in quotes if item.get("pct", 0) >= 9.8),
        limit_down_count=sum(1 for item in quotes if item.get("pct", 0) <= -9.8),
    )
    breadth.update(
        {
            "data_source": "real_market",
            "quote_source": quote_source,
            "quote_errors": quote_errors,
            "generated_at": datetime.utcnow().isoformat(),
            "candidate_filter": filter_summary,
            "hot_theme": " / ".join(industries[:2]) if industries else "未知",
        }
    )
    return {
        "snapshot_id": f"snapshot_{quote_source}_{trade_date}",
        "trade_date": trade_date,
        "sample_count": len(quotes),
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": sum(1 for item in quotes if item.get("pct", 0) >= 9.8),
        "limit_down_count": sum(1 for item in quotes if item.get("pct", 0) <= -9.8),
        "main_industries": industries,
        "breadth": breadth,
    }


def _fetch_first_quotes(sources: list[str], settings: Any) -> tuple[str, list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    for source in sources:
        try:
            quotes = _fetch_quotes(source, settings)
            if quotes:
                return source, quotes, errors
            errors.append(f"{source}: empty result")
        except Exception as exc:  # noqa: BLE001 - returned to the local operator as run evidence
            errors.append(f"{source}: {exc}")
    raise MarketDataError("真实行情快照获取失败，已停止运行。" + ("；".join(errors) if errors else ""))


def _fetch_first_candidate_quotes(sources: list[str], settings: Any) -> tuple[str, list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    for source in sources:
        try:
            quotes = _fetch_quotes(source, settings)
            requested_count = int(getattr(settings, "market_candidate_count", 300))
            candidate_count = len(_select_candidates(quotes, len(quotes) if requested_count <= 0 else max(1, requested_count)))
            if candidate_count:
                return source, quotes, errors
            errors.append(f"{source}: {len(quotes)} quotes but 0 candidates after filters")
        except Exception as exc:  # noqa: BLE001 - returned to the local operator as run evidence
            errors.append(f"{source}: {exc}")
    raise MarketDataError("真实行情候选池获取失败，已停止运行。" + ("；".join(errors) if errors else ""))


def _fetch_first_kline(
    quote: dict[str, Any],
    sources: list[str],
    settings: Any,
    disabled_sources: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    errors: list[str] = []
    disabled_sources = disabled_sources if disabled_sources is not None else set()
    for source in sources:
        if source in disabled_sources:
            errors.append(f"{source}: 本次扫描已熔断，跳过")
            continue
        try:
            bars = _fetch_kline(source, quote, settings)
            if len(bars) >= 30:
                return bars, source, errors
            errors.append(f"{source}: 日K不足30条")
        except Exception as exc:  # noqa: BLE001 - returned to the local operator as run evidence
            message = f"{source}: {exc}"
            errors.append(message)
            if _is_global_kline_source_error(source, str(exc)):
                disabled_sources.add(source)
    return [], "", errors


def _fetch_quotes(source: str, settings: Any) -> list[dict[str, Any]]:
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_quotes(settings)
    if source == "sina":
        return _fetch_sina_quotes(settings)
    if source == "tencent":
        return _fetch_tencent_quotes(settings)
    if source == "eastmoney":
        return _fetch_eastmoney_quotes(settings)
    if source == "akshare":
        return _fetch_akshare_quotes(settings)
    if source == "tushare":
        return _fetch_tushare_quotes(settings)
    raise MarketDataError(f"未知行情源: {source}")


def _fetch_kline(
    source: str,
    quote: dict[str, Any],
    settings: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    if source == "tickflow":
        return _fetch_tickflow_kline(quote, settings, start_date=start_date, end_date=end_date)
    if source in {"thsdk", "thssdk"}:
        return _fetch_thsdk_kline(quote, settings)
    if source == "baostock":
        return _fetch_baostock_kline(quote, settings, start_date=start_date, end_date=end_date)
    if source == "eastmoney":
        return _fetch_eastmoney_kline(quote, settings, start_date=start_date, end_date=end_date)
    if source == "tencent":
        return _fetch_tencent_kline(quote, settings)
    if source == "akshare":
        return _fetch_akshare_kline(quote, settings, start_date=start_date, end_date=end_date)
    if source == "tushare":
        return _fetch_tushare_kline(quote, settings, start_date=start_date, end_date=end_date)
    raise MarketDataError(f"未知日K源: {source}")


def _fetch_eastmoney_quotes(settings: Any) -> list[dict[str, Any]]:
    fields = "f2,f3,f5,f6,f8,f9,f12,f14,f20,f21,f24,f25,f62,f100"
    rows: list[dict[str, Any]] = []
    page_size = min(100, max(50, int(getattr(settings, "market_scan_size", 100))))
    max_pages = max(1, math.ceil(max(1, int(getattr(settings, "market_scan_size", page_size))) / page_size))
    for page in range(1, max_pages + 1):
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get"
            f"?pn={page}&pz={page_size}&po=1&np=1&fltt=2&invt=2"
            "&fid=f6&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
            f"&fields={fields}"
        )
        data = _http_get_json(url, settings)
        page_rows = data.get("data", {}).get("diff", []) or []
        if not page_rows:
            break
        rows.extend(page_rows)
        total = int(_num(data.get("data", {}).get("total"), 0))
        if total and len(rows) >= min(total, int(getattr(settings, "market_scan_size", total))):
            break
    rows = rows[: int(getattr(settings, "market_scan_size", len(rows)))]
    return [
        _quote(
            code=str(row.get("f12") or ""),
            name=str(row.get("f14") or ""),
            price=_num(row.get("f2")),
            pct=_num(row.get("f3")),
            volume=_num(row.get("f5")),
            amount_yi=_num(row.get("f6")) / 100000000,
            turnover_rate=_num(row.get("f8")),
            pe_ttm=_num(row.get("f9")),
            total_mv_yi=_num(row.get("f20")) / 100000000,
            free_mv_yi=_num(row.get("f21")) / 100000000,
            chg60=_num(row.get("f24")),
            ytd_chg=_num(row.get("f25")),
            main_net_inflow_yi=_num(row.get("f62")) / 100000000,
            industry=str(row.get("f100") or "未分类"),
            source="eastmoney",
        )
        for row in rows
    ]


def _fetch_akshare_quotes(settings: Any) -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise MarketDataError("未安装 akshare，请先安装后端依赖。") from exc
    df = ak.stock_zh_a_spot_em()
    rows = df.to_dict("records")
    return [
        _quote(
            code=str(row.get("代码") or ""),
            name=str(row.get("名称") or ""),
            price=_num(row.get("最新价")),
            pct=_num(row.get("涨跌幅")),
            volume=_num(row.get("成交量")),
            amount_yi=_num(row.get("成交额")) / 100000000,
            turnover_rate=_num(row.get("换手率")),
            pe_ttm=_num(row.get("市盈率-动态")),
            total_mv_yi=_num(row.get("总市值")) / 100000000,
            free_mv_yi=_num(row.get("流通市值")) / 100000000,
            chg60=_num(row.get("60日涨跌幅")),
            ytd_chg=_num(row.get("年初至今涨跌幅")),
            main_net_inflow_yi=0,
            industry=str(row.get("行业") or "未分类"),
            source="akshare",
        )
        for row in rows
    ][: settings.market_scan_size]


def _fetch_sina_quotes(settings: Any) -> list[dict[str, Any]]:
    codes = _default_quote_code_list(settings.market_scan_size)
    rows: list[dict[str, Any]] = []
    batch_size = 120
    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        if not batch:
            continue
        url = f"https://hq.sinajs.cn/list={','.join(batch)}"
        text = _http_get_text(url, settings, headers={"Referer": "https://finance.sina.com.cn"}, encoding="gbk")
        rows.extend(_parse_sina_quotes(text))
    return rows


def _fetch_tencent_quotes(settings: Any) -> list[dict[str, Any]]:
    codes = _default_quote_code_list(settings.market_scan_size)
    rows: list[dict[str, Any]] = []
    batch_size = 120
    for start in range(0, len(codes), batch_size):
        batch = codes[start : start + batch_size]
        if not batch:
            continue
        url = f"https://qt.gtimg.cn/q={','.join(batch)}"
        text = _http_get_text(url, settings, headers={"Referer": "https://gu.qq.com"}, encoding="gbk")
        rows.extend(_parse_tencent_quotes(text))
    return rows


def _fetch_tushare_quotes(settings: Any) -> list[dict[str, Any]]:
    raise MarketDataError("Tushare 免费 token 仅接入非复权日线，不作为报价源。")


def _fetch_thsdk_quotes(settings: Any) -> list[dict[str, Any]]:
    rows_by_code: dict[str, dict[str, Any]] = {}
    list_response = None
    batch_errors: list[str] = []
    batch_size = max(1, int(getattr(settings, "thsdk_batch_size", 600) or 600))
    scan_size = max(1, int(getattr(settings, "market_scan_size", 5200) or 5200))

    with _thsdk_client(settings) as ths:
        list_response = ths.stock_cn_lists()
        if not list_response:
            raise MarketDataError(f"thsdk stock_cn_lists failed: {getattr(list_response, 'error', '')}")

        listed_rows = _thsdk_rows(getattr(list_response, "data", None))
        ths_codes = []
        name_by_code: dict[str, str] = {}
        for row in listed_rows:
            ths_code = _ths_code_from_row(row)
            code = _normalize_code(ths_code)
            if not ths_code or _ths_market_prefix(ths_code) not in {"USHA", "USZA", "USTM"} or not code:
                continue
            ths_codes.append(ths_code)
            name_by_code[code] = _first_text(row, ("名称", "证券简称", "股票简称", "name", "Name"))
            if len(ths_codes) >= scan_size:
                break

        by_market: dict[str, list[str]] = defaultdict(list)
        for ths_code in ths_codes:
            by_market[_ths_market_prefix(ths_code)].append(ths_code)

        for market, market_codes in by_market.items():
            for batch in _batched(market_codes, batch_size):
                try:
                    time.sleep(0.03)
                    response = ths.market_data_cn(batch, query_key="汇总")
                    if not response:
                        batch_errors.append(f"{market}: {getattr(response, 'error', '') or 'empty response'}")
                        continue
                    for row in _thsdk_rows(getattr(response, "data", None)):
                        ths_code = _ths_code_from_row(row, default_market=market)
                        code = _normalize_code(ths_code)
                        if not code:
                            continue
                        merged = rows_by_code.setdefault(code, {"代码": ths_code, "名称": name_by_code.get(code, "")})
                        _merge_non_empty(merged, row)
                except Exception as exc:  # noqa: BLE001 - surfaced to caller if the source is unusable
                    batch_errors.append(f"{market}: {exc}")

    quotes = [_quote_from_thsdk_row(row) for row in rows_by_code.values()]
    if not quotes and batch_errors:
        raise MarketDataError("thsdk 行情查询失败：" + "；".join(batch_errors[:5]))
    return quotes[:scan_size]


def _fetch_tickflow_kline(
    quote: dict[str, Any],
    settings: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """使用 TickFlow 免费 API 获取历史日K线。"""
    exchange = str(quote.get("exchange", "")).upper()
    code = str(quote.get("code", ""))
    if exchange not in {"SH", "SZ", "BJ"}:
        raise MarketDataError(f"tickflow 暂不支持交易所: {exchange}")
    symbol = f"{code}.{exchange}"

    try:
        from tickflow import TickFlow
    except ImportError as exc:
        raise MarketDataError("未安装 tickflow，请先安装后端依赖。") from exc

    client = TickFlow.free()
    days = max(30, int(getattr(settings, "market_kline_days", 90)))

    start_ms: int | None = None
    if start_date:
        start_ms = int(datetime.strptime(start_date, "%Y%m%d").timestamp() * 1000)
    elif end_date:
        start_ms = int(datetime.strptime(end_date, "%Y%m%d").timestamp() * 1000) - days * 86400000

    count = min(10000, max(days * 4, 500)) if start_ms else days * 4

    df = client.klines.get(
        symbol,
        period="1d",
        count=count,
        start_time=start_ms,
        as_dataframe=True,
    )
    if df is None or df.empty:
        raise MarketDataError(f"tickflow 返回空日K: {symbol}")

    df = df.sort_values("trade_date")
    bars: list[dict[str, Any]] = []
    previous_close = 0.0
    for _, row in df.iterrows():
        close_val = float(row.get("close") or 0)
        pct_chg = ((close_val - previous_close) / previous_close * 100) if previous_close else 0.0
        bars.append(
            {
                "code": quote["code"],
                "trade_date": str(row.get("trade_date") or ""),
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": close_val,
                "volume": float(row.get("volume") or 0),
                "amount": float(row.get("amount") or 0),
                "pct_chg": pct_chg,
                "turnover_rate": 0.0,
            }
        )
        previous_close = close_val

    if bars:
        bars[-1]["turnover_rate"] = float(quote.get("turnover_rate") or 0)
    return bars[-days:]


def _fetch_eastmoney_kline(
    quote: dict[str, Any],
    settings: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    secid = f"{_eastmoney_market_id(quote['code'])}.{quote['code']}"
    beg = start_date or (date.today() - timedelta(days=settings.market_kline_days * 3)).strftime("%Y%m%d")
    end = end_date or "20500101"
    lmt = settings.market_kline_days if start_date is None else max(settings.market_kline_days, 500)
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&beg={beg}&end={end}&lmt={lmt}"
    )
    data = _http_get_json(url, settings)
    lines = data.get("data", {}).get("klines", []) or []
    return [_bar_from_eastmoney_line(quote["code"], line) for line in lines]


def _fetch_thsdk_kline(quote: dict[str, Any], settings: Any) -> list[dict[str, Any]]:
    ths_code = _ths_code_from_quote_or_code(quote)
    if not ths_code:
        raise MarketDataError(f"thsdk 无法识别股票代码: {quote.get('code')}")

    with _thsdk_client(settings) as ths:
        response = ths.klines(ths_code, count=settings.market_kline_days, adjust="forward")
    if not response:
        raise MarketDataError(f"thsdk klines failed: {getattr(response, 'error', '')}")

    bars: list[dict[str, Any]] = []
    previous_close = 0.0
    for row in _thsdk_rows(getattr(response, "data", None)):
        bar = _bar_from_thsdk_row(_normalize_code(ths_code), row, previous_close)
        bars.append(bar)
        previous_close = _num(bar.get("close"), previous_close)
    return bars[-settings.market_kline_days :]


def _fetch_tencent_kline(quote: dict[str, Any], settings: Any) -> list[dict[str, Any]]:
    symbol = f"{quote['exchange'].lower()}{quote['code']}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{settings.market_kline_days},qfq"
    data = _http_get_json(url, settings)
    item = data.get("data", {}).get(symbol, {})
    rows = item.get("qfqday") or item.get("day") or []
    bars: list[dict[str, Any]] = []
    previous_close = 0.0
    for row in rows:
        trade_date, open_, close, high, low, volume = row[:6]
        close_value = _num(close)
        pct_chg = ((close_value - previous_close) / previous_close * 100) if previous_close else 0.0
        bars.append(
            {
                "code": quote["code"],
                "trade_date": str(trade_date),
                "open": _num(open_),
                "high": _num(high),
                "low": _num(low),
                "close": close_value,
                "volume": _num(volume),
                "amount": close_value * _num(volume) * 100,
                "pct_chg": pct_chg,
                "turnover_rate": 0.0,
            }
        )
        previous_close = close_value
    if bars:
        bars[-1]["turnover_rate"] = float(quote.get("turnover_rate") or 0)
    return bars


def _fetch_akshare_kline(
    quote: dict[str, Any],
    settings: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except ImportError as exc:
        raise MarketDataError("未安装 akshare，请先安装后端依赖。") from exc
    sd = start_date or (date.today() - timedelta(days=settings.market_kline_days * 3)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")
    exchange = str(quote.get("exchange", "")).lower()
    symbol = f"{exchange}{quote['code']}"
    adjust = "" if start_date else "qfq"
    df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=sd, end_date=ed, adjust=adjust, timeout=15)
    if df is None or df.empty:
        raise MarketDataError(f"akshare_tx 返回空日K: {symbol}")
    if start_date is None:
        rows = df.tail(settings.market_kline_days).to_dict("records")
    else:
        rows = df.to_dict("records")
    bars: list[dict[str, Any]] = []
    previous_close = 0.0
    for row in rows:
        close_val = _num(row.get("close"))
        pct_chg = ((close_val - previous_close) / previous_close * 100) if previous_close else 0.0
        open_val = _num(row.get("open"))
        high_val = _num(row.get("high"))
        low_val = _num(row.get("low"))
        amount_val = _num(row.get("amount"))
        volume_val = amount_val / close_val / 100 if close_val else 0.0
        bars.append(
            {
                "code": quote["code"],
                "trade_date": str(row.get("date") or ""),
                "open": open_val,
                "high": high_val,
                "low": low_val,
                "close": close_val,
                "volume": volume_val,
                "amount": amount_val,
                "pct_chg": pct_chg,
                "turnover_rate": 0.0,
            }
        )
        previous_close = close_val
    if bars:
        bars[-1]["turnover_rate"] = float(quote.get("turnover_rate") or 0)
    return bars


def _fetch_baostock_kline(
    quote: dict[str, Any],
    settings: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    try:
        import baostock as bs
    except ImportError as exc:
        raise MarketDataError("未安装 baostock，请先安装后端依赖。") from exc

    exchange = quote["exchange"].lower()
    if exchange not in {"sh", "sz", "bj"}:
        raise MarketDataError(f"baostock 暂不支持交易所: {quote['exchange']}")
    code = f"{exchange}.{quote['code']}"
    if start_date:
        sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    else:
        sd = (date.today() - timedelta(days=settings.market_kline_days * 3)).strftime("%Y-%m-%d")
    if end_date:
        ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    else:
        ed = date.today().strftime("%Y-%m-%d")
    login_result = bs.login()
    if login_result.error_code != "0":
        raise MarketDataError(f"baostock login failed: {login_result.error_msg}")
    try:
        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume,amount,pctChg,turn",
            start_date=sd,
            end_date=ed,
            frequency="d",
            adjustflag="2",
        )
        if rs.error_code != "0":
            raise MarketDataError(f"baostock query failed: {rs.error_msg}")
        rows: list[list[str]] = []
        while rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()
    if start_date is None:
        rows = rows[-settings.market_kline_days :]
    return [
        {
            "code": quote["code"],
            "trade_date": str(row[0]),
            "open": _num(row[1]),
            "high": _num(row[2]),
            "low": _num(row[3]),
            "close": _num(row[4]),
            "volume": _num(row[5]),
            "amount": _num(row[6]),
            "pct_chg": _num(row[7]),
            "turnover_rate": _num(row[8]),
        }
        for row in rows
    ]


def _fetch_tushare_kline(
    quote: dict[str, Any],
    settings: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    if not settings.tushare_token:
        raise MarketDataError("未配置 ASHARE_TUSHARE_TOKEN。")
    try:
        import tushare as ts
    except ImportError as exc:
        raise MarketDataError("未安装 tushare，请先安装后端依赖。") from exc
    _throttle_tushare(settings)
    ts.set_token(settings.tushare_token)
    pro = ts.pro_api()
    ts_code = f"{quote['code']}.{quote['exchange']}"
    sd = start_date or (date.today() - timedelta(days=settings.market_kline_days * 3)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")
    daily = pro.daily(ts_code=ts_code, start_date=sd, end_date=ed)
    if daily is None or daily.empty:
        raise MarketDataError(f"tushare 返回空日K: {ts_code}")
    df = daily.sort_values("trade_date")
    if start_date is None:
        rows = df.tail(settings.market_kline_days).to_dict("records")
    else:
        rows = df.to_dict("records")
    bars = [
        {
            "code": quote["code"],
            "trade_date": _format_yyyymmdd(row.get("trade_date")),
            "open": _num(row.get("open")),
            "high": _num(row.get("high")),
            "low": _num(row.get("low")),
            "close": _num(row.get("close")),
            "volume": _num(row.get("vol")),
            "amount": _num(row.get("amount")) * 1000,
            "pct_chg": _num(row.get("pct_chg")),
            "turnover_rate": 0.0,
        }
        for row in rows
    ]
    if bars:
        bars[-1]["turnover_rate"] = float(quote.get("turnover_rate") or 0)
    return bars


_TUSHARE_INDUSTRY_MAP: dict[str, str] | None = None


_INDUSTRY_CACHE_PATH: str | None = None


def _industry_cache_path() -> str:
    global _INDUSTRY_CACHE_PATH
    if _INDUSTRY_CACHE_PATH is None:
        from pathlib import Path

        _INDUSTRY_CACHE_PATH = str(Path(__file__).resolve().parent.parent.parent / "data" / "industry_cache.json")
    return _INDUSTRY_CACHE_PATH


def _load_industry_cache() -> dict[str, str]:
    import json as _json
    from pathlib import Path

    p = Path(_industry_cache_path())
    if p.exists():
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_industry_cache(data: dict[str, str]) -> None:
    import json as _json
    from pathlib import Path

    p = Path(_industry_cache_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")


def fetch_tushare_industries(settings: Any) -> dict[str, str]:
    """从 Tushare stock_basic 获取 A 股行业分类映射，结果双级缓存（内存 + 文件）。"""
    global _TUSHARE_INDUSTRY_MAP
    if _TUSHARE_INDUSTRY_MAP is not None:
        return _TUSHARE_INDUSTRY_MAP
    cached = _load_industry_cache()
    if cached:
        _TUSHARE_INDUSTRY_MAP = cached
        return cached
    if not settings.tushare_token:
        return {}
    try:
        import tushare as ts
    except ImportError:
        return {}
    _throttle_tushare(settings)
    ts.set_token(settings.tushare_token)
    pro = ts.pro_api()
    df = pro.stock_basic(exchange="", list_status="L", fields="symbol,industry")
    if df is None or df.empty:
        return {}
    result: dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row.get("symbol") or "").strip()
        industry = str(row.get("industry") or "").strip()
        if code and industry:
            result[code] = industry
    _TUSHARE_INDUSTRY_MAP = result
    _save_industry_cache(result)
    return result


def _quote(**kwargs: Any) -> dict[str, Any]:
    code = _normalize_code(kwargs["code"])
    return {
        **kwargs,
        "code": code,
        "exchange": _exchange_from_code(code),
        "is_st": "ST" in str(kwargs.get("name", "")).upper() or "退" in str(kwargs.get("name", "")),
        "quote_time": datetime.utcnow().isoformat(),
    }


def _select_candidates(quotes: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    filtered = [
        item
        for item in quotes
        if _candidate_exclusion_reason(item) is None
    ]
    filtered.sort(key=_candidate_score, reverse=True)
    return filtered[:count]


def _candidate_exclusion_reason(item: dict[str, Any]) -> str | None:
    if not _is_supported_a_share(item):
        return "unsupported_market"
    name = str(item.get("name") or "")
    if item.get("is_st") or "ST" in name.upper() or "退" in name:
        return "st_or_delisting_risk"
    if float(item.get("price") or 0) <= 1:
        return "invalid_price"
    if float(item.get("amount_yi") or 0) <= 0 or float(item.get("turnover_rate") or 0) <= 0:
        return "suspended_or_no_trade"
    return None


def _candidate_filter_summary(quotes: list[dict[str, Any]]) -> dict[str, Any]:
    exclusion_counts: Counter[str] = Counter()
    eligible_count = 0
    high_liquidity_count = 0
    tradeable_quote_count = 0
    for item in quotes:
        reason = _candidate_exclusion_reason(item)
        if reason is not None:
            exclusion_counts[reason] += 1
            continue
        eligible_count += 1
        if float(item.get("amount_yi") or 0) >= 0.5 and float(item.get("turnover_rate") or 0) >= 0.1:
            high_liquidity_count += 1
        if float(item.get("amount_yi") or 0) >= 1.5 and 1.0 <= float(item.get("turnover_rate") or 0) <= 12.0:
            tradeable_quote_count += 1
    return {
        "total_count": len(quotes),
        "base_eligible_count": eligible_count,
        "excluded_count": len(quotes) - eligible_count,
        "high_liquidity_count": high_liquidity_count,
        "low_liquidity_count": max(0, eligible_count - high_liquidity_count),
        "tradeable_quote_count": tradeable_quote_count,
        "exclusion_counts": dict(exclusion_counts),
        "high_liquidity_threshold": {
            "amount_yi": 0.5,
            "turnover_rate": 0.1,
        },
        "preferred_tradeability_threshold": {
            "amount_yi": 1.5,
            "turnover_rate_min": 1.0,
            "turnover_rate_max": 12.0,
        },
    }


def _candidate_score(item: dict[str, Any]) -> float:
    return (
        _candidate_amount_score(float(item.get("amount_yi") or 0))
        + _candidate_pct_score(float(item.get("pct") or 0))
        + _candidate_turnover_score(float(item.get("turnover_rate") or 0))
        + _candidate_trend_score(float(item.get("chg60") or 0))
    )


def _candidate_amount_score(amount_yi: float) -> float:
    if amount_yi <= 0:
        return -20.0
    return min(18.0, math.log1p(amount_yi) * 7.5)


def _candidate_pct_score(pct: float) -> float:
    if pct <= -7:
        return -10.0
    if pct <= -3:
        return -5.0
    if pct < 0:
        return -1.5
    if pct <= 1:
        return 4.0
    if pct <= 5:
        return 15.0 - abs(pct - 3.0) * 2.0
    if pct <= 8:
        return 11.0 - (pct - 5.0) * 1.5
    return max(-6.0, 6.5 - (pct - 8.0) * 2.4)


def _candidate_turnover_score(turnover_rate: float) -> float:
    if turnover_rate < 0.8:
        return -8.0
    if turnover_rate < 2.0:
        return 3.0 + (turnover_rate - 0.8) * 4.0
    if turnover_rate <= 8.0:
        return 15.0 - abs(turnover_rate - 5.0) * 1.2
    if turnover_rate <= 12.0:
        return 8.5 - (turnover_rate - 8.0) * 2.0
    return max(-8.0, 0.5 - (turnover_rate - 12.0) * 1.8)


def _candidate_trend_score(chg60: float) -> float:
    if chg60 <= -15:
        return -8.0
    if chg60 < 0:
        return -2.5
    if chg60 <= 5:
        return 2.0 + chg60 * 0.6
    if chg60 <= 30:
        return 5.0 + (chg60 - 5.0) * 0.32
    if chg60 <= 50:
        return 13.0 - (chg60 - 30.0) * 0.22
    return max(-4.0, 8.6 - (chg60 - 50.0) * 0.28)


def _build_stock_from_bars(
    quote: dict[str, Any],
    bars: list[dict[str, Any]],
    quote_source: str,
    kline_source: str,
) -> dict[str, Any]:
    bars = sorted(bars, key=lambda item: item["trade_date"])
    latest = bars[-1]
    closes = [_num(bar["close"]) for bar in bars]
    highs = [_num(bar["high"]) for bar in bars]
    lows = [_num(bar["low"]) for bar in bars]
    volumes = [_num(bar["volume"]) for bar in bars]
    price = _num(quote.get("price"), latest["close"]) or latest["close"]

    # Prefer cached indicators from bars (set by market_pool._load_cached_bars)
    latest_indicator = latest.get("_indicator") or {}

    ma5 = latest.get("ma5") if latest.get("ma5") is not None else _ma(closes, 5)
    ma10 = latest.get("ma10") if latest.get("ma10") is not None else _ma(closes, 10)
    ma20 = latest.get("ma20") if latest.get("ma20") is not None else _ma(closes, 20)
    ma60 = latest.get("ma60") if latest.get("ma60") is not None else _ma(closes, 60)
    vol5_20 = latest.get("volume_ratio_5d") if latest.get("volume_ratio_5d") is not None else _safe_div(_ma(volumes, 5), _ma(volumes, 20), 1.0)

    chg20 = _chg(closes, 20)
    chg60 = _chg(closes, 60)
    max_high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    min_low20 = min(lows[-20:]) if len(highs) >= 20 else min(lows)
    previous_high20 = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1] or highs)
    max_high60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    yesterday_high = highs[-2] if len(highs) >= 2 else highs[-1]
    yesterday_low = lows[-2] if len(lows) >= 2 else lows[-1]

    market_data_gaps: list[str] = []
    if not quote.get("industry") or quote.get("industry") == "未分类":
        market_data_gaps.append("industry")
    if not quote.get("main_net_inflow_yi"):
        market_data_gaps.append("main_net_inflow")
    if not quote.get("pe_ttm"):
        market_data_gaps.append("valuation")

    # Bias values — prefer cached from DailyIndicator, fall back to computation
    bias_ma5_val = latest_indicator.get("bias_ma5")
    bias_ma10_val = latest_indicator.get("bias_ma10")
    bias_ma20_val = latest_indicator.get("bias_ma20")
    if bias_ma5_val is None:
        bias_ma5_val = _bias(price, ma5)
    if bias_ma10_val is None:
        bias_ma10_val = _bias(price, ma10)
    if bias_ma20_val is None:
        bias_ma20_val = _bias(price, ma20)

    support_ma5 = abs(bias_ma5_val) <= 2 and price >= ma5
    support_ma10 = abs(bias_ma10_val) <= 2.8 and price >= ma10

    # RSI — prefer cached
    rsi12 = latest_indicator.get("rsi_12") if latest_indicator.get("rsi_12") is not None else _rsi(closes, 12)

    # MACD — prefer cached
    cached_dif = latest_indicator.get("macd_dif")
    cached_dea = latest_indicator.get("macd_dea")
    if cached_dif is not None and cached_dea is not None:
        if cached_dif > 0 and cached_dif > cached_dea:
            macd_status = "零轴上金叉"
        elif cached_dif > cached_dea:
            macd_status = "金叉"
        elif cached_dif < 0 and cached_dif < cached_dea:
            macd_status = "空头"
        else:
            macd_status = "多头" if cached_dif > 0 else "下穿零轴"
    else:
        macd_status = _macd_status(closes)

    # Breakout — prefer cached
    breakout20 = latest_indicator.get("breakout20")
    breakout60 = latest_indicator.get("breakout60")
    if breakout20 is None:
        breakout20 = int(price >= previous_high20 * 0.995) if previous_high20 else 0
    if breakout60 is None:
        breakout60 = int(price >= max(highs[-61:-1] or highs) * 0.995)

    # Upper shadow count — prefer cached
    upper_shadow_count20 = latest_indicator.get("upper_shadow_count20")
    if upper_shadow_count20 is None:
        upper_shadow_count20 = _upper_shadow_count(bars[-20:])

    # Max drawdown — prefer cached
    max_dd20 = latest_indicator.get("max_dd20") if latest_indicator.get("max_dd20") is not None else _max_drawdown(closes[-20:])
    max_dd60 = latest_indicator.get("max_dd60") if latest_indicator.get("max_dd60") is not None else _max_drawdown(closes[-60:])

    stock = {
        "code": quote["code"],
        "name": quote["name"],
        "exchange": quote["exchange"],
        "industry": quote.get("industry") or "未分类",
        "price": round(price, 3),
        "pct": round(_num(quote.get("pct"), latest.get("pct_chg")), 3),
        "amount_yi": round(_num(quote.get("amount_yi"), latest.get("amount", 0) / 100000000), 3),
        "turnover_rate": round(_num(quote.get("turnover_rate"), latest.get("turnover_rate")), 3),
        "free_mv_yi": round(_num(quote.get("free_mv_yi")), 3),
        "total_mv_yi": round(_num(quote.get("total_mv_yi")), 3),
        "chg20": round(chg20, 3),
        "chg60": round(chg60, 3),
        "dist20": round(bias_ma20_val, 3),
        "dist60": round(_bias(price, ma60), 3),
        "pos20": round(_position(price, highs[-20:], lows[-20:]), 3),
        "pos60": round(_position(price, highs[-60:], lows[-60:]), 3),
        "pos120": round(_position(price, highs[-120:], lows[-120:]), 3),
        "eff20": round(_efficiency(closes, 20), 3),
        "vol5_20": round(vol5_20, 3),
        "ma5": round(ma5, 3),
        "ma10": round(ma10, 3),
        "ma20": round(ma20, 3),
        "ma60": round(ma60, 3),
        "support_ma5": support_ma5,
        "support_ma10": support_ma10,
        "rsi_12": round(rsi12, 3),
        "volume_shrink_pullback": vol5_20 <= 0.85 and (support_ma5 or support_ma10) and quote.get("pct", 0) <= 1,
        "volume_breakout_signal": vol5_20 >= 1.15 and price >= previous_high20 * 0.995 and quote.get("pct", 0) > 0,
        "upper_shadow_count20": upper_shadow_count20,
        "max_dd20": round(max_dd20, 3),
        "max_dd60": round(max_dd60, 3),
        "breakout20": breakout20,
        "breakout60": breakout60,
        "yesterday_high": round(yesterday_high, 3),
        "yesterday_low": round(yesterday_low, 3),
        "high_20d": round(max_high20, 3),
        "low_20d": round(min_low20, 3),
        "off_high_pct": round((price - max_high20) / max_high20 * 100, 3) if max_high20 else 0.0,
        "off_high60_pct": round((price - max_high60) / max_high60 * 100, 3) if max_high60 else 0.0,
        "limit_up_days20": sum(1 for bar in bars[-20:] if _num(bar.get("pct_chg")) >= 9.8),
        "announcement_negative_count": 0,
        "news_risk_count": 0,
        "is_st": bool(quote.get("is_st")),
        "data_source": "real_market",
        "quote_source": quote_source,
        "kline_source": kline_source,
        "latest_trade_date": latest["trade_date"],
        "quote_time": quote.get("quote_time"),
        "data_quality": {
            "strict_real_data": True,
            "kline_days": len(bars),
            "missing_fields": market_data_gaps,
            "bars_source": kline_source,
            "quote_source": quote_source,
        },
        "_bars": bars,
        "dsa_profile": {
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "ma60": ma60,
            "bias_ma5": bias_ma5_val,
            "bias_ma10": bias_ma10_val,
            "bias_ma20": bias_ma20_val,
            "volume_ratio_5d": vol5_20,
            "support_ma5": support_ma5,
            "support_ma10": support_ma10,
            "macd_status": macd_status,
            "rsi_12": rsi12,
            "main_net_inflow_yi": _num(quote.get("main_net_inflow_yi")),
            "inflow_5d_yi": _num(quote.get("main_net_inflow_yi")),
            "inflow_10d_yi": _num(quote.get("main_net_inflow_yi")),
            "sector_fund_rank": 99,
            "profit_ratio": 50,
            "concentration_90": 18,
            "chip_health": "未接入真实筹码分布，按中性处理",
            "pe_ttm": _num(quote.get("pe_ttm")),
            "positive_catalysts": [],
            "risk_alerts": [],
            "latest_news": "未接入实时新闻源",
            "sentiment_summary": "仅基于真实行情和日K计算，新闻/公告需人工复核",
            "volume_shrink_pullback": vol5_20 <= 0.85 and (support_ma5 or support_ma10) and quote.get("pct", 0) <= 1,
            "volume_breakout_signal": vol5_20 >= 1.15 and price >= previous_high20 * 0.995 and quote.get("pct", 0) > 0,
        },
    }
    return stock


def _apply_relative_features(stocks: list[dict[str, Any]]) -> None:
    if not stocks:
        return
    market_chg20 = statistics.fmean(float(item.get("chg20") or 0) for item in stocks)
    market_chg60 = statistics.fmean(float(item.get("chg60") or 0) for item in stocks)
    industry_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for stock in stocks:
        industry_rows[str(stock.get("industry") or "未分类")].append(stock)
    ranked_industries = _rank_industries(stocks)
    main_industries = {item for item, _score in ranked_industries[:3] if item != "未分类"}
    for stock in stocks:
        peers = industry_rows[str(stock.get("industry") or "未分类")]
        stock["primary_excess20"] = round(float(stock.get("chg20") or 0) - market_chg20, 3)
        stock["primary_excess60"] = round(float(stock.get("chg60") or 0) - market_chg60, 3)
        stock["industry_capacity_like_count"] = sum(1 for peer in peers if float(peer.get("amount_yi") or 0) >= 3)
        stock["industry_positive_excess20_count"] = sum(1 for peer in peers if float(peer.get("chg20") or 0) > market_chg20)
        stock["_is_main"] = stock.get("industry") in main_industries and stock.get("industry") != "未分类"
        profile = dict(stock.get("dsa_profile") or {})
        if stock["_is_main"]:
            profile["sector_fund_rank"] = min(8, profile.get("sector_fund_rank", 99))
        stock["dsa_profile"] = profile


def _build_snapshot(
    stocks: list[dict[str, Any]],
    quote_source: str,
    quote_errors: list[str],
    kline_sources: list[str],
    kline_usage: Counter[str],
    kline_errors: dict[str, list[str]],
    disabled_kline_sources: set[str] | None = None,
) -> dict[str, Any]:
    trade_date = max(str(item.get("latest_trade_date") or date.today().isoformat()) for item in stocks)
    up_count = sum(1 for item in stocks if item.get("pct", 0) > 0)
    down_count = sum(1 for item in stocks if item.get("pct", 0) < 0)
    limit_up_count = sum(1 for item in stocks if item.get("pct", 0) >= 9.8)
    limit_down_count = sum(1 for item in stocks if item.get("pct", 0) <= -9.8)
    main_industries = [name for name, _score in _rank_industries(stocks)[:3] if name != "未分类"]
    breadth = _breadth_from_counts(len(stocks), up_count, down_count, limit_up_count, limit_down_count)
    breadth.update(
        {
            "data_source": "real_market",
            "quote_source": quote_source,
            "kline_sources": dict(kline_usage),
            "configured_kline_sources": kline_sources,
            "disabled_kline_sources": sorted(disabled_kline_sources or []),
            "quote_errors": quote_errors,
            "kline_error_count": len(kline_errors),
            "generated_at": datetime.utcnow().isoformat(),
            "hot_theme": " / ".join(main_industries[:2]) if main_industries else "未知",
        }
    )
    return {
        "snapshot_id": f"snapshot_real_{quote_source}_{trade_date}_{datetime.utcnow().strftime('%H%M%S')}",
        "trade_date": trade_date,
        "sample_count": len(stocks),
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "main_industries": main_industries,
        "breadth": breadth,
    }


def _rank_industries(stocks: list[dict[str, Any]]) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for stock in stocks:
        industry = str(stock.get("industry") or "未分类")
        amount = max(0.0, float(stock.get("amount_yi") or 0))
        pct = float(stock.get("pct") or 0)
        chg20 = float(stock.get("chg20") or 0)
        scores[industry] += amount * (1 + max(-0.2, min(0.5, pct / 20))) + max(0.0, chg20) * 0.8
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _top_industries_from_quotes(quotes: list[dict[str, Any]]) -> list[str]:
    scores: dict[str, float] = defaultdict(float)
    for quote in quotes:
        industry = str(quote.get("industry") or "未分类")
        if industry == "未分类":
            continue
        amount = max(0.0, float(quote.get("amount_yi") or 0))
        pct = float(quote.get("pct") or 0)
        scores[industry] += amount * (1 + max(-0.2, min(0.5, pct / 20)))
    return [name for name, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:3]]


def _breadth_from_counts(
    sample_count: int,
    up_count: int,
    down_count: int,
    limit_up_count: int,
    limit_down_count: int,
) -> dict[str, Any]:
    sample = max(1, sample_count)
    up_ratio = up_count / sample
    limit_ratio = limit_up_count / sample
    if up_ratio >= 0.62 and limit_ratio >= 0.03:
        regime = "强势"
        risk = "中"
    elif up_ratio >= 0.55:
        regime = "结构性强势"
        risk = "中"
    elif up_ratio >= 0.42:
        regime = "震荡"
        risk = "中高"
    else:
        regime = "弱势"
        risk = "高"
    return {
        "regime": regime,
        "risk": risk,
        "up_ratio": round(up_ratio, 3),
        "down_ratio": round(down_count / sample, 3),
        "up_limit_ratio": round(limit_ratio, 3),
        "limit_down_ratio": round(limit_down_count / sample, 3),
        "leadership_height": None,
        "broken_board_rate": None,
    }


def _bar_from_eastmoney_line(code: str, line: str) -> dict[str, Any]:
    parts = line.split(",")
    return {
        "code": code,
        "trade_date": parts[0],
        "open": _num(parts[1]),
        "close": _num(parts[2]),
        "high": _num(parts[3]),
        "low": _num(parts[4]),
        "volume": _num(parts[5]),
        "amount": _num(parts[6]),
        "pct_chg": _num(parts[8]),
        "turnover_rate": _num(parts[10]) if len(parts) > 10 else 0.0,
    }


def _global_failed_kline_sources(errors: list[str]) -> set[str]:
    disabled: set[str] = set()
    for error in errors:
        source, _sep, message = error.partition(":")
        source = source.strip()
        if source and _is_global_kline_source_error(source, message):
            disabled.add(source)
    return disabled


def _summarize_kline_skip_reasons(kline_errors: dict[str, list[str]]) -> dict[str, int]:
    summary: Counter[str] = Counter()
    for errors in kline_errors.values():
        if not errors:
            summary["unknown"] += 1
            continue
        first = str(errors[0])
        if "不足30条" in first:
            summary["日K不足30条"] += 1
        elif "未覆盖报价日期" in first:
            summary["日K未覆盖报价日期"] += 1
        else:
            summary[first.split(":", 1)[0] or "其他"] += 1
    return dict(summary)


def _is_global_kline_source_error(source: str, message: str) -> bool:
    text = str(message)
    if source == "baostock":
        return "login failed" in text or "网络" in text or "Broken pipe" in text
    if source in {"thsdk", "thssdk"}:
        return "连接失败" in text or "TCP" in text
    return False


def _quote_from_thsdk_row(row: dict[str, Any]) -> dict[str, Any]:
    code = _normalize_code(_ths_code_from_row(row))
    return _quote(
        code=code,
        name=_first_text(row, ("名称", "证券简称", "股票简称", "name", "Name")),
        price=_first_num(row, ("价格", "最新价", "现价", "收盘价")),
        pct=_first_num(row, ("涨幅", "涨跌幅")),
        volume=_first_num(row, ("成交量", "总手")),
        amount_yi=_first_num(row, ("总金额", "成交额")) / 100000000,
        turnover_rate=_first_num(row, ("换手率",)),
        pe_ttm=_first_num(row, ("市盈率TTM", "市盈率(TTM)", "动态市盈率")),
        total_mv_yi=_first_num(row, ("总市值",)) / 100000000,
        free_mv_yi=_first_num(row, ("流通市值",)) / 100000000,
        chg60=0.0,
        ytd_chg=0.0,
        main_net_inflow_yi=_first_num(row, ("主力净流入",)) / 100000000,
        industry=_first_text(row, ("行业", "所属行业"), default="未分类"),
        source="thsdk",
    )


def _bar_from_thsdk_row(code: str, row: dict[str, Any], previous_close: float = 0.0) -> dict[str, Any]:
    trade_date = _thsdk_trade_date(row.get("时间") or row.get("日期") or row.get("date"))
    close = _first_num(row, ("收盘价", "收盘", "close"))
    pct_chg = _first_num(row, ("涨幅", "涨跌幅", "pct_chg"), default=None)
    if pct_chg is None:
        pct_chg = ((close - previous_close) / previous_close * 100) if previous_close else 0.0
    return {
        "code": code,
        "trade_date": trade_date,
        "open": _first_num(row, ("开盘价", "开盘", "open")),
        "high": _first_num(row, ("最高价", "最高", "high")),
        "low": _first_num(row, ("最低价", "最低", "low")),
        "close": close,
        "volume": _first_num(row, ("成交量", "总手", "volume")),
        "amount": _first_num(row, ("总金额", "成交额", "amount")),
        "pct_chg": pct_chg,
        "turnover_rate": _first_num(row, ("换手率", "turnover_rate")),
    }


def _thsdk_client(settings: Any) -> Any:
    try:
        from thsdk import THS
    except ImportError as exc:
        raise MarketDataError("未安装 thsdk，请先安装后端依赖。") from exc
    return THS(_thsdk_ops(settings))


def _thsdk_ops(settings: Any) -> dict[str, Any]:
    username = str(getattr(settings, "thsdk_username", "") or "")
    password = str(getattr(settings, "thsdk_password", "") or "")
    mac = str(getattr(settings, "thsdk_mac", "") or "")
    if not username or not password:
        return {}
    ops = {"username": username, "password": password}
    if mac:
        ops["mac"] = mac
    return ops


def _thsdk_rows(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _ths_code_from_quote_or_code(value: Any) -> str:
    if isinstance(value, dict):
        raw = str(value.get("ths_code") or value.get("代码") or value.get("code") or "")
        exchange = str(value.get("exchange") or "").upper()
    else:
        raw = str(value or "")
        exchange = ""
    text = raw.strip().upper()
    if len(text) >= 10 and text[:4] in {"USHA", "USZA", "USTM"}:
        return text[:10]
    code = _normalize_code(text)
    if not exchange:
        exchange = _exchange_from_code(code)
    market = {"SH": "USHA", "SZ": "USZA", "BJ": "USTM"}.get(exchange)
    return f"{market}{code}" if market and code else ""


def _ths_code_from_row(row: dict[str, Any], default_market: str = "") -> str:
    for key in ("THSCODE", "ths_code", "代码"):
        raw = str(row.get(key) or "").strip().upper()
        if len(raw) >= 10 and raw[:4] in {"USHA", "USZA", "USTM"}:
            return raw[:10]
        code = _normalize_code(raw)
        if code and default_market:
            return f"{default_market}{code}"
    market = str(row.get("MarketDisplay") or row.get("市场") or default_market or "").strip().upper()
    code = _normalize_code(row.get("Code") or row.get("证券代码") or row.get("股票代码") or row.get("代码") or "")
    if market in {"USHA", "USZA", "USTM"} and code:
        return f"{market}{code}"
    if code:
        return _ths_code_from_quote_or_code(code)
    return ""


def _ths_market_prefix(ths_code: str) -> str:
    return str(ths_code or "").strip().upper()[:4]


def _merge_non_empty(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if value is None or value == "":
            continue
        target[key] = value


def _first_text(row: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _first_num(row: dict[str, Any], keys: tuple[str, ...], default: float | None = 0.0) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return _num(value)
    return default


def _thsdk_trade_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value or "")
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return _format_yyyymmdd(text[:8]) if len(text) >= 8 else text


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _quote_sources(settings: Any) -> list[str]:
    if settings.data_provider in {"eastmoney", "akshare", "thsdk", "thssdk"}:
        return [settings.data_provider]
    return [source for source in _split_sources(settings.quote_sources) if source != "tushare"]


def _kline_sources(settings: Any) -> list[str]:
    if settings.data_provider in {"eastmoney", "akshare", "tushare", "thsdk", "thssdk", "baostock"}:
        return [settings.data_provider]
    return _split_sources(settings.kline_sources)


def _split_sources(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _throttle_tushare(settings: Any) -> None:
    limit = max(1, int(getattr(settings, "tushare_rate_limit_per_minute", 50) or 50))
    now = time.monotonic()
    with _TUSHARE_RATE_LOCK:
        while _TUSHARE_CALL_TIMES and now - _TUSHARE_CALL_TIMES[0] >= 60:
            _TUSHARE_CALL_TIMES.popleft()
        if len(_TUSHARE_CALL_TIMES) >= limit:
            sleep_for = max(0.0, 60 - (now - _TUSHARE_CALL_TIMES[0]))
            time.sleep(sleep_for)
            now = time.monotonic()
            while _TUSHARE_CALL_TIMES and now - _TUSHARE_CALL_TIMES[0] >= 60:
                _TUSHARE_CALL_TIMES.popleft()
        _TUSHARE_CALL_TIMES.append(now)


def _http_get_text(
    url: str,
    settings: Any,
    headers: dict[str, str] | None = None,
    encoding: str | None = None,
) -> str:
    last_error: Exception | None = None
    merged_headers = dict(HTTP_HEADERS)
    if headers:
        merged_headers.update(headers)
    for _attempt in range(3):
        try:
            with httpx.Client(timeout=settings.market_request_timeout_seconds, headers=merged_headers, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                if encoding:
                    response.encoding = encoding
                return response.text
        except Exception as exc:  # noqa: BLE001 - surfaced in MarketDataError after retries
            last_error = exc
    raise MarketDataError(str(last_error) if last_error else "HTTP 文本请求失败")


def _default_quote_code_list(limit: int) -> list[str]:
    base_codes = [
        "sh600519", "sz000858", "sz300750", "sh601318", "sh600036", "sh600030", "sh601127", "sz002594",
        "sh600309", "sz000333", "sh603259", "sh688041", "sz002475", "sh601899", "sz300059", "sh601688",
        "sh600887", "sz000001", "sz300308", "sh601012", "sz002230", "sh600276", "sh601166", "sh600406",
        "sh600941", "sh601398", "sh600031", "sh601888", "sz000725", "sh600760", "sz300274", "sz002371",
        "sh688111", "sh600050", "sh601857", "sh600036", "sh600000", "sz000063", "sz300502", "sh601919",
        "sh603986", "sz000568", "sh600585", "sh601601", "sz002463", "sh600584", "sz300760", "sh600690",
        "sz000977", "sh601628", "sz300124", "sh601600", "sh600438", "sz000538", "sh601111", "sh600809",
        "sz002714", "sh601318", "sh600660", "sz002415", "sh601899", "sh600111", "sh688981", "sz300015",
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for code in base_codes:
        if code not in seen:
            seen.add(code)
            deduped.append(code)
        if len(deduped) >= limit:
            break
    return deduped[:limit]


def _parse_sina_quotes(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or '="' not in raw:
            continue
        prefix, payload = raw.split('="', 1)
        payload = payload.rstrip('";')
        symbol = prefix.split('hq_str_', 1)[-1]
        parts = payload.split(',')
        if len(parts) < 10 or not parts[0].strip():
            continue
        code = ''.join(ch for ch in symbol if ch.isdigit())[-6:]
        if not code:
            continue
        open_price = _num(parts[1])
        prev_close = _num(parts[2])
        price = _num(parts[3])
        high = _num(parts[4])
        low = _num(parts[5])
        volume_shares = _num(parts[8])
        amount = _num(parts[9])
        pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
        rows.append(
            _quote(
                code=code,
                name=parts[0].strip(),
                price=price or prev_close or open_price,
                pct=pct,
                volume=volume_shares / 100,
                amount_yi=amount / 100000000,
                turnover_rate=0.0,
                pe_ttm=0.0,
                total_mv_yi=0.0,
                free_mv_yi=0.0,
                chg60=0.0,
                ytd_chg=0.0,
                main_net_inflow_yi=0.0,
                industry="未分类",
                source="sina",
            )
        )
    return rows


def _parse_tencent_quotes(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in text.split(';'):
        raw = raw.strip()
        if not raw or '="' not in raw:
            continue
        prefix, payload = raw.split('="', 1)
        payload = payload.rstrip('";')
        symbol = prefix.split('_', 1)[-1]
        parts = payload.split('~')
        if len(parts) < 46 or not parts[1].strip():
            continue
        code = ''.join(ch for ch in symbol if ch.isdigit())[-6:]
        if not code:
            continue
        price = _num(parts[3])
        prev_close = _num(parts[4])
        volume_lot = _num(parts[36])
        amount_wan = _num(parts[37])
        turnover_rate = _num(parts[38])
        pe_ttm = _num(parts[39])
        total_mv_yi = _num(parts[45]) / 100
        free_mv_yi = _num(parts[44]) / 100
        pct = ((price - prev_close) / prev_close * 100) if prev_close else _num(parts[32])
        rows.append(
            _quote(
                code=code,
                name=parts[1].strip(),
                price=price,
                pct=pct,
                volume=volume_lot * 100,
                amount_yi=amount_wan / 10000,
                turnover_rate=turnover_rate,
                pe_ttm=pe_ttm,
                total_mv_yi=total_mv_yi,
                free_mv_yi=free_mv_yi,
                chg60=0.0,
                ytd_chg=0.0,
                main_net_inflow_yi=0.0,
                industry="未分类",
                source="tencent",
            )
        )
    return rows


def _http_get_json(url: str, settings: Any) -> dict[str, Any]:
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            with httpx.Client(timeout=settings.market_request_timeout_seconds, headers=HTTP_HEADERS, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.json()
        except Exception as exc:  # noqa: BLE001 - surfaced in MarketDataError after retries
            last_error = exc
    raise MarketDataError(str(last_error) if last_error else "HTTP 请求失败")


def _is_supported_a_share(item: dict[str, Any]) -> bool:
    code = str(item.get("code") or "")
    return len(code) == 6 and code[0] in {"0", "2", "3", "6", "8", "4"} and item.get("exchange") in {"SH", "SZ", "BJ"}


def _normalize_code(code: str) -> str:
    text = "".join(ch for ch in str(code) if ch.isdigit())
    return text.zfill(6)[-6:] if text else ""


def _exchange_from_code(code: str) -> str:
    if code.startswith(("60", "68", "900")):
        return "SH"
    if code.startswith(("8", "4")):
        return "BJ"
    return "SZ"


def _eastmoney_market_id(code: str) -> int:
    if _exchange_from_code(code) == "SH":
        return 1
    if _exchange_from_code(code) == "BJ":
        return 0
    return 0


def _latest_trade_date_from_quotes(quotes: list[dict[str, Any]]) -> str:
    trade_dates: list[str] = []
    for quote in quotes:
        raw = str(quote.get("trade_date") or quote.get("date") or "").strip()
        if not raw:
            continue
        if len(raw) == 8 and raw.isdigit():
            raw = _format_yyyymmdd(raw)
        if len(raw) >= 10:
            trade_dates.append(raw[:10])
    if trade_dates:
        return max(trade_dates)
    return _latest_allowed_bar_date()


def _latest_allowed_bar_date(today: date | None = None) -> str:
    today = today or date.today()
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def _latest_tushare_trade_date(pro: Any) -> str:
    today = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=today, is_open="1")
    if cal.empty:
        raise MarketDataError("Tushare 未返回交易日历。")
    return str(cal.sort_values("cal_date").iloc[-1]["cal_date"])


def _format_yyyymmdd(value: Any) -> str:
    text = str(value)
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _ma(values: list[float], window: int) -> float:
    sample = values[-window:] if len(values) >= window else values
    return statistics.fmean(sample) if sample else 0.0


def _chg(values: list[float], window: int) -> float:
    if len(values) <= window or values[-window - 1] == 0:
        return 0.0
    return (values[-1] - values[-window - 1]) / values[-window - 1] * 100


def _bias(price: float, ma: float) -> float:
    return (price - ma) / ma * 100 if ma else 0.0


def _position(price: float, highs: list[float], lows: list[float]) -> float:
    if not highs or not lows:
        return 50.0
    high = max(highs)
    low = min(lows)
    return (price - low) / (high - low) * 100 if high > low else 50.0


def _efficiency(values: list[float], window: int) -> float:
    if len(values) <= window:
        return 0.0
    direction = abs(values[-1] - values[-window - 1])
    noise = sum(abs(values[index] - values[index - 1]) for index in range(len(values) - window, len(values)))
    return direction / noise if noise else 0.0


def _upper_shadow_count(bars: list[dict[str, Any]]) -> int:
    count = 0
    for bar in bars:
        high = _num(bar.get("high"))
        low = _num(bar.get("low"))
        open_ = _num(bar.get("open"))
        close = _num(bar.get("close"))
        spread = high - low
        if spread > 0 and (high - max(open_, close)) / spread >= 0.45:
            count += 1
    return count


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_dd = min(max_dd, (value - peak) / peak * 100)
    return max_dd


def _rsi(values: list[float], window: int) -> float:
    if len(values) <= window:
        return 50.0
    deltas = [values[index] - values[index - 1] for index in range(len(values) - window, len(values))]
    gains = [delta for delta in deltas if delta > 0]
    losses = [-delta for delta in deltas if delta < 0]
    avg_gain = statistics.fmean(gains) if gains else 0.0
    avg_loss = statistics.fmean(losses) if losses else 0.0
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _macd_status(values: list[float]) -> str:
    if len(values) < 35:
        return "上穿零轴"
    dif, dea = _macd(values)
    if dif > 0 and dif > dea:
        return "零轴上金叉"
    if dif > dea:
        return "金叉"
    if dif < 0 and dif < dea:
        return "空头"
    return "多头" if dif > 0 else "下穿零轴"


def _macd(values: list[float]) -> tuple[float, float]:
    ema12 = _ema(values, 12)
    ema26 = _ema(values, 26)
    dif_series: list[float] = []
    for index in range(len(values)):
        subset = values[: index + 1]
        dif_series.append(_ema(subset, 12) - _ema(subset, 26))
    dif = ema12 - ema26
    dea = _ema(dif_series, 9)
    return dif, dea


def _ema(values: list[float], span: int) -> float:
    if not values:
        return 0.0
    alpha = 2 / (span + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _safe_div(left: float, right: float, default: float = 0.0) -> float:
    return left / right if right else default


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "" or value == "-":
        return float(default)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(number) or math.isinf(number):
        return float(default)
    return number

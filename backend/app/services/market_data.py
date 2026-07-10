from __future__ import annotations

import math
import statistics
import threading
import time
from collections import Counter, defaultdict
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.services.outcome_service import DEFAULT_STOP_LOSS_PCT, DEFAULT_TAKE_PROFIT_PCT

# 实时行情代理地址（realtime_quote 是爬虫接口，不走 Pro API 代理）
_REALTIME_PROXY_URL = "https://realtime.stockai888.top"


def _patch_tushare_verify_token() -> None:
    """绕过 Tushare 库 verify_token 装饰器 bug。

    verify_token.py 第 32 行 ``r = verify_token(...).json()`` 已将 response
    解析为 dict，但第 39 行错误访问 ``r.text``（dict 无 text 属性），导致
    realtime_quote 等被装饰的接口在权限验证失败时抛出 AttributeError。
    """
    try:
        import tushare.util.verify_token as vt

        if getattr(vt, "_patched", False):
            return

        def _mock_verify_token(token=None, event_name="", event_detail=""):  # noqa: ANN001
            class _MockResp:
                def json(self):  # noqa: ANN201
                    return {"message": "success"}

            return _MockResp()

        vt.verify_token = _mock_verify_token
        vt._patched = True
    except Exception:
        pass


_patch_tushare_verify_token()


class MarketDataError(RuntimeError):
    pass


_TUSHARE_CALL_TIMES: deque[float] = deque()
_TUSHARE_RATE_LOCK = threading.Lock()


def get_market_universe() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    quote_source, quotes, quote_errors = _fetch_first_candidate_quotes(_quote_sources(settings), settings)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    recall_plan = _candidate_recall_plan(total_quote_count, len(eligible_quotes), settings)
    candidates = _select_recall_candidates(eligible_quotes, recall_plan["broad_recall_count"])
    if not candidates:
        raise MarketDataError("真实行情源返回了空股票池，已停止运行。")

    kline_sources = _kline_sources(settings)
    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()

    # 优先从本地 daily_kbars 批量加载K线（避免逐股票 Tushare 调用导致 10+ 分钟延迟）
    candidate_codes = [q["code"] for q in candidates[: recall_plan["scan_target_count"]]]
    cached_bars_map: dict[str, list[dict[str, Any]]] = {}
    try:
        from app.db.session import SessionLocal
        from app.services.market_pool import _load_cached_bars_batch
        db = SessionLocal()
        try:
            cached_bars_map = _load_cached_bars_batch(db, candidate_codes, settings.market_kline_days)
        finally:
            db.close()
    except Exception:
        pass

    for quote in candidates:
        if len(stocks) >= recall_plan["scan_target_count"]:
            break
        code = str(quote["code"])
        # 使用本地缓存的K线
        cached = cached_bars_map.get(code, [])
        if cached and len(cached) >= 30:
            stock = _build_stock_from_bars(quote, cached, quote_source=quote_source, kline_source="local_pool")
            stocks.append(stock)
            kline_usage["local_pool"] += 1
            continue
        # 缓存不足时回退到 Tushare 逐股票拉取
        bars, source, errors = _fetch_first_kline(quote, kline_sources, settings)
        if not bars:
            kline_errors[code] = errors
            continue
        stock = _build_stock_from_bars(quote, bars, quote_source=quote_source, kline_source=source)
        stocks.append(stock)
        kline_usage[source] += 1

    if not stocks:
        detail = "; ".join(f"{code}: {' | '.join(errors[:2])}" for code, errors in list(kline_errors.items())[:8])
        raise MarketDataError(f"所有候选股票都无法取得真实日K，已停止运行。{detail}")

    quote_snapshot = _build_quote_snapshot(
        eligible_quotes,
        data_source="real_market",
        quote_source=quote_source,
        quote_errors=quote_errors,
    )
    _apply_relative_features(stocks)
    snapshot = _build_snapshot(
        stocks=stocks,
        quote_source=quote_source,
        quote_errors=quote_errors,
        kline_sources=kline_sources,
        kline_usage=kline_usage,
        kline_errors=kline_errors,
        quote_snapshot=quote_snapshot,
    )
    snapshot.setdefault("breadth", {}).update(
        {
            "full_market_mode": recall_plan["full_market_mode"],
            "unified_quote_total": total_quote_count,
            "base_filter_eligible_count": len(eligible_quotes),
            "candidate_filter": filter_summary,
            "actual_scan_count": len(stocks),
            "kline_missing_count": len(kline_errors),
            "kline_skip_reasons": _summarize_kline_skip_reasons(kline_errors),
            "candidate_limit_config": {
                "market_candidate_count": recall_plan["explicit_scan_limit"],
                "lookahead_multiplier": recall_plan["lookahead_multiplier"],
                "lookahead_count": recall_plan["broad_recall_count"],
                "target_scan_count": recall_plan["scan_target_count"],
                "broad_recall_count": recall_plan["broad_recall_count"],
                "broad_recall_limited": recall_plan["broad_recall_limited"],
                "recall_strategy": "multi_bucket_quote_recall",
            },
        }
    )
    return stocks, snapshot


def get_market_snapshot() -> dict[str, Any]:
    settings = get_settings()
    quote_source, quotes, quote_errors = _fetch_first_candidate_quotes(_quote_sources(settings), settings)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    if not eligible_quotes:
        raise MarketDataError("真实行情源返回了空市场快照。")
    snapshot = _build_quote_snapshot(
        eligible_quotes,
        data_source="real_market",
        quote_source=quote_source,
        quote_errors=quote_errors,
    )
    snapshot["breadth"].update(
        {
            "candidate_filter": filter_summary,
            "unified_quote_total": len(quotes),
            "base_filter_eligible_count": len(eligible_quotes),
            "actual_scan_count": len(eligible_quotes),
        }
    )
    return snapshot


def _fetch_first_quotes(sources: list[str], settings: Any) -> tuple[str, list[dict[str, Any]], list[str]]:
    try:
        quotes = _fetch_quotes("tushare", settings)
        if quotes:
            return "tushare", quotes, []
    except Exception as exc:  # noqa: BLE001 - returned to the local operator as run evidence
        raise MarketDataError("真实行情快照获取失败，已停止运行。" + str(exc)) from exc
    raise MarketDataError("真实行情快照获取失败，已停止运行。Tushare 返回空。")


def _fetch_first_candidate_quotes(sources: list[str], settings: Any) -> tuple[str, list[dict[str, Any]], list[str]]:
    try:
        quotes = _fetch_quotes("tushare", settings)
        if _eligible_market_quotes(quotes):
            return "tushare", quotes, []
    except Exception as exc:  # noqa: BLE001 - returned to the local operator as run evidence
        raise MarketDataError("真实行情候选池获取失败，已停止运行。" + str(exc)) from exc
    raise MarketDataError("真实行情候选池获取失败，已停止运行。Tushare 返回空。")


def _eligible_market_quotes(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in quotes
        if _candidate_exclusion_reason(item) is None
    ]


def _candidate_recall_plan(total_quote_count: int, eligible_count: int, settings: Any) -> dict[str, Any]:
    explicit_scan_limit = max(0, int(getattr(settings, "market_candidate_count", 0) or 0))
    lookahead_multiplier = max(1, int(getattr(settings, "market_candidate_lookahead_multiplier", 4) or 1))
    broad_recall_default = max(0, int(getattr(settings, "market_broad_recall_count", 1800) or 0))
    full_market_mode = explicit_scan_limit <= 0

    if explicit_scan_limit > 0:
        broad_recall_count = min(
            eligible_count,
            max(explicit_scan_limit, explicit_scan_limit * lookahead_multiplier),
        )
        scan_target_count = min(eligible_count, explicit_scan_limit)
    else:
        if broad_recall_default <= 0:
            broad_recall_count = eligible_count
        else:
            broad_recall_count = min(eligible_count, broad_recall_default)
        scan_target_count = broad_recall_count

    return {
        "full_market_mode": full_market_mode,
        "explicit_scan_limit": explicit_scan_limit,
        "lookahead_multiplier": lookahead_multiplier,
        "broad_recall_count": broad_recall_count,
        "scan_target_count": scan_target_count,
        "broad_recall_limited": broad_recall_count < eligible_count,
        "total_quote_count": total_quote_count,
        "eligible_count": eligible_count,
    }


def _select_recall_candidates(quotes: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if not quotes:
        return []

    ordered = list(quotes)
    if count <= 0 or count >= len(ordered):
        return sorted(
            ordered,
            key=lambda item: (_candidate_score(item), float(item.get("amount_yi") or 0)),
            reverse=True,
        )

    window = min(len(ordered), max(count * 3, 160))
    recall_scores: dict[str, float] = defaultdict(float)
    recall_factors: dict[str, set[str]] = defaultdict(set)
    ranking_specs = [
        ("composite", lambda item: _candidate_score(item), 1.0),
        ("amount", lambda item: float(item.get("amount_yi") or 0), 0.75),
        ("turnover", lambda item: _candidate_turnover_score(float(item.get("turnover_rate") or 0)), 0.65),
        ("pct", lambda item: _candidate_pct_score(float(item.get("pct") or 0)), 0.65),
        ("trend", lambda item: _candidate_trend_score(float(item.get("chg60") or 0)), 0.55),
    ]
    for factor_name, key_fn, weight in ranking_specs:
        ranked = sorted(ordered, key=key_fn, reverse=True)[:window]
        for rank, item in enumerate(ranked):
            code = str(item.get("code") or "")
            if not code:
                continue
            recall_scores[code] += weight * (window - rank)
            recall_factors[code].add(factor_name)

    industry_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ordered:
        industry = str(item.get("industry") or "未分类")
        if industry != "未分类":
            industry_rows[industry].append(item)
    industry_quota = max(2, min(8, max(1, count // 150)))
    for industry in _top_industries_from_quotes(ordered)[:12]:
        ranked = sorted(
            industry_rows.get(industry, []),
            key=lambda item: (_candidate_score(item), float(item.get("amount_yi") or 0)),
            reverse=True,
        )[:industry_quota]
        for rank, item in enumerate(ranked):
            code = str(item.get("code") or "")
            if not code:
                continue
            recall_scores[code] += max(12.0, window * 0.35) - rank
            recall_factors[code].add("industry")

    selected = sorted(
        ordered,
        key=lambda item: (
            recall_scores.get(str(item.get("code") or ""), 0.0),
            len(recall_factors.get(str(item.get("code") or ""), set())),
            _candidate_score(item),
            float(item.get("amount_yi") or 0),
        ),
        reverse=True,
    )[:count]
    result = [dict(item) for item in selected]
    for item in result:
        code = str(item.get("code") or "")
        item["_quote_recall_score"] = round(recall_scores.get(code, 0.0), 2)
        item["_quote_recall_factors"] = sorted(recall_factors.get(code, set()))
    return result


def _fetch_first_kline(
    quote: dict[str, Any],
    sources: list[str],
    settings: Any,
    disabled_sources: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """获取单只股票的日K线（local-pool 优先，Tushare 兜底）。"""
    errors: list[str] = []
    try:
        bars = _fetch_kline("tushare", quote, settings)
        if len(bars) >= 30:
            return bars, "tushare", errors
        errors.append("tushare: 日K不足30条")
    except Exception as exc:
        errors.append(f"tushare: {exc}")
    return [], "", errors


def _fetch_quotes(source: str, settings: Any) -> list[dict[str, Any]]:
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
    return _fetch_tushare_kline(quote, settings, start_date=start_date, end_date=end_date)


def _load_local_stock_basics() -> list[dict[str, Any]]:
    try:
        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.tables import Stock
    except Exception:
        return []

    industry_cache = _load_industry_cache()
    db = SessionLocal()
    try:
        rows = db.execute(
            select(
                Stock.code,
                Stock.name,
                Stock.industry,
                Stock.market,
                Stock.list_date,
                Stock.is_st,
            )
        ).all()
    except Exception:
        db.close()
        return []
    db.close()

    stocks: list[dict[str, Any]] = []
    for code, name, industry, market, list_date, is_st in rows:
        symbol = _normalize_code(code)
        if not symbol:
            continue
        stocks.append(
            {
                "symbol": symbol,
                "name": str(name or "").strip() or symbol,
                "industry": str(industry or industry_cache.get(symbol) or "").strip() or "未分类",
                "market": str(market or "").strip(),
                "list_date": _format_yyyymmdd(list_date) if list_date else "",
                "is_st": bool(is_st),
            }
        )
    return stocks


def _fetch_stock_basics(pro: Any, settings: Any) -> list[dict[str, Any]]:
    local_rows = _load_local_stock_basics()
    if local_rows:
        return local_rows

    basic = _call_tushare_with_retry(
        pro,
        settings,
        "stock_basic",
        exchange="",
        list_status="L",
        fields="symbol,name,industry,market,list_date",
    )
    if basic is None or basic.empty:
        return []

    industry_cache = _load_industry_cache()
    stocks: list[dict[str, Any]] = []
    for _, row in basic.iterrows():
        symbol = _normalize_code(str(row.get("symbol") or ""))
        if not symbol:
            continue
        stocks.append(
            {
                "symbol": symbol,
                "name": str(row.get("name") or "").strip() or symbol,
                "industry": str(row.get("industry") or industry_cache.get(symbol) or "").strip() or "未分类",
                "market": str(row.get("market") or "").strip(),
                "list_date": _format_yyyymmdd(row.get("list_date")) if row.get("list_date") else "",
                "is_st": False,
            }
        )
    return stocks


def _recent_open_trade_dates(settings: Any, *, allow_current_session: bool = False) -> list[str]:
    from app.services.tushare_client import get_pro

    pro = get_pro()
    today_str = date.today().strftime("%Y%m%d")
    start_str = (date.today() - timedelta(days=14)).strftime("%Y%m%d")
    cal = _call_tushare_with_retry(
        pro,
        settings,
        "trade_cal",
        exchange="SSE",
        start_date=start_str,
        end_date=today_str,
        is_open="1",
    )
    if cal is None or cal.empty:
        raise MarketDataError("Tushare 未返回交易日历。")
    latest_allowed = (
        _latest_allowed_quote_date() if allow_current_session else _latest_allowed_bar_date()
    ).replace("-", "")
    return [
        str(value)
        for value in cal.sort_values("cal_date")["cal_date"].astype(str).tolist()
        if str(value) <= latest_allowed
    ]


def _batch_quote_tradeable_count(
    batch_quotes: Any,
    stock_basics: list[dict[str, Any]],
    *,
    require_turnover: bool,
    daily_basic_map: dict[str, dict[str, Any]] | None = None,
) -> int:
    if batch_quotes is None or batch_quotes.empty:
        return 0

    basics_by_code = {
        _normalize_code(str(row.get("symbol") or "")): row
        for row in stock_basics
        if _normalize_code(str(row.get("symbol") or ""))
    }
    count = 0
    for _, row in batch_quotes.iterrows():
        code = _normalize_code(str(row.get("ts_code") or row.get("code") or "").split(".")[0])
        if not code:
            continue
        basic = basics_by_code.get(code, {})
        if not _is_supported_a_share({"code": code, "exchange": _exchange_from_code(code)}):
            continue
        name = str(basic.get("name") or row.get("name") or "")
        if bool(basic.get("is_st")) or "ST" in name.upper() or "退" in name:
            continue
        price = _num(row.get("close"))
        amount_yi = _num(row.get("amount")) * 1000 / 100000000
        basic_row = (daily_basic_map or {}).get(code, {})
        turnover_rate = _num(
            row.get("turn_over"),
            _num(row.get("turnover_rate"), _num(basic_row.get("turnover_rate"))),
        )
        has_turnover = turnover_rate > 0 or not require_turnover
        if price > 1 and amount_yi > 0 and has_turnover:
            count += 1
    return count


def _min_complete_batch_quote_count(stock_count: int) -> int:
    if stock_count < 20:
        return 1
    return max(20, min(300, int(stock_count * 0.05)))


def _is_complete_batch_quote_snapshot(
    batch_quotes: Any,
    stock_basics: list[dict[str, Any]],
    *,
    require_turnover: bool,
    daily_basic_map: dict[str, dict[str, Any]] | None = None,
) -> tuple[bool, int, int]:
    tradeable_count = _batch_quote_tradeable_count(
        batch_quotes,
        stock_basics,
        require_turnover=require_turnover,
        daily_basic_map=daily_basic_map,
    )
    min_tradeable_count = _min_complete_batch_quote_count(len(stock_basics))
    return tradeable_count >= min_tradeable_count, tradeable_count, min_tradeable_count


def _load_tushare_daily_basic_map(pro: Any, settings: Any, trade_date: str) -> dict[str, dict[str, Any]]:
    try:
        daily_basic = _call_tushare_with_retry(
            pro,
            settings,
            "daily_basic",
            trade_date=trade_date,
            fields="ts_code,trade_date,turnover_rate,pe_ttm,total_mv,circ_mv",
        )
        if daily_basic is None or daily_basic.empty:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for _, row in daily_basic.iterrows():
            code = _normalize_code(str(row.get("ts_code") or "").split(".")[0])
            if code:
                result[code] = dict(row)
        return result
    except Exception:
        return {}


def _fetch_tushare_realtime_frame(ts_codes: list[str], settings: Any):
    import tushare as ts
    from tushare.stock import cons as ct

    _throttle_tushare(settings)
    ct.verify_token_url = _REALTIME_PROXY_URL
    return ts.realtime_quote(ts_code=",".join(ts_codes))


def _fetch_tushare_realtime_quotes(
    stock_basics: list[dict[str, Any]],
    pro: Any,
    settings: Any,
    closed_trade_date: str,
) -> list[dict[str, Any]]:
    ts_codes = [
        f"{code}.{_exchange_from_code(code)}"
        for code in (_normalize_code(str(row.get("symbol") or "")) for row in stock_basics)
        if code
    ]
    if not ts_codes:
        return []

    realtime_map: dict[str, dict[str, Any]] = {}
    chunk_size = 50  # realtime_quote sina 源单次最多 50 只
    for start in range(0, len(ts_codes), chunk_size):
        frame = _fetch_tushare_realtime_frame(ts_codes[start:start + chunk_size], settings)
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            item = dict(row)
            code = _normalize_code(str(_row_value(item, "TS_CODE", "ts_code", "code") or "").split(".")[0])
            if code:
                realtime_map[code] = item

    if not realtime_map:
        return []

    daily_basic_map = _load_tushare_daily_basic_map(pro, settings, closed_trade_date.replace("-", ""))
    quotes: list[dict[str, Any]] = []
    for stock in stock_basics:
        symbol = _normalize_code(str(stock.get("symbol") or ""))
        if not symbol:
            continue
        quote_row = realtime_map.get(symbol)
        if not quote_row:
            continue
        basic_row = daily_basic_map.get(symbol, {})
        trade_date = _format_yyyymmdd(_row_value(quote_row, "DATE", "date"))
        price = _num(_row_value(quote_row, "PRICE", "price"))
        preclose = _num(_row_value(quote_row, "PRE_CLOSE", "pre_close", "preclose"))
        amount_yi = _num(_row_value(quote_row, "AMOUNT", "amount")) / 100000000
        free_mv_yi = _market_value_yi(None, fallback=basic_row.get("circ_mv"))
        total_mv_yi = _market_value_yi(None, fallback=basic_row.get("total_mv"))
        turnover_rate = round(amount_yi / free_mv_yi * 100, 4) if free_mv_yi > 0 else _num(basic_row.get("turnover_rate"))
        pct = round((price - preclose) / preclose * 100, 4) if price > 0 and preclose > 0 else 0.0
        quotes.append(
            _quote(
                code=symbol,
                name=str(stock.get("name") or _row_value(quote_row, "NAME", "name") or symbol).strip() or symbol,
                trade_date=trade_date,
                price=price,
                pct=pct,
                volume=_num(_row_value(quote_row, "VOLUME", "volume", "volumn")),
                amount_yi=amount_yi,
                turnover_rate=turnover_rate,
                pe_ttm=_num(basic_row.get("pe_ttm")),
                total_mv_yi=total_mv_yi,
                free_mv_yi=free_mv_yi,
                high=_num(_row_value(quote_row, "HIGH", "high")),
                low=_num(_row_value(quote_row, "LOW", "low")),
                open=_num(_row_value(quote_row, "OPEN", "open")),
                preclose=preclose,
                chg60=0.0,
                ytd_chg=0.0,
                main_net_inflow_yi=0.0,
                industry=str(stock.get("industry") or "未分类").strip() or "未分类",
                is_st=bool(stock.get("is_st")),
                source="tushare",
            )
        )
    return [quote for quote in quotes if quote.get("price", 0) > 0 and quote.get("trade_date")]


def _row_value(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row.get(name)
    return None


def _quote_list_is_complete(quotes: list[dict[str, Any]], stock_count: int) -> tuple[bool, int, int]:
    tradeable_count = sum(1 for quote in quotes if _candidate_exclusion_reason(quote) is None)
    min_tradeable_count = _min_complete_batch_quote_count(stock_count)
    return tradeable_count >= min_tradeable_count, tradeable_count, min_tradeable_count








def _fetch_tushare_quotes(settings: Any) -> list[dict[str, Any]]:
    """使用 Tushare 获取 A 股行情快照，盘中优先实时行情，收盘后回退日线批量。"""
    from app.services.tushare_client import get_pro

    pro = get_pro()

    stock_basics = _fetch_stock_basics(pro, settings)
    if not stock_basics:
        raise MarketDataError("本地 stocks 表与 Tushare stock_basic 都不可用，无法构建行情池。")

    recent_trade_dates = _recent_open_trade_dates(settings, allow_current_session=True)
    latest_open_trade_date = recent_trade_dates[-1] if recent_trade_dates else ""
    latest_quote_trade_date = _latest_allowed_quote_date().replace("-", "")
    latest_closed_trade_date = _latest_allowed_bar_date().replace("-", "")
    realtime_error = ""
    if latest_open_trade_date and latest_open_trade_date == latest_quote_trade_date:
        realtime_basic_trade_date = (
            recent_trade_dates[-2]
            if len(recent_trade_dates) >= 2
            else latest_closed_trade_date
        )
        try:
            realtime_quotes = _fetch_tushare_realtime_quotes(
                stock_basics,
                pro,
                settings,
                _format_yyyymmdd(realtime_basic_trade_date),
            )
            realtime_trade_date = _latest_trade_date_from_quotes(realtime_quotes).replace("-", "")
            is_complete, tradeable_count, min_tradeable_count = _quote_list_is_complete(realtime_quotes, len(stock_basics))
            if realtime_trade_date == latest_open_trade_date and is_complete:
                return realtime_quotes[: getattr(settings, "market_scan_size", len(realtime_quotes))]
            realtime_error = (
                f"realtime_quote:{latest_open_trade_date}: incomplete snapshot "
                f"({tradeable_count}/{min_tradeable_count} tradeable, latest={realtime_trade_date or 'none'})"
            )
        except Exception as exc:  # noqa: BLE001 - fallback to daily batch with evidence
            realtime_error = f"realtime_quote:{latest_open_trade_date}: {exc}"

    # 再批量拉全市场日线行情，避免逐股票高频调用触发限速。
    batch_quotes = None
    selected_trade_date = ""
    selected_api_name = ""
    selected_daily_basic_map: dict[str, dict[str, Any]] | None = None
    batch_quote_errors: list[str] = [realtime_error] if realtime_error else []
    recent_trade_dates = _recent_open_trade_dates(settings)
    for trade_date in reversed(recent_trade_dates[-5:]):
        for api_name in ("bak_daily", "daily"):
            try:
                candidate = _call_tushare_with_retry(pro, settings, api_name, trade_date=trade_date)
                if candidate is not None and not candidate.empty:
                    candidate_daily_basic_map = (
                        _load_tushare_daily_basic_map(pro, settings, trade_date)
                        if api_name == "daily"
                        else None
                    )
                    is_complete, tradeable_count, min_tradeable_count = _is_complete_batch_quote_snapshot(
                        candidate,
                        stock_basics,
                        require_turnover=True,
                        daily_basic_map=candidate_daily_basic_map,
                    )
                    if not is_complete:
                        batch_quote_errors.append(
                            f"{trade_date}:{api_name}: incomplete snapshot "
                            f"({tradeable_count}/{min_tradeable_count} tradeable)"
                        )
                        continue
                    batch_quotes = candidate
                    selected_trade_date = trade_date
                    selected_api_name = api_name
                    selected_daily_basic_map = candidate_daily_basic_map
                    break
                batch_quote_errors.append(f"{trade_date}:{api_name}: empty")
            except Exception as exc:  # noqa: BLE001 - accumulate evidence for fallback
                batch_quote_errors.append(f"{trade_date}:{api_name}: {exc}")
        if batch_quotes is not None and not batch_quotes.empty:
            break
    if batch_quotes is None or batch_quotes.empty:
        detail = "；".join(batch_quote_errors) if batch_quote_errors else "batch quote empty"
        raise MarketDataError(f"Tushare 批量行情拉取失败: {detail}")

    batch_quote_map: dict[str, dict[str, Any]] = {}
    for _, row in batch_quotes.iterrows():
        code = _normalize_code(str(row.get("ts_code") or row.get("code") or "").split(".")[0])
        if code:
            batch_quote_map[code] = dict(row)

    daily_basic_map = (
        selected_daily_basic_map
        if selected_daily_basic_map is not None
        else _load_tushare_daily_basic_map(pro, settings, selected_trade_date)
    )

    stocks_list: list[dict[str, Any]] = []
    quote_trade_date = _format_yyyymmdd(selected_trade_date)
    for row in stock_basics:
        symbol = _normalize_code(str(row.get("symbol") or ""))
        if not symbol:
            continue
        quote_row = batch_quote_map.get(symbol, {})
        basic_row = daily_basic_map.get(symbol, {})
        name = str(row.get("name") or quote_row.get("name") or symbol).strip() or symbol
        industry = str(row.get("industry") or quote_row.get("industry") or "未分类").strip() or "未分类"
        exchange = _exchange_from_code(symbol)
        pct = _num(quote_row.get("pct_chg"), _num(quote_row.get("pct_change")))
        price = _num(quote_row.get("close"))
        amount_yi = _tushare_amount_to_yi(_num(quote_row.get("amount")), selected_api_name)
        turnover_rate = _num(quote_row.get("turn_over"), _num(basic_row.get("turnover_rate")))
        pe_ttm = _num(quote_row.get("pe"), _num(basic_row.get("pe_ttm")))
        total_mv_yi = _market_value_yi(
            quote_row.get("total_mv"),
            fallback=basic_row.get("total_mv"),
        )
        free_mv_yi = _market_value_yi(
            quote_row.get("float_mv"),
            fallback=basic_row.get("circ_mv"),
        )
        stocks_list.append({
            "code": symbol,
            "name": name,
            "industry": industry,
            "exchange": exchange,
            "trade_date": quote_trade_date,
            "price": price,
            "pct": pct,
            "volume": _num(quote_row.get("vol"), _num(quote_row.get("volume"))),
            "amount_yi": amount_yi,
            "turnover_rate": turnover_rate,
            "pe_ttm": pe_ttm,
            "total_mv_yi": total_mv_yi,
            "free_mv_yi": free_mv_yi,
            "high": _num(quote_row.get("high")),
            "low": _num(quote_row.get("low")),
            "open": _num(quote_row.get("open")),
            "preclose": _num(quote_row.get("pre_close"), _num(quote_row.get("preclose"))),
            "chg60": 0.0,
            "ytd_chg": 0.0,
            "main_net_inflow_yi": 0.0,
            "is_st": bool(row.get("is_st")),
        })

    quotes = [
        _quote(
            code=s["code"],
            name=s["name"],
            trade_date=s["trade_date"],
            price=s.get("price", 0.0),
            pct=s.get("pct", 0.0),
            volume=s.get("volume", 0),
            amount_yi=s.get("amount_yi", 0.0),
            turnover_rate=s.get("turnover_rate", 0.0),
            pe_ttm=s.get("pe_ttm", 0.0),
            total_mv_yi=s.get("total_mv_yi", 0.0),
            free_mv_yi=s.get("free_mv_yi", 0.0),
            high=s.get("high"),
            low=s.get("low"),
            open=s.get("open"),
            preclose=s.get("preclose"),
            chg60=s.get("chg60", 0.0),
            ytd_chg=s.get("ytd_chg", 0.0),
            main_net_inflow_yi=s.get("main_net_inflow_yi", 0.0),
            industry=s["industry"],
            is_st=s.get("is_st", False),
            source="tushare",
        )
        for s in stocks_list
        if s.get("price", 0) > 0 and s["code"]
    ]

    return quotes[: getattr(settings, "market_scan_size", len(quotes))]


def _call_tushare_with_retry(pro: Any, settings: Any, api_name: str, **params: Any):
    last_exc: Exception | None = None
    for attempt in range(3):
        _throttle_tushare(settings)
        try:
            return getattr(pro, api_name)(**params)
        except Exception as exc:  # noqa: BLE001 - preserve provider message for caller
            last_exc = exc
            message = str(exc)
            if "请求速度过快" not in message or attempt == 2:
                break
            time.sleep(1.5 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise MarketDataError(f"Tushare {api_name} 调用失败")




def _fetch_tushare_kline(
    quote: dict[str, Any],
    settings: Any,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    from app.services.tushare_client import get_pro

    _throttle_tushare(settings)
    pro = get_pro()
    ts_code = f"{quote['code']}.{quote['exchange']}"
    sd = start_date or (date.today() - timedelta(days=settings.market_kline_days * 3)).strftime("%Y%m%d")
    ed = end_date or date.today().strftime("%Y%m%d")
    daily = pro.daily(ts_code=ts_code, start_date=sd, end_date=ed)
    if daily is None or daily.empty:
        raise MarketDataError(f"tushare 返回空日K: {ts_code}")

    _daily_basic_cache: dict[str, dict[str, float]] = {}
    try:
        basic = pro.daily_basic(ts_code=ts_code, start_date=sd, end_date=ed, fields="trade_date,turnover_rate")
        if basic is not None and not basic.empty:
            for _, row in basic.iterrows():
                _daily_basic_cache[str(row["trade_date"]).strip()] = {
                    "turnover_rate": float(row.get("turnover_rate") or 0),
                }
    except Exception:
        pass

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
            "turnover_rate": _daily_basic_cache.get(str(row.get("trade_date")).strip(), {}).get("turnover_rate", 0.0),
        }
        for row in rows
    ]
    if bars:
        bars[-1]["turnover_rate"] = bars[-1]["turnover_rate"] or float(quote.get("turnover_rate") or 0)
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
    try:
        from app.services.tushare_client import get_pro
    except ImportError:
        return {}
    _throttle_tushare(settings)
    pro = get_pro()
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
        "is_st": bool(kwargs.get("is_st"))
        or "ST" in str(kwargs.get("name", "")).upper()
        or "退" in str(kwargs.get("name", "")),
        "quote_time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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


def _quote_sources(settings: Any) -> list[str]:
    return ["tushare"]


def _kline_sources(settings: Any) -> list[str]:
    return ["tushare"]


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

    # Prefer cached indicators from bars (set by market_pool._load_cached_bars).
    # 注意: ma 字段缓存值可能是 0.0(backfill 缺数据时写入),0.0 对均线无效,
    # 必须视为缺失并 fallback 到 _ma(closes,N) 重算,否则 dist60/breakout 连锁归零
    # 导致依赖趋势/突破的策略 prefilter 死锁(high-range-breakout-momentum 等)。
    latest_indicator = latest.get("_indicator") or {}

    ma5 = latest.get("ma5") or _ma(closes, 5)
    ma10 = latest.get("ma10") or _ma(closes, 10)
    ma20 = latest.get("ma20") or _ma(closes, 20)
    ma60 = latest.get("ma60") or _ma(closes, 60)
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
    rsi6 = latest_indicator.get("rsi_6") if latest_indicator.get("rsi_6") is not None else _rsi(closes, 6)
    rsi24 = latest_indicator.get("rsi_24") if latest_indicator.get("rsi_24") is not None else _rsi(closes, 24)

    # MACD — prefer cached
    cached_dif = latest_indicator.get("macd_dif")
    cached_dea = latest_indicator.get("macd_dea")
    if cached_dif is not None and cached_dea is not None:
        macd_dif_val = float(cached_dif)
        macd_dea_val = float(cached_dea)
        macd_bar_val = round((macd_dif_val - macd_dea_val) * 2, 4)
        if macd_dif_val > 0 and macd_dif_val > macd_dea_val:
            macd_status = "零轴上金叉"
        elif macd_dif_val > macd_dea_val:
            macd_status = "金叉"
        elif macd_dif_val < 0 and macd_dif_val < macd_dea_val:
            macd_status = "空头"
        else:
            macd_status = "多头" if macd_dif_val > 0 else "下穿零轴"
    else:
        dif, dea = _macd(closes)
        macd_dif_val = round(dif, 4)
        macd_dea_val = round(dea, 4)
        macd_bar_val = round((macd_dif_val - macd_dea_val) * 2, 4)
        if macd_dif_val > 0 and macd_dif_val > macd_dea_val:
            macd_status = "零轴上金叉"
        elif macd_dif_val > macd_dea_val:
            macd_status = "金叉"
        elif macd_dif_val < 0 and macd_dif_val < macd_dea_val:
            macd_status = "空头"
        else:
            macd_status = "多头" if macd_dif_val > 0 else "下穿零轴"

    # Breakout — 用原始 highs 重算(backfill 缓存的 breakout 可能为 0 误判,
    # 且依赖 previous_high60 等需 ma60 正确;直接重算避免连锁错误)
    breakout20 = int(price >= previous_high20 * 0.995) if previous_high20 else 0
    breakout60 = int(price >= max(highs[-61:-1] or highs) * 0.995) if len(highs) >= 2 else 0

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
        # 筹码集中度顶层字段（前移计算，供 prefilter 阶段使用，与 market_pool._load_cached_bars_batch
        # 一致的计算口径）。此前只在 dsa_profile 嵌套字典里赋值，顶层 stock 字典缺失该字段，
        # 导致 chip-single-peak-breakout 策略 prefilter.market.include 的
        # chip_concentration >= 20 因 strict_missing 判定为缺失字段而永远拒绝，
        # 该策略历史全部推荐样本实为 fallback fill 补齐凑数，未真正命中过规则。
        "chip_concentration": round(
            (_num(latest.get("chip_cost_85")) - _num(latest.get("chip_cost_15"))) / _num(latest.get("chip_cost_15")) * 100, 2
        ) if _num(latest.get("chip_cost_15")) > 0 and latest.get("chip_cost_85") else None,
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
            "macd_dif": macd_dif_val,
            "macd_dea": macd_dea_val,
            "macd_bar": macd_bar_val,
            "rsi_6": rsi6,
            "rsi_12": rsi12,
            "rsi_24": rsi24,
            "main_net_inflow_yi": _num(quote.get("main_net_inflow_yi")),
            "inflow_5d_yi": _num(quote.get("main_net_inflow_yi")),
            "inflow_10d_yi": _num(quote.get("main_net_inflow_yi")),
            "sector_fund_rank": 99,
            # 筹码数据：从 daily_kbars 获取真实 Tushare 筹码分布，缺失时用中性估算
            "profit_ratio": _num(latest.get("chip_winner_rate"), 50),
            "avg_cost": _num(latest.get("chip_weight_avg"), round(price * 0.94, 2)),
            "cost_90_low": _num(latest.get("chip_cost_15"), round(price * 0.92, 2)),
            "cost_90_high": _num(latest.get("chip_cost_85"), round(price * 1.08, 2)),
            "cost_70_low": _num(latest.get("chip_cost_15"), round(price * 0.96, 2)),
            "cost_70_high": _num(latest.get("chip_cost_85"), round(price * 1.04, 2)),
            "concentration_90": _num(
                round((_num(latest.get("chip_cost_85")) - _num(latest.get("chip_cost_15"))) / price * 100, 1)
                if latest.get("chip_cost_85") and latest.get("chip_cost_15") else None,
                18,
            ),
            "concentration_70": _num(
                round((_num(latest.get("chip_cost_85")) - _num(latest.get("chip_cost_15"))) / price * 70, 1)
                if latest.get("chip_cost_85") and latest.get("chip_cost_15") else None,
                12,
            ),
            "chip_health": "真实筹码分布" if latest.get("chip_weight_avg") else "未接入真实筹码分布，按中性处理",
            "pe_ttm": _num(quote.get("pe_ttm")),
            "positive_catalysts": [],
            "risk_alerts": [],
            "latest_news": "未接入实时新闻源",
            "sentiment_summary": "仅基于真实行情和日K计算，新闻/公告需人工复核",
            "volume_shrink_pullback": vol5_20 <= 0.85 and (support_ma5 or support_ma10) and quote.get("pct", 0) <= 1,
            "volume_breakout_signal": vol5_20 >= 1.15 and price >= previous_high20 * 0.995 and quote.get("pct", 0) > 0,
        },
        # ── 顶层交易字段：供 feature_snapshot / outcome_service 直接读取 ──
        "ideal_buy": round(max(ma10, ma20, price * 0.985), 2),
        "secondary_buy": round(max(ma20, price * 0.97), 2),
        "buy_signal": "持有" if support_ma5 else "观望",
        "stop_loss": round(max(ma20 * 0.95, price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)), 2),
        "take_profit": round(price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0), 2),
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
        stock["is_main_theme"] = stock.get("industry") in main_industries and stock.get("industry") != "未分类"
        profile = dict(stock.get("dsa_profile") or {})
        if stock["is_main_theme"]:
            profile["sector_fund_rank"] = min(8, profile.get("sector_fund_rank", 99))
        stock["dsa_profile"] = profile


def _build_quote_snapshot(
    quotes: list[dict[str, Any]],
    data_source: str,
    quote_source: str,
    quote_errors: list[str],
) -> dict[str, Any]:
    trade_date = _latest_trade_date_from_quotes(quotes)
    up_count = sum(1 for item in quotes if item.get("pct", 0) > 0)
    down_count = sum(1 for item in quotes if item.get("pct", 0) < 0)
    limit_up_count = sum(1 for item in quotes if item.get("pct", 0) >= 9.8)
    limit_down_count = sum(1 for item in quotes if item.get("pct", 0) <= -9.8)
    main_industries = _top_industries_from_quotes(quotes)
    breadth = _breadth_from_counts(len(quotes), up_count, down_count, limit_up_count, limit_down_count)
    breadth.update(
        {
            "data_source": data_source,
            "quote_source": quote_source,
            "quote_errors": quote_errors,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "hot_theme": " / ".join(main_industries[:2]) if main_industries else "未知",
        }
    )
    return {
        "snapshot_id": f"snapshot_{data_source}_{quote_source}_{trade_date}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}",
        "trade_date": trade_date,
        "sample_count": len(quotes),
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "main_industries": main_industries,
        "breadth": breadth,
    }


def _build_snapshot(
    stocks: list[dict[str, Any]],
    quote_source: str,
    quote_errors: list[str],
    kline_sources: list[str],
    kline_usage: Counter[str],
    kline_errors: dict[str, list[str]],
    quote_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if quote_snapshot is not None:
        trade_date = str(quote_snapshot.get("trade_date") or date.today().isoformat())
        up_count = int(quote_snapshot.get("up_count") or 0)
        down_count = int(quote_snapshot.get("down_count") or 0)
        limit_up_count = int(quote_snapshot.get("limit_up_count") or 0)
        limit_down_count = int(quote_snapshot.get("limit_down_count") or 0)
        main_industries = list(quote_snapshot.get("main_industries") or [])
        breadth = dict(quote_snapshot.get("breadth") or {})
        snapshot_id = str(quote_snapshot.get("snapshot_id") or "")
        sample_count = int(quote_snapshot.get("sample_count") or 0)
    else:
        trade_date = max(str(item.get("latest_trade_date") or date.today().isoformat()) for item in stocks)
        up_count = sum(1 for item in stocks if item.get("pct", 0) > 0)
        down_count = sum(1 for item in stocks if item.get("pct", 0) < 0)
        limit_up_count = sum(1 for item in stocks if item.get("pct", 0) >= 9.8)
        limit_down_count = sum(1 for item in stocks if item.get("pct", 0) <= -9.8)
        main_industries = [name for name, _score in _rank_industries(stocks)[:3] if name != "未分类"]
        breadth = _breadth_from_counts(len(stocks), up_count, down_count, limit_up_count, limit_down_count)
        snapshot_id = f"snapshot_real_{quote_source}_{trade_date}_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}"
        sample_count = len(stocks)
    breadth.update(
        {
            "data_source": breadth.get("data_source") or "real_market",
            "quote_source": quote_source,
            "kline_sources": dict(kline_usage),
            "configured_kline_sources": kline_sources,
            "quote_errors": quote_errors,
            "kline_error_count": len(kline_errors),
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "hot_theme": breadth.get("hot_theme") or (" / ".join(main_industries[:2]) if main_industries else "未知"),
        }
    )
    return {
        "snapshot_id": snapshot_id,
        "trade_date": trade_date,
        "sample_count": sample_count,
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


def _split_sources(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _throttle_tushare(settings: Any) -> None:
    limit = max(0, int(getattr(settings, "tushare_rate_limit_per_minute", 0) or 0))
    if limit == 0:
        return
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


def _is_supported_a_share(item: dict[str, Any]) -> bool:
    code = str(item.get("code") or "")
    if not (len(code) == 6 and code[0] in {"0", "2", "3", "6", "8", "4"}):
        return False
    # stock_quotes 表可能没有 exchange 字段，从 code 前缀推断
    exchange = item.get("exchange") or _exchange_from_code(code)
    return exchange in {"SH", "SZ", "BJ"}


def _normalize_code(code: str) -> str:
    text = "".join(ch for ch in str(code) if ch.isdigit())
    return text.zfill(6)[-6:] if text else ""


def _exchange_from_code(code: str) -> str:
    if code.startswith(("60", "68", "900")):
        return "SH"
    if code.startswith(("8", "4")):
        return "BJ"
    return "SZ"


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
    if today is None:
        now = datetime.now()
        today = now.date()
        if today.weekday() < 5 and (now.hour < 17 or (now.hour == 17 and now.minute < 30)):
            # 17:30 前视为当日 daily 未就绪（Tushare 盘后约 17:00 后才出当日收盘数据）
            today = _previous_weekday(today)
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def _latest_allowed_quote_date(today: date | None = None) -> str:
    today = today or datetime.now().date()
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def _previous_weekday(day: date) -> date:
    current = day - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


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
    if len(values) < 26:
        ema12 = _ema(values, 12)
        ema26 = _ema(values, 26)
        return ema12 - ema26, 0.0
    alpha12 = 2 / 13
    alpha26 = 2 / 27
    alpha9 = 2 / 10
    ema12 = values[0]
    ema26 = values[0]
    dif_series: list[float] = []
    for value in values:
        ema12 = alpha12 * value + (1 - alpha12) * ema12
        ema26 = alpha26 * value + (1 - alpha26) * ema26
        dif_series.append(ema12 - ema26)
    dif = dif_series[-1]
    dea = dif_series[0]
    for d in dif_series[1:]:
        dea = alpha9 * d + (1 - alpha9) * dea
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


def _tushare_amount_to_yi(raw_amount: float, api_name: str) -> float:
    """Tushare amount 字段转亿元。

    Tushare API 的 amount 单位因接口而异（常见陷阱）：
    - daily: 千元（×1000=元，÷1e8=亿元）
    - bak_daily: 万元（×10000=元，÷1e8=亿元）
    - realtime_quote: 元（÷1e8=亿元）

    之前 _fetch_tushare_quotes 统一用 *1000/1e8 对 bak_daily 少算 10 倍，
    导致 stock_quotes.amount_yi 偏小，策略 amount_yi 门槛误筛涨停股。
    """
    multiplier = 10000 if api_name == "bak_daily" else 1000
    return raw_amount * multiplier / 100000000


def _market_value_yi(value: Any, fallback: Any = None) -> float:
    """Normalize Tushare market value fields to 亿元.

    `bak_daily.total_mv/float_mv` is observed as 亿元 in current batch quote
    data, while `daily_basic.total_mv/circ_mv` is documented as 万元.
    Some tests and fallback rows may still provide yuan-level large numbers,
    so large primary values are treated as yuan and converted defensively.
    """
    primary = _num(value)
    if primary > 0:
        return round(primary / 100000000, 4) if primary >= 1000000 else round(primary, 4)
    secondary = _num(fallback)
    if secondary > 0:
        return round(secondary / 10000, 4)
    return 0.0

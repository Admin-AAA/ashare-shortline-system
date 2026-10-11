"""行情池核心：报价池（quote pool）+ 日K池（bars）+ candidate readiness + universe/snapshot 组装。

任务 5.1（需求 5.2）：从原 `market_pool/__init__.py`（混杂核心行情池与 ~15 组辅助数据集
两类职责的单文件）中拆出核心报价/日K池部分。对外只暴露 universe/snapshot/readiness
语义的一组函数：

- `get_market_universe_from_pool` / `get_market_universe_from_pool_with_realtime`
- `get_market_snapshot_from_pool`
- `refresh_quote_pool`
- `local_pool_recommendation_readiness`
- `enrich_quote_with_local_metrics`

其余 `_` 前缀函数是内部实现细节（quote 合并/持久化/缓存读取、日K 增量刷新、
数据源健康度记录等），仍留在本模块内部，不算作"对外暴露"（需求 5.2）。

`_record_source_health` 虽然是核心池内部记录数据源健康度的函数，但辅助数据集
（limit_up/concept_sector/dragon_tiger/capital_flow/news）的通用刷新壳子
`_refresh_aux_data` 也需要记录健康度——为避免复制一份，`auxiliary.py` 从本模块
`import` 该函数，而不是各自重新实现（这正是本次重构要消除的"重复"）。

`_resolve_snapshot_trade_date` / `_date_window_days` 同理：核心池自身用它们解析
snapshot 交易日与 TTL 窗口天数，辅助数据集的刷新/加载入口（limit_up/concept_sector/
dragon_tiger/capital_flow/news/auction/pledge/margin/hsgt/holder/survey/fina 等）
也需要解析"目标交易日"，同样从本模块 import 复用，不在 auxiliary.py 重造。
"""
from __future__ import annotations

import logging
import statistics
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tables import (
    DailyKbar,
    DataSourceHealth,
    MarketSnapshot,
    StockQuote,
)
from app.services.data_sources import enabled_sources
from app.services.daily_bar_write_policy import (
    SHARES_PER_LOT,
    group_rows_by_quote_metric_presence,
    normalize_quote_metric,
    quote_metrics_look_unready,
)
from app.services.market_data import (
    MarketDataError,
    _apply_relative_features,
    _build_quote_snapshot,
    _build_snapshot,
    _build_stock_from_bars,
    _candidate_recall_plan,
    _candidate_filter_summary,
    _eligible_market_quotes,
    _exchange_from_code,
    _fetch_tushare_realtime_frame,
    _format_yyyymmdd,
    _latest_trade_date_from_quotes,
    _normalize_code,
    _num,
    _row_value,
    _select_recall_candidates,
)
from app.engine.trading_calendar import (
    latest_allowed_bar_date as _latest_allowed_bar_date,
    latest_allowed_quote_date as _latest_allowed_quote_date,
    normalize_trade_date as _normalize_trade_date,
    previous_weekday as _previous_weekday,
)
from app.services.market_pool.history_maturity import (
    is_natural_short_history as _is_natural_short_history,
    load_history_maturity_map as _load_history_maturity_map,
)
from app.services import market_pool_adapters as pool_adapters
from app.services.market_source.port import (
    BatchStatus,
    DailyBarRequest,
    MarketSourceError,
    QuoteRef,
    QuoteRequest,
)

_quote_sources = pool_adapters.quote_sources
_kline_sources = pool_adapters.kline_sources
_fetch_quote_batch = pool_adapters.fetch_quote_batch
_fetch_daily_bar_batch = pool_adapters.fetch_daily_bar_batch


NUMERIC_QUOTE_FIELDS = [
    "price",
    "pct",
    "volume",
    "amount_yi",
    "turnover_rate",
    "pe_ttm",
    "total_mv_yi",
    "free_mv_yi",
    "chg60",
    "ytd_chg",
    "main_net_inflow_yi",
    "high",
    "low",
    "open",
    "preclose",
]

STRICT_CONSENSUS_FIELDS = {"price": 0.018, "pct": 0.08}
DERIVED_RETURN_FIELDS = {"chg60", "ytd_chg"}
LOCAL_DAILY_BAR_FIELDS = DERIVED_RETURN_FIELDS | {"ma5", "ma10", "ma20"}
STOCK_QUOTE_UPSERT_BATCH_SIZE = 500
# daily_kbars is an 80-column wide table, so its upsert takes far more row locks
# per statement than stock_quotes.  Matches the batch size the gap-remediation
# service already uses for the same table (L-141).
DAILY_KBAR_UPSERT_BATCH_SIZE = 200

# ---------------------------------------------------------------------------
# 派生列的 upsert 覆盖保护（L-144）
#
# 日常同步只带回权威行情（OHLCV/pct_chg/turnover_rate），派生指标要么由本地
# backfill 计算、要么来自 Tushare stk_factor/cyq_perf 这类可能拉不到的接口。
# 但它们此前全在 on_duplicate_key_update 列表里，于是每跑一次同步/推荐就用当批
# 的 NULL（或 `or 0` 兜底出来的 0）把 backfill 补好的真值刷掉——实测每跑一次
# 推荐就发生一次，Stage 4 刚补满的 vol_ratio_5 在下一个 run 里即被清零。
#
# 按列语义分两类处理：
#   _DERIVED_NUMERIC_COLUMNS: 0 与 NULL 都不是有效取值（均线、乖离、量比、回撤、
#       RSI/MACD/KDJ/BOLL/CCI、筹码成本），用 COALESCE(NULLIF(新值,0), 旧值)——
#       当批带真值就更新，当批无效就保留库里已有的值。
#   _DERIVED_COUNTER_COLUMNS: 0 是**合法**取值（"没有突破"/"零根长上影"），
#       NULLIF 会把合法的 0 误判成缺失，所以只能整列退出 update，由 backfill
#       独占写入权；INSERT 新行时仍会写入当批值。
# ---------------------------------------------------------------------------
_DERIVED_NUMERIC_COLUMNS = (
    "ma5", "ma10", "ma20", "ma60", "ma250",
    "macd_dif", "macd_dea", "macd_bar",
    "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24",
    "boll_upper", "boll_mid", "boll_lower", "cci",
    "bias_5", "bias_10", "bias_20",
    "vol_ratio_5", "vol_ratio_20",
    "max_dd20", "max_dd60",
)
_DERIVED_COUNTER_COLUMNS = ("breakout20", "breakout60", "upper_shadow_count20")


def _preserve_derived_on_duplicate(stmt: Any) -> dict[str, Any]:
    """构造派生列的 ON DUPLICATE KEY UPDATE 表达式：当批无效则保留库中旧值。"""
    return {
        name: func.coalesce(func.nullif(getattr(stmt.inserted, name), 0), getattr(DailyKbar, name))
        for name in _DERIVED_NUMERIC_COLUMNS
    }


def _new_snapshot_id(prefix: str, trade_date: str, *, now: datetime | None = None) -> str:
    """Create a collision-resistant ID for a run-owned market snapshot."""
    created_at = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return f"{prefix}_{trade_date}_{created_at.strftime('%H%M%S')}_{uuid4().hex[:12]}"


def _required_bar_date_for_quote(quote: dict[str, Any]) -> str:
    quote_date = _normalize_trade_date(quote.get("trade_date") or date.today().isoformat())
    latest_quote_date = _latest_allowed_quote_date()
    if quote_date >= latest_quote_date:
        return _latest_allowed_bar_date(_previous_weekday(date.fromisoformat(latest_quote_date)))
    return _latest_allowed_bar_date(date.fromisoformat(quote_date))



def _date_window_days(ttl_seconds: int | None, minimum_days: int = 1) -> int:
    if not ttl_seconds:
        return minimum_days
    return max(minimum_days, int((int(ttl_seconds) + 86399) // 86400))


def _resolve_snapshot_trade_date(db: Session, trade_date: str | None = None) -> str:
    target = _normalize_trade_date(trade_date)
    if target:
        return target

    latest_snapshot = db.scalar(select(MarketSnapshot.trade_date).order_by(MarketSnapshot.trade_date.desc()).limit(1))
    if latest_snapshot:
        return _normalize_trade_date(latest_snapshot)

    latest_quote = db.scalar(
        select(func.max(StockQuote.trade_date)).where(StockQuote.freshness_status != "inactive")
    )
    if latest_quote:
        return _normalize_trade_date(latest_quote)

    latest_bar = db.scalar(select(func.max(DailyKbar.trade_date)))
    if latest_bar:
        return _normalize_trade_date(latest_bar)

    return date.today().isoformat()


def get_market_universe_from_pool(db: Session, refresh: bool = False, as_of_date: str | None = None, quotes_override: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = get_settings()
    # as_of_date 指定历史日期时强制走只读路径（不刷新），避免用最新行情覆盖历史日期的语义
    effective_refresh = refresh and (as_of_date is None) and quotes_override is None
    if quotes_override is not None:
        # 历史回放路径：quotes 由调用方从历史数据池（daily_kbars+daily_basic+时点元数据）
        # 合成，跳过 stock_quotes 实时报价池（其历史覆盖不足）。其余流程与生产一致。
        quotes, quote_meta = quotes_override, {"errors": [], "status": "historical_replay", "source_count": 1, "sources": ["historical_pool"]}
    else:
        quotes, quote_meta = _load_or_refresh_quotes(db, settings) if effective_refresh else _load_quotes_from_pool(db, settings, skip_metrics=True, as_of_date=as_of_date)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    recall_plan = _candidate_recall_plan(total_quote_count, len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidates = _select_recall_candidates(eligible_quotes, execution_recall_count)
    if not candidates:
        raise MarketDataError("统一行情池没有可用候选股票，已停止运行。")

    candidate_codes = [quote["code"] for quote in candidates]
    latest_quote_trade_date = _latest_trade_date_from_quotes(candidates)
    latest_allowed_bar_date = as_of_date or _latest_allowed_bar_date()
    latest_backfill_count = 0
    if effective_refresh and candidate_codes and latest_quote_trade_date:
        backfill_target_trade_date = _required_bar_date_for_quote({"trade_date": latest_quote_trade_date})
        bar_summaries = _load_cached_bars_summary(db, candidate_codes, 30)
        missing_latest_trade_date_quotes = []
        for quote in candidates:
            code = str(quote.get("code") or "")
            quote_trade_date = _normalize_trade_date(quote.get("trade_date"))
            if not code or quote_trade_date != backfill_target_trade_date:
                continue
            latest_bar_date = _normalize_trade_date(bar_summaries.get(code, {}).get("latest_trade_date"))
            if latest_bar_date < backfill_target_trade_date:
                missing_latest_trade_date_quotes.append(quote)
        if missing_latest_trade_date_quotes:
            latest_backfill_count = _backfill_latest_trade_date_bars(
                db,
                missing_latest_trade_date_quotes,
                settings,
                backfill_target_trade_date,
            )

    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()

    # ── 批量预加载日K（非刷新模式），避免 N 次逐个查询 ──
    if not effective_refresh:
        bars_batch = _load_cached_bars_batch(db, candidate_codes, settings.market_kline_days, as_of_date=as_of_date)
    else:
        bars_batch = None

    for quote in candidates:
        if len(stocks) >= recall_plan["scan_target_count"]:
            break
        if effective_refresh:
            bars, kline_trace, errors = _load_or_refresh_bars(db, quote, settings)
        else:
            cached = bars_batch.get(quote["code"], []) if bars_batch else []
            if len(cached) < 30:
                bars, kline_trace, errors = cached, {"selected_source": "daily_bars_cache", "freshness_status": "missing"}, [
                    f"daily_bars_cache: 本地日K不足30条，仅 {len(cached)} 条"
                ]
            elif not _bars_cover_quote_date(cached, quote):
                bars, kline_trace, errors = cached, {"selected_source": "daily_bars_cache", "freshness_status": "stale_cache"}, [
                    "daily_bars_cache: 本地日K未覆盖报价日期"
                ]
            else:
                bars, kline_trace, errors = cached, {"selected_source": "daily_bars_cache", "freshness_status": "local_pool_readonly"}, []
        if errors:
            kline_errors[quote["code"]] = errors
            continue
        selected_source = str(kline_trace.get("selected_source") or "daily_bars_cache")
        stock = _build_stock_from_bars(
            quote,
            bars,
            quote_source="unified_pool",
            kline_source=selected_source,
        )
        stock["quote_source"] = "unified_pool"
        stock["quote_time"] = quote.get("fetched_at") or stock.get("quote_time")
        stock["data_quality"] = _merge_quality(stock.get("data_quality"), quote, kline_trace)
        stock["source_trace"] = {
            "data_source": "local_unified_market_pool",
            "quote_source": "unified_pool",
            "quote_raw_sources": quote.get("raw_sources", []),
            "quote_source_count": quote.get("source_count", 0),
            "quote_quality_score": quote.get("quality_score", 0),
            "quote_conflicts": quote.get("conflicts", []),
            "quote_cache_status": quote.get("freshness_status", "unknown"),
            "kline_source": selected_source,
            "kline_cache_status": kline_trace.get("freshness_status", "unknown"),
        }
        stocks.append(stock)
        kline_usage[selected_source] += 1

    if not stocks:
        detail = "; ".join(f"{code}: {' | '.join(errors[:2])}" for code, errors in list(kline_errors.items())[:8])
        raise MarketDataError(f"统一行情池无法取得候选日K，已停止运行。{detail}")

    quote_snapshot = _build_quote_snapshot(
        eligible_quotes,
        data_source="local_unified_market_pool",
        quote_source="unified_pool",
        quote_errors=quote_meta.get("errors", []),
    )
    _apply_relative_features(stocks)
    snapshot = _build_snapshot(
        stocks=stocks,
        quote_source="unified_pool",
        quote_errors=quote_meta.get("errors", []),
        kline_sources=_kline_sources(settings),
        kline_usage=kline_usage,
        kline_errors=kline_errors,
        quote_snapshot=quote_snapshot,
    )
    snapshot["breadth"].update(
        {
            "data_source": "local_unified_market_pool",
            "quote_pool_status": quote_meta.get("status"),
            "quote_source_count": quote_meta.get("source_count"),
            "quote_sources": quote_meta.get("sources", []),
            "quote_cache_age_seconds": quote_meta.get("cache_age_seconds"),
            "quote_pool_min_source_count": settings.market_pool_min_source_count,
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
                "broad_recall_count": execution_recall_count,
                "configured_broad_recall_count": recall_plan["broad_recall_count"],
                "broad_recall_limited": execution_recall_count < len(eligible_quotes),
                "recall_strategy": "multi_bucket_quote_recall",
            },
            "latest_quote_trade_date": latest_quote_trade_date,
            "latest_allowed_bar_date": latest_allowed_bar_date,
            "latest_trade_date_backfill_count": latest_backfill_count,
        }
    )
    snapshot["snapshot_id"] = _new_snapshot_id("snapshot_pool", snapshot["trade_date"])
    if quotes_override is not None:
        # 历史回放（研究冻结快照）：剔除 run-owned 非确定性字段，保证同输入可确定性重放
        # （候选快照 fingerprint 必须稳定）。生产 LIVE 路径不经过这里。
        trade_date = str(snapshot.get("trade_date") or "")
        snapshot["snapshot_id"] = f"snapshot_pool_historical_{trade_date}"
        breadth = snapshot.get("breadth")
        if isinstance(breadth, dict):
            breadth["generated_at"] = f"{trade_date}T00:00:00"
    return stocks, snapshot


def _build_realtime_bar(realtime_row: dict[str, Any], prev_close: float, code: str) -> dict[str, Any]:
    """将 realtime_quote 返回的实时行情拼接为一条日K。

    成交量换算成手，与 daily_kbars 历史日K同单位：realtime_quote 的 VOLUME 按股计，
    不换算时当日成交量是历史的 100 倍，量比、放量突破等特征全部失真（L-159）。
    """
    price = _num(_row_value(realtime_row, "PRICE", "price"))
    return {
        "code": code,
        "trade_date": _format_yyyymmdd(_row_value(realtime_row, "DATE", "date")),
        "open": _num(_row_value(realtime_row, "OPEN", "open")),
        "high": _num(_row_value(realtime_row, "HIGH", "high")),
        "low": _num(_row_value(realtime_row, "LOW", "low")),
        "close": price,
        "volume": _num(_row_value(realtime_row, "VOLUME", "volume", "volumn")) / SHARES_PER_LOT,
        "amount": _num(_row_value(realtime_row, "AMOUNT", "amount")),
        "pct_chg": round((price - prev_close) / prev_close * 100, 4) if prev_close > 0 else 0.0,
        "turnover_rate": 0.0,
        "source": "realtime_quote",
    }


def get_market_universe_from_pool_with_realtime(db: Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """盘中实时模式：本地池历史K线 + realtime_quote 今日实时快照拼接。

    流程：
    1. 从本地 stock_quotes 加载缓存行情（含市值/PE/换手率）
    2. 提取候选股票，分批拉取 realtime_quote 实时快照
    3. 合并 quote：实时 price/high/low/volume/amount + 缓存市值/PE
    4. 从 daily_kbars 加载历史K线，拼接今日实时K线
    5. 调用 _build_stock_from_bars 构建完整 Stock 对象
    """
    settings = get_settings()
    quotes, quote_meta = _load_quotes_from_pool(db, settings, skip_metrics=True)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    recall_plan = _candidate_recall_plan(total_quote_count, len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidates = _select_recall_candidates(eligible_quotes, execution_recall_count)
    if not candidates:
        raise MarketDataError("统一行情池没有可用候选股票，已停止运行。")

    candidate_codes = [quote["code"] for quote in candidates]
    ts_codes = [f"{code}.{_exchange_from_code(code)}" for code in candidate_codes]

    # 分批拉取 realtime_quote（sina 源单次最多 50 只）
    realtime_map: dict[str, dict[str, Any]] = {}
    chunk_size = 50
    for start in range(0, len(ts_codes), chunk_size):
        chunk = ts_codes[start:start + chunk_size]
        try:
            frame = _fetch_tushare_realtime_frame(chunk, settings)
            if frame is None or frame.empty:
                continue
            for _, row in frame.iterrows():
                item = dict(row)
                code = _normalize_code(str(_row_value(item, "TS_CODE", "ts_code", "code") or "").split(".")[0])
                if code:
                    realtime_map[code] = item
        except Exception as exc:
            logger.warning("realtime_quote 批次 %d-%d 失败: %s", start, start + chunk_size, exc)

    if not realtime_map:
        raise MarketDataError("盘中实时行情拉取失败，realtime_quote 未返回任何数据。")

    # 合并 quote：实时 price/high/low/volume/amount + 缓存市值/PE
    merged_quotes: list[dict[str, Any]] = []
    for quote in candidates:
        code = quote["code"]
        rt = realtime_map.get(code)
        if not rt:
            continue
        price = _num(_row_value(rt, "PRICE", "price"))
        if price <= 0:
            continue
        preclose = _num(_row_value(rt, "PRE_CLOSE", "pre_close", "preclose")) or _num(quote.get("preclose"))
        merged = dict(quote)
        merged["price"] = price
        merged["pct"] = round((price - preclose) / preclose * 100, 4) if preclose > 0 else 0.0
        merged["high"] = _num(_row_value(rt, "HIGH", "high"))
        merged["low"] = _num(_row_value(rt, "LOW", "low"))
        merged["open"] = _num(_row_value(rt, "OPEN", "open"))
        merged["preclose"] = preclose
        merged["volume"] = _num(_row_value(rt, "VOLUME", "volume", "volumn"))
        merged["amount_yi"] = _num(_row_value(rt, "AMOUNT", "amount")) / 100000000
        free_mv_yi = _num(quote.get("free_mv_yi"))
        if free_mv_yi > 0 and merged["amount_yi"] > 0:
            merged["turnover_rate"] = round(merged["amount_yi"] / free_mv_yi * 100, 4)
        merged["trade_date"] = _format_yyyymmdd(_row_value(rt, "DATE", "date"))
        merged["fetched_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        merged_quotes.append(merged)

    if not merged_quotes:
        raise MarketDataError("盘中实时行情合并失败，无有效候选。")

    # 批量预加载历史K线
    stocks: list[dict[str, Any]] = []
    kline_errors: dict[str, list[str]] = {}
    kline_usage: Counter[str] = Counter()
    bars_batch = _load_cached_bars_batch(db, candidate_codes, settings.market_kline_days)

    for quote in merged_quotes:
        if len(stocks) >= recall_plan["scan_target_count"]:
            break
        code = quote["code"]
        bars = list(bars_batch.get(code, []))
        if len(bars) < 30:
            kline_errors[code] = [f"daily_bars_cache: 本地日K不足30条，仅 {len(bars)} 条"]
            continue

        # 拼接今日实时K线。实时K线与最后一条历史K线同日（收盘后或次日开盘前运行）时
        # 替换掉那一条，昨收必须取被替换那条之前的收盘价：直接取 bars[-1] 会拿当日自身
        # 收盘价当昨收，涨跌幅恒为 0（L-159）。实时行情自带昨收时优先用它。
        rt_row = realtime_map[code]
        rt_trade_date = _format_yyyymmdd(_row_value(rt_row, "DATE", "date"))
        if bars and bars[-1]["trade_date"] == rt_trade_date:
            bars = bars[:-1]
        prev_close = (
            _num(_row_value(rt_row, "PRE_CLOSE", "pre_close", "preclose"))
            or (bars[-1]["close"] if bars else 0.0)
            or _num(quote.get("preclose"))
        )
        rt_bar = _build_realtime_bar(rt_row, prev_close, code)
        bars.append(rt_bar)

        selected_source = "daily_bars_cache+realtime"
        stock = _build_stock_from_bars(
            quote,
            bars,
            quote_source="unified_pool_realtime",
            kline_source=selected_source,
        )
        stock["quote_source"] = "unified_pool_realtime"
        stock["quote_time"] = quote.get("fetched_at") or stock.get("quote_time")
        stock["data_quality"] = _merge_quality(stock.get("data_quality"), quote, {"selected_source": selected_source, "freshness_status": "realtime"})
        stock["source_trace"] = {
            "data_source": "local_pool_realtime",
            "quote_source": "unified_pool_realtime",
            "quote_raw_sources": ["tushare_realtime"],
            "quote_source_count": 1,
            "quote_quality_score": quote.get("quality_score", 0),
            "quote_conflicts": [],
            "quote_cache_status": "realtime",
            "kline_source": selected_source,
            "kline_cache_status": "local_pool+realtime_bar",
        }
        stocks.append(stock)
        kline_usage[selected_source] += 1

    if not stocks:
        detail = "; ".join(f"{code}: {' | '.join(errors[:2])}" for code, errors in list(kline_errors.items())[:8])
        raise MarketDataError(f"盘中实时行情池无法取得候选日K，已停止运行。{detail}")

    quote_snapshot = _build_quote_snapshot(
        merged_quotes,
        data_source="local_pool_realtime",
        quote_source="unified_pool_realtime",
        quote_errors=quote_meta.get("errors", []),
    )
    _apply_relative_features(stocks)
    snapshot = _build_snapshot(
        stocks=stocks,
        quote_source="unified_pool_realtime",
        quote_errors=quote_meta.get("errors", []),
        kline_sources=_kline_sources(settings),
        kline_usage=kline_usage,
        kline_errors=kline_errors,
        quote_snapshot=quote_snapshot,
    )
    snapshot["breadth"].update(
        {
            "data_source": "local_pool_realtime",
            "quote_pool_status": quote_meta.get("status"),
            "realtime_quote_count": len(realtime_map),
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
                "broad_recall_count": execution_recall_count,
                "configured_broad_recall_count": recall_plan["broad_recall_count"],
                "broad_recall_limited": execution_recall_count < len(eligible_quotes),
                "recall_strategy": "multi_bucket_quote_recall",
            },
        }
    )
    snapshot["snapshot_id"] = _new_snapshot_id("snapshot_pool_rt", snapshot["trade_date"])
    return stocks, snapshot


def get_market_snapshot_from_pool(db: Session, refresh: bool = False) -> dict[str, Any]:
    settings = get_settings()
    quotes, quote_meta = _load_or_refresh_quotes(db, settings) if refresh else _load_quotes_from_pool(db, settings, skip_metrics=True)
    total_quote_count = len(quotes)
    eligible_quotes = _eligible_market_quotes(quotes)
    filter_summary = _candidate_filter_summary(quotes)
    if not eligible_quotes:
        raise MarketDataError("统一行情池没有可用市场快照。")
    snapshot = _build_quote_snapshot(
        eligible_quotes,
        data_source="local_unified_market_pool",
        quote_source="unified_pool",
        quote_errors=quote_meta.get("errors", []),
    )
    snapshot["breadth"].update(
        {
            "quote_pool_status": quote_meta.get("status"),
            "quote_source_count": quote_meta.get("source_count"),
            "quote_sources": quote_meta.get("sources", []),
            "quote_cache_age_seconds": quote_meta.get("cache_age_seconds"),
            "unified_quote_total": total_quote_count,
            "base_filter_eligible_count": len(eligible_quotes),
            "candidate_filter": filter_summary,
            "actual_scan_count": len(eligible_quotes),
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
    )
    snapshot["snapshot_id"] = f"snapshot_pool_{snapshot['trade_date']}"
    return snapshot


def refresh_quote_pool(db: Session) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _refresh_quote_pool(db, get_settings())


def local_pool_recommendation_readiness(
    db: Session,
    cached_quotes: list[dict[str, Any]] | None = None,
    cached_bar_summaries: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    configured_full_market_mode = int(getattr(settings, "market_candidate_count", 0) or 0) <= 0
    min_bar_count = 30
    messages: list[str] = []
    examples: list[dict[str, Any]] = []

    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    quotes = (
        cached_quotes
        if cached_quotes is not None
        else _load_cached_quotes(
            db,
            allowed_sources=set(active_sources) if active_sources else None,
            skip_metrics=True,
        )
    )
    using_cache_without_active_sources = not active_sources and bool(quotes)
    if not active_sources and not quotes:
        return {
            "status": "blocked",
            "can_run_local_recommendation": False,
            "has_missing_data": True,
            "full_market_mode": configured_full_market_mode,
            "required_candidate_count": 0,
            "quote": {
                "available_count": 0,
                "candidate_count": 0,
                "latest_trade_date": None,
                "active_sources": [],
            },
            "kline": {
                "checked_count": 0,
                "ready_count": 0,
                "missing_count": 0,
                "stale_count": 0,
                "min_bars": min_bar_count,
                "examples": [],
            },
            "messages": ["所有报价数据源都已关闭，本地池无法确认可用报价。"],
        }

    quote_trade_dates = [str(item.get("trade_date") or "") for item in quotes if item.get("trade_date")]
    eligible_quotes = _eligible_market_quotes(quotes) if quotes else []
    filter_summary = _candidate_filter_summary(quotes) if quotes else {
        "total_count": 0,
        "base_eligible_count": 0,
        "excluded_count": 0,
        "high_liquidity_count": 0,
        "low_liquidity_count": 0,
        "exclusion_counts": {},
        "high_liquidity_threshold": {"amount_yi": 0.5, "turnover_rate": 0.1},
    }
    recall_plan = _candidate_recall_plan(len(quotes), len(eligible_quotes), settings)
    execution_recall_count = _execution_recall_count(recall_plan, len(eligible_quotes))
    candidates = _select_recall_candidates(eligible_quotes, execution_recall_count)
    effective_required_count = min(len(candidates), recall_plan["scan_target_count"])

    # Bar summaries：优先用传入缓存，避免重复 GROUP BY 查询
    candidate_codes = [str(item.get("code") or "") for item in candidates]
    if cached_bar_summaries is not None:
        bar_summaries = {code: cached_bar_summaries[code] for code in candidate_codes if code in cached_bar_summaries}
    else:
        bar_summaries = _load_cached_bars_summary(db, candidate_codes, min_bar_count) if candidate_codes else {}

    ready_count = 0
    missing_count = 0
    short_history_count = 0
    immature_history_count = 0
    stale_count = 0
    checked_count = 0
    maturity_map = _load_history_maturity_map(db, candidate_codes) if candidate_codes else {}
    for quote in candidates:
        checked_count += 1
        code = str(quote.get("code") or "")
        summary = bar_summaries.get(code, {})
        bar_count = int(summary.get("bar_count") or 0)
        latest_bar_date = _normalize_trade_date(summary.get("latest_trade_date"))
        quote_date = _normalize_trade_date(quote.get("trade_date") or date.today().isoformat())
        required_bar_date = _required_bar_date_for_quote(quote)
        natural_short_history = False
        if bar_count < min_bar_count:
            if bar_count <= 0:
                missing_count += 1
            else:
                short_history_count += 1
                maturity = maturity_map.get(code, {})
                natural_short_history = _is_natural_short_history(
                    bar_count=bar_count,
                    latest_bar_date=latest_bar_date,
                    quote_date=required_bar_date,
                    first_bar_date=_normalize_trade_date(summary.get("first_trade_date") or maturity.get("first_bar_date")),
                    list_date=_normalize_trade_date(summary.get("list_date") or maturity.get("list_date")),
                )
                if natural_short_history:
                    immature_history_count += 1
            if len(examples) < 8:
                examples.append(
                    _readiness_example(
                        quote,
                        "上市未满30根日K" if natural_short_history else "本地日K不足30条",
                        bar_count,
                        latest_bar_date,
                        quote_date,
                        required_bar_date,
                    )
                )
            continue
        if not latest_bar_date or latest_bar_date < required_bar_date:
            stale_count += 1
            if len(examples) < 8:
                examples.append(
                    _readiness_example(
                        quote,
                        "本地日K未覆盖报价日期",
                        bar_count,
                        latest_bar_date,
                        quote_date,
                        required_bar_date,
                    )
                )
            continue
        ready_count += 1

    can_run = (
        len(quotes) > 0 if recall_plan["full_market_mode"] else len(quotes) >= recall_plan["scan_target_count"]
    ) and ready_count > 0
    has_missing_data = (
        not candidates
        or ready_count < effective_required_count
        or (
            not recall_plan["full_market_mode"]
            and (
                len(quotes) < recall_plan["scan_target_count"]
                or len(candidates) < recall_plan["scan_target_count"]
            )
        )
    )
    if recall_plan["full_market_mode"] and not quotes:
        status = "blocked"
        messages.append("本地统一报价为空，全市场模式无法运行推荐。")
    elif not recall_plan["full_market_mode"] and len(quotes) < recall_plan["scan_target_count"]:
        status = "blocked"
        messages.append(
            f"本地统一报价不足，当前 {len(quotes)} 条，至少需要 {recall_plan['scan_target_count']} 条。"
        )
    elif not candidates:
        status = "blocked"
        messages.append("本地报价存在，但没有股票通过候选过滤条件。")
    elif ready_count == 0:
        status = "blocked"
        messages.append("候选股的本地日K全部缺失或未覆盖报价日期。")
    elif ready_count < effective_required_count:
        status = "warning"
        if recall_plan["full_market_mode"]:
            messages.append(
                f"全市场模式可运行本地推荐，将扫描 {len(candidates)} 只候选；"
                f"当前日K覆盖完整 {ready_count} 只，目标扫描 {recall_plan['scan_target_count']} 只。"
            )
        else:
            messages.append(
                f"可运行本地推荐，但宽召回 {len(candidates)} 只里只有 {ready_count} 只日K覆盖完整，"
                f"目标扫描数为 {recall_plan['scan_target_count']}。"
            )
    else:
        status = "ready"
        if recall_plan["full_market_mode"]:
            messages.append(f"全市场模式可直接运行推荐，{ready_count} / {len(candidates)} 只候选股报价与日K覆盖完整。")
        else:
            messages.append(
                f"本地池可直接运行推荐，宽召回 {len(candidates)} 只中已有 {ready_count} 只候选股报价与日K覆盖完整。"
            )
    if using_cache_without_active_sources:
        messages.append("报价源开关当前全部关闭，本地池将仅基于已落库缓存运行；如需刷新行情请重新启用报价源。")

    if missing_count or stale_count:
        gap_parts = []
        if missing_count:
            gap_parts.append(f"日K缺失 {missing_count} 只")
        remediable_short_history = max(0, short_history_count - immature_history_count)
        if remediable_short_history:
            gap_parts.append(f"短历史 {remediable_short_history} 只")
        if stale_count:
            gap_parts.append(f"日K过期 {stale_count} 只")
        messages.append(f"当前存在数据缺口：{'，'.join(gap_parts)}。")
    elif short_history_count > immature_history_count:
        messages.append(f"当前存在数据缺口：短历史 {short_history_count - immature_history_count} 只。")
    if immature_history_count:
        messages.append(f"另有 {immature_history_count} 只候选属于上市未满30根日K，按成熟度风险处理。")

    return {
        "status": status,
        "can_run_local_recommendation": can_run,
        "has_missing_data": has_missing_data,
        "full_market_mode": recall_plan["full_market_mode"],
        "required_candidate_count": effective_required_count,
        "quote": {
            "available_count": len(quotes),
            "candidate_count": len(candidates),
            "base_filter_eligible_count": len(eligible_quotes),
            "unified_quote_total": len(quotes),
            "filter_summary": filter_summary,
            "latest_trade_date": max(quote_trade_dates) if quote_trade_dates else None,
            "active_sources": active_sources,
            "using_cache_without_active_sources": using_cache_without_active_sources,
        },
        "kline": {
            "checked_count": checked_count,
            "ready_count": ready_count,
            "missing_count": missing_count,
            "short_history_count": short_history_count,
            "immature_history_count": immature_history_count,
            "stale_count": stale_count,
            "min_bars": min_bar_count,
            "coverage_count": ready_count,
            "coverage_rate": round(ready_count / max(1, len(candidates)), 4) if candidates else 0.0,
            "latest_trade_date": max(
                [str(summary.get("latest_trade_date") or "") for summary in bar_summaries.values() if summary.get("latest_trade_date")],
                default=None,
            ),
            "example_count": len(examples),
            "example_limit": 8,
            "examples": examples,
        },
        "scan": {
            "configured_candidate_count": recall_plan["explicit_scan_limit"],
            "lookahead_multiplier": recall_plan["lookahead_multiplier"],
            "lookahead_count": recall_plan["broad_recall_count"],
            "data_coverage_lookahead_count": execution_recall_count,
            "actual_scan_count": min(ready_count, recall_plan["scan_target_count"]),
            "skill_input_count": min(ready_count, recall_plan["scan_target_count"]),
            "agent_review_input_count": min(ready_count, recall_plan["scan_target_count"]),
            "scan_limited": execution_recall_count < len(eligible_quotes) or (
                not recall_plan["full_market_mode"] and len(candidates) > recall_plan["scan_target_count"]
            ),
            "demo_limited": (not recall_plan["full_market_mode"]) and len(eligible_quotes) > recall_plan["scan_target_count"],
        },
        "messages": messages,
    }


def _execution_recall_count(recall_plan: dict[str, Any], eligible_count: int) -> int:
    broad_count = max(0, int(recall_plan.get("broad_recall_count") or 0))
    if not bool(recall_plan.get("full_market_mode")) or not bool(recall_plan.get("broad_recall_limited")):
        return min(eligible_count, broad_count)
    target_count = max(1, int(recall_plan.get("scan_target_count") or broad_count or 1))
    return min(eligible_count, max(broad_count, target_count * 3))


def _load_quotes_from_pool(db: Session, settings: Any, skip_metrics: bool = False, as_of_date: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    active_sources = enabled_sources(db, "quote", _quote_sources(settings))
    quotes = _load_cached_quotes(
        db,
        allowed_sources=set(active_sources) if active_sources else None,
        skip_metrics=skip_metrics,
        as_of_date=as_of_date,
    )
    if not active_sources and not quotes:
        raise MarketDataError("所有报价数据源都已关闭，且本地数据池没有可用缓存。请至少启用一个报价源。")
    required_count = max(0, int(settings.market_candidate_count))
    if required_count > 0 and len(quotes) < required_count:
        raise MarketDataError(
            f"本地数据池可用报价不足，当前 {len(quotes)} 条，至少需要 {required_count} 条。"
            "请先显式刷新本地数据池，或切换为实时数据。"
        )
    status = "local_pool_readonly" if active_sources else "local_pool_readonly_source_disabled"
    errors = [] if active_sources else ["所有报价数据源关闭，当前仅使用本地缓存。"]
    return quotes, _quote_meta(status, quotes, errors=errors)


def _load_or_refresh_quotes(db: Session, settings: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configured_sources = _quote_sources(settings)
    active_sources = enabled_sources(db, "quote", configured_sources)
    if not active_sources:
        cached = _load_cached_quotes(db)
        if cached:
            return cached, _quote_meta(
                "local_pool_readonly_source_disabled",
                cached,
                errors=["所有报价数据源关闭，无法刷新行情；当前仅使用本地缓存。"],
            )
        raise MarketDataError("所有报价数据源都已关闭，且本地数据池没有可用缓存。请至少启用一个报价源。")
    allowed_sources = set(active_sources)
    cached = _load_cached_quotes(
        db,
        max_age=timedelta(seconds=settings.market_quote_cache_ttl_seconds),
        allowed_sources=allowed_sources,
    )
    required_count = max(0, int(settings.market_candidate_count))
    if (
        len(cached) > 0
        and (required_count == 0 or len(cached) >= required_count)
        and _cached_quotes_cover_latest_quote_date(cached)
    ):
        return cached, _quote_meta("fresh_cache", cached)
    return _refresh_quote_pool(db, settings, active_sources=active_sources)


def _cached_quotes_cover_latest_quote_date(quotes: list[dict[str, Any]]) -> bool:
    latest_quote_date = _latest_allowed_quote_date()
    latest_cached_date = _latest_trade_date_from_quotes(quotes)
    return bool(latest_cached_date and latest_cached_date >= latest_quote_date)


def _batch_error_detail(errors: tuple[MarketSourceError, ...], fallback: str) -> str:
    """Render typed adapter errors into the pool's persisted run evidence."""

    messages = [error.message for error in errors if error.message]
    return " | ".join(messages) if messages else fallback


def _refresh_quote_pool(
    db: Session,
    settings: Any,
    active_sources: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    errors: list[str] = []
    sources = active_sources if active_sources is not None else enabled_sources(db, "quote", _quote_sources(settings))
    if not sources:
        raise MarketDataError("所有报价数据源都已关闭，请至少启用一个报价源。")
    for source in sources:
        start = time.perf_counter()
        try:
            batch = _fetch_quote_batch(source, settings, QuoteRequest())
            latency_ms = (time.perf_counter() - start) * 1000
            if batch.audit.status is BatchStatus.FAILURE:
                detail = _batch_error_detail(batch.errors, "quote retrieval failed")
                _record_source_health(db, source, "quote", "failure", error=detail, latency_ms=latency_ms)
                errors.append(f"{source}: {detail}")
                continue

            rows = batch.as_legacy_rows()
            if batch.audit.status is BatchStatus.EMPTY or not rows:
                _record_source_health(db, source, "quote", "empty", latency_ms=latency_ms)
                errors.append(f"{source}: empty result")
                continue
            if batch.audit.status is BatchStatus.PARTIAL:
                errors.append(
                    f"{source}: partial result: "
                    f"{_batch_error_detail(batch.errors, 'incomplete quote batch')}"
                )
            _record_source_health(db, source, "quote", "success", latency_ms=latency_ms)
            for row in rows:
                code = str(row.get("code") or "")
                if code:
                    grouped[code].append(row)
        except Exception as exc:  # noqa: BLE001 - unexpected adapter boundary failure is run evidence
            latency_ms = (time.perf_counter() - start) * 1000
            _record_source_health(db, source, "quote", "failure", error=str(exc), latency_ms=latency_ms)
            errors.append(f"{source}: {exc}")

    if not grouped:
        db.commit()
        # 所有报价源都失败时，用旧缓存兜底（T12：preclose_overnight 因 Tushare 超时失败，
        # formal run 跑不通 = 系统对用户等于没在跑。旧缓存虽非最新但有数据，比 0 推荐好）
        try:
            cached = _load_cached_quotes(db, skip_metrics=True)
            if cached:
                logger.warning(
                    "行情池刷新全失败，用旧缓存兜底（%d 条）。错误: %s",
                    len(cached),
                    "；".join(errors),
                )
                return cached, _quote_meta("stale_cache_fallback", cached, errors=errors)
        except Exception:
            pass
        raise MarketDataError("统一行情池刷新失败，所有报价源不可用。" + "；".join(errors))

    merged = [_merge_quote_rows(code, rows, sources, settings) for code, rows in grouped.items()]
    _enrich_industries(settings, merged)
    _persist_stock_quotes(db, merged)
    db.commit()
    return merged, _quote_meta("refreshed", merged, errors=errors)


def _load_cached_quotes(
    db: Session,
    max_age: timedelta | None = None,
    allowed_sources: set[str] | None = None,
    skip_metrics: bool = False,
    as_of_date: str | None = None,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - max_age if max_age is not None else None
    latest_allowed_date = as_of_date or _latest_allowed_quote_date()
    base_cond = [StockQuote.trade_date <= latest_allowed_date]
    if as_of_date:
        # 历史日期模式：inactive 只是表示"不再是最新行情"，历史数据仍然有效
        base_cond.append(StockQuote.trade_date == as_of_date)
    else:
        base_cond.append(StockQuote.freshness_status != "inactive")
    stmt = (
        select(StockQuote)
        .where(*base_cond)
        .order_by(StockQuote.quality_score.desc(), StockQuote.snapped_at.desc())
    )
    if cutoff is not None and as_of_date is None:
        # max_age 仅对实时刷新有意义，历史日期覆盖时跳过时效过滤
        stmt = stmt.where(StockQuote.snapped_at >= cutoff)
    rows = _dedupe_latest_quote_rows(db.scalars(stmt).all())
    local_metrics = {} if skip_metrics else _quote_metrics_from_bars(db, [row.code for row in rows], as_of_date=as_of_date)
    quotes = []
    for row in rows:
        quote = {
            "code": row.code,
            "name": row.name,
            "trade_date": row.trade_date,
            "price": float(row.price or 0),
            "pct": float(row.pct or 0),
            "volume": float(row.volume or 0),
            "amount_yi": float(row.amount_yi or 0),
            "turnover_rate": float(row.turnover_rate or 0),
            "pe_ttm": float(row.pe_ttm or 0) if row.pe_ttm else None,
            "total_mv_yi": float(row.total_mv_yi or 0) if row.total_mv_yi else None,
            "free_mv_yi": float(row.free_mv_yi or 0) if row.free_mv_yi else None,
            "chg60": float(row.chg60 or 0) if row.chg60 else None,
            "high": float(row.high or 0) if row.high else None,
            "low": float(row.low or 0) if row.low else None,
            "open": float(row.open or 0) if row.open else None,
            "preclose": float(row.preclose or 0) if row.preclose else None,
            "industry": row.industry or "",
            "source_count": row.source_count,
            "quality_score": row.quality_score,
            "freshness_status": row.freshness_status if cutoff is None or (row.snapped_at and row.snapped_at >= cutoff) else "stale_cache",
            "fetched_at": row.snapped_at.isoformat() if row.snapped_at else "",
            "raw_sources": [row.source] if row.source else [],
            "conflicts": [],
            "exchange": "SH" if (row.code or "").startswith(("6", "9")) else "SZ",
        }
        if not skip_metrics:
            _apply_local_quote_metrics(quote, local_metrics.get(row.code, {}))
        if allowed_sources is not None and not set(quote["raw_sources"]).issubset(allowed_sources):
            continue
        quotes.append(quote)
    return quotes


def _dedupe_latest_quote_rows(rows: list[Any]) -> list[Any]:
    latest_by_code: dict[str, Any] = {}
    for row in rows:
        code = str(getattr(row, "code", "") or "")
        if not code:
            continue
        current = latest_by_code.get(code)
        if current is None or _quote_row_recency_key(row) > _quote_row_recency_key(current):
            latest_by_code[code] = row
    return sorted(
        latest_by_code.values(),
        key=lambda row: (
            float(getattr(row, "quality_score", 0) or 0),
            str(getattr(row, "snapped_at", "") or ""),
            _normalize_trade_date(getattr(row, "trade_date", None)),
        ),
        reverse=True,
    )


def _quote_row_recency_key(row: Any) -> tuple[str, str, float]:
    return (
        _normalize_trade_date(getattr(row, "trade_date", None)),
        str(getattr(row, "snapped_at", "") or ""),
        float(getattr(row, "quality_score", 0) or 0),
    )


def enrich_quote_with_local_metrics(db: Session, code: str, quote: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(quote)
    _apply_local_quote_metrics(enriched, _quote_metrics_from_bars(db, [code]).get(code, {}))
    return enriched


def _quote_metrics_from_bars(db: Session, codes: list[str], as_of_date: str | None = None) -> dict[str, dict[str, float]]:
    unique_codes = sorted({str(code) for code in codes if code})
    if not unique_codes:
        return {}

    latest_allowed_date = as_of_date or _latest_allowed_bar_date()
    year_start = date.fromisoformat(latest_allowed_date).replace(month=1, day=1).isoformat()
    # 只查询需要的列 + 限制日期范围（避免加载全年数据，只需最近 ~150 交易日）
    cutoff_date = (date.fromisoformat(latest_allowed_date) - timedelta(days=220)).isoformat()
    stmt = (
        select(
            DailyKbar.code,
            DailyKbar.trade_date,
            DailyKbar.close,
            DailyKbar.ma5,
            DailyKbar.ma10,
            DailyKbar.ma20,
        )
        .where(
            DailyKbar.code.in_(unique_codes),
            DailyKbar.trade_date >= cutoff_date,
            DailyKbar.trade_date <= latest_allowed_date,
        )
        .order_by(DailyKbar.code, DailyKbar.trade_date.desc())
    )
    rows = db.execute(stmt).all()

    bars_by_code: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        bars_by_code[str(row[0])].append(row)

    result: dict[str, dict[str, float]] = {}
    for code, bars in bars_by_code.items():
        if not bars:
            continue
        latest_close = float(bars[0][2] or 0)
        if latest_close <= 0:
            continue

        latest_bar = bars[0]
        values: dict[str, float] = {}
        for idx, field in enumerate(("ma5", "ma10", "ma20"), start=3):
            value = latest_bar[idx]
            if value is not None:
                values[field] = round(float(value), 3)

        if len(bars) >= 60:
            base_close = float(bars[59][2] or 0)
            if base_close:
                values["chg60"] = round((latest_close - base_close) / base_close * 100, 4)

        ytd_base = next((bar for bar in bars if str(bar[1]) < year_start), None)
        if ytd_base is not None:
            base_close = float(ytd_base[2] or 0)
            if base_close:
                values["ytd_chg"] = round((latest_close - base_close) / base_close * 100, 4)

        if values:
            result[code] = values
    return result


def _apply_local_quote_metrics(quote: dict[str, Any], metrics: dict[str, float]) -> None:
    source_trace = quote.setdefault("source_trace", {})
    if isinstance(source_trace, dict):
        field_sources = source_trace.setdefault("field_sources", {})
    else:
        field_sources = {}
    for field in LOCAL_DAILY_BAR_FIELDS:
        value = metrics.get(field)
        has_source_value = isinstance(field_sources, dict) and bool(field_sources.get(field))
        if value is None:
            if quote.get(field) in {0, 0.0} and not has_source_value:
                quote[field] = None
            continue
        if quote.get(field) in {None, "", "-", 0, 0.0}:
            quote[field] = value
            if isinstance(field_sources, dict):
                field_sources[field] = ["daily_bars_cache"]


def _load_or_refresh_bars(
    db: Session,
    quote: dict[str, Any],
    settings: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    cached = _load_cached_bars(db, quote["code"], settings.market_kline_days)
    if len(cached) >= 30 and _bars_cover_quote_date(cached, quote):
        return cached, {"selected_source": "daily_bars_cache", "freshness_status": "fresh_cache"}, []
    bars, source, errors = _refresh_bars_for_quote(db, quote, settings)
    if bars:
        _persist_kbars(db, bars)
        db.commit()
        merged = _load_cached_bars(db, quote["code"], settings.market_kline_days)
        if len(merged) >= 30 and _bars_cover_quote_date(merged, quote):
            return merged, {"selected_source": source, "freshness_status": "refreshed"}, errors
        if len(merged) < 30:
            errors.append(f"{source}: 合并后日K不足30条，仅 {len(merged)} 条")
        elif not _bars_cover_quote_date(merged, quote):
            errors.append(f"{source}: 合并后日K未覆盖报价日期")
    return [], {"selected_source": source or "", "freshness_status": "missing"}, errors


def _load_bars_from_pool(
    db: Session,
    quote: dict[str, Any],
    settings: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    cached = _load_cached_bars(db, quote["code"], settings.market_kline_days)
    if len(cached) < 30:
        return cached, {"selected_source": "daily_bars_cache", "freshness_status": "missing"}, [
            f"daily_bars_cache: 本地日K不足30条，仅 {len(cached)} 条"
        ]
    if not _bars_cover_quote_date(cached, quote):
        return cached, {"selected_source": "daily_bars_cache", "freshness_status": "stale_cache"}, [
            "daily_bars_cache: 本地日K未覆盖报价日期"
        ]
    return cached, {"selected_source": "daily_bars_cache", "freshness_status": "local_pool_readonly"}, []


def _refresh_bars_for_quote(
    db: Session,
    quote: dict[str, Any],
    settings: Any,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    errors: list[str] = []
    sources = enabled_sources(db, "kline", _kline_sources(settings))
    if not sources:
        return [], "", ["kline: 所有日K数据源都已关闭"]

    # 增量拉取：查本地已存数据的最新日期，只拉缺失的天数
    start_date = _incremental_kline_start(db, quote["code"], settings)
    incremental_mode = start_date is not None
    reference = QuoteRef.from_row(quote)

    for source in sources:
        start = time.perf_counter()
        try:
            batch = _fetch_daily_bar_batch(
                source,
                settings,
                DailyBarRequest((reference,), start_date=start_date),
            )
            latency_ms = (time.perf_counter() - start) * 1000
            if batch.audit.status is BatchStatus.FAILURE:
                detail = _batch_error_detail(batch.errors, "daily-bar retrieval failed")
                errors.append(f"{source}: {detail}")
                _record_source_health(db, source, "kline", "failure", error=detail, latency_ms=latency_ms)
                continue

            bars = [bar.as_legacy_row() for bar in batch.bars_by_instrument.get(reference, ())]
            if bars:
                # The typed request intentionally carries only stable identity.
                # Preserve the provider's former quote-row fallback at the
                # boundary where that richer quote context still exists.
                bars[-1]["turnover_rate"] = (
                    _num(bars[-1].get("turnover_rate"))
                    or _num(quote.get("turnover_rate"))
                )
            if batch.audit.status is BatchStatus.PARTIAL:
                errors.append(
                    f"{source}: partial result: "
                    f"{_batch_error_detail(batch.errors, 'incomplete daily-bar batch')}"
                )
            if len(bars) < 30 and not incremental_mode:
                _record_source_health(db, source, "kline", "empty", latency_ms=latency_ms)
                errors.append(f"{source}: 日K不足30条")
                continue
            _record_source_health(db, source, "kline", "success", latency_ms=latency_ms)
            return bars[-settings.market_kline_days :] if not incremental_mode else bars, source, errors
        except Exception as exc:  # noqa: BLE001 - unexpected adapter boundary failure is run evidence
            latency_ms = (time.perf_counter() - start) * 1000
            message = f"{source}: {exc}"
            errors.append(message)
            _record_source_health(db, source, "kline", "failure", error=str(exc), latency_ms=latency_ms)
    return [], "", errors


def _incremental_kline_start(db: Session, code: str, settings: Any) -> str | None:
    """查询本地已有日K的最新交易日，返回增量拉取的起始日期 (YYYYMMDD)。

    已有数据时，从最新日期 - buffer 天开始拉取（覆盖可能的复权修正）。
    无数据时返回 None（通知调用方按全量拉取）。
    """
    existing_count, existing_max_date = db.execute(
        select(func.count(), func.max(DailyKbar.trade_date)).where(DailyKbar.code == code)
    ).one()
    if int(existing_count or 0) < 30:
        return None
    if not existing_max_date:
        return None
    buffer_days = getattr(settings, "market_kline_incremental_buffer", 10)
    try:
        latest = datetime.strptime(_normalize_trade_date(existing_max_date), "%Y-%m-%d").date()
        start = latest - timedelta(days=buffer_days)
        return start.strftime("%Y%m%d")
    except ValueError:
        return None


def _readiness_example(
    quote: dict[str, Any],
    reason: str,
    bar_count: int,
    latest_bar_date: str | None,
    quote_date: str,
    required_bar_date: str | None = None,
) -> dict[str, Any]:
    return {
        "code": quote.get("code"),
        "name": quote.get("name"),
        "reason": reason,
        "bar_count": bar_count,
        "latest_bar_date": latest_bar_date,
        "quote_date": quote_date,
        "required_bar_date": required_bar_date or quote_date,
    }


def _backfill_latest_trade_date_bars(
    db: Session,
    quotes: list[dict[str, Any]],
    settings: Any,
    target_trade_date: str,
) -> int:
    from app.services.indicator_computer import compute_daily_bar_indicators, compute_daily_indicators

    target_iso = _normalize_trade_date(target_trade_date)
    if not target_iso or not quotes:
        return 0

    quote_bars_by_code: dict[str, dict[str, Any]] = {}
    for item in quotes:
        code = str(item.get("code") or "")
        quote_trade_date = _normalize_trade_date(item.get("trade_date"))
        price = _num(item.get("price"))
        if not code or quote_trade_date != target_iso or price <= 0:
            continue

        open_price = _num(item.get("open"))
        if open_price <= 0:
            open_price = price
        high_price = _num(item.get("high"))
        if high_price <= 0:
            high_price = max(open_price, price)
        low_price = _num(item.get("low"))
        if low_price <= 0:
            low_price = min(open_price, price)

        quote_bars_by_code[code] = {
            "code": code,
            "trade_date": target_iso,
            "open": max(open_price, 0.0),
            "high": max(high_price, open_price, price),
            "low": min(low_price, open_price, price),
            "close": price,
            "volume": _num(item.get("volume")),
            "amount": _num(item.get("amount_yi")) * 100000000,
            # 快照未带回这两个字段时写 None，由写入策略让它们退出 update，
            # 保住库中已有真值（L-146）。
            "pct_chg": normalize_quote_metric(item.get("pct")),
            "turnover_rate": normalize_quote_metric(item.get("turnover_rate")),
            "source": "tushare",
        }

    codes = sorted(quote_bars_by_code)
    if not codes:
        return 0

    historical = db.scalars(
        select(DailyKbar)
        .where(
            DailyKbar.code.in_(codes),
            DailyKbar.trade_date < target_iso,
        )
        .order_by(DailyKbar.code, DailyKbar.trade_date.asc())
    ).all()

    hist_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in historical:
        hist_by_code[row.code].append(
            {
                "code": row.code,
                "trade_date": _normalize_trade_date(row.trade_date),
                "open": float(row.open or 0),
                "high": float(row.high or 0),
                "low": float(row.low or 0),
                "close": float(row.close or 0),
                "volume": float(row.volume or 0),
                "amount": float(row.amount or 0),
                "pct_chg": float(row.pct_chg or 0),
                "turnover_rate": float(row.turnover_rate or 0),
            }
        )

    enriched_bars: list[dict[str, Any]] = []
    for code in codes:
        bar = quote_bars_by_code[code]
        code = bar["code"]
        combined = list(hist_by_code.get(code, []))
        combined.append(dict(bar))
        bar_indicators = compute_daily_bar_indicators(code, combined)
        daily_indicators = compute_daily_indicators(code, combined)
        enriched = dict(bar)
        enriched.update(bar_indicators.get(target_iso, {}))
        indicator_row = daily_indicators.get(target_iso, {})
        enriched.update(indicator_row)
        if "macd_bar" in indicator_row:
            enriched["macd_bar"] = indicator_row.get("macd_bar")
        enriched_bars.append(enriched)

    _persist_kbars(db, enriched_bars)
    db.commit()
    return len(enriched_bars)


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


def _merge_quote_rows(
    code: str,
    rows: list[dict[str, Any]],
    source_priority: list[str],
    settings: Any,
) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: _source_rank(row.get("source"), source_priority))
    base = ordered[0]
    result = {
        "code": code,
        "name": _first_non_empty(ordered, "name") or code,
        "exchange": base.get("exchange"),
        "industry": _first_non_empty(ordered, "industry", skip_values={"未分类"}) or "未分类",
        "is_st": any(bool(row.get("is_st")) for row in ordered),
        "trade_date": _latest_trade_date_from_quotes(ordered),
        "source": "unified_pool",
        "source_count": len({row.get("source") for row in ordered if row.get("source")}),
        "raw_sources": sorted({str(row.get("source")) for row in ordered if row.get("source")}),
        "quote_time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }

    conflicts: list[str] = []
    field_sources: dict[str, list[str]] = {}
    for field in NUMERIC_QUOTE_FIELDS:
        values = [
            (_num(row.get(field)), str(row.get("source") or "unknown"))
            for row in ordered
            if row.get(field) not in {None, "", "-"}
        ]
        values = [(value, source) for value, source in values if value != 0 or field in {"pct", "main_net_inflow_yi"}]
        if not values:
            result[field] = None if field in DERIVED_RETURN_FIELDS else 0.0
            continue
        numeric_values = [value for value, _source in values]
        merged = _robust_median(numeric_values)
        result[field] = round(merged, 4)
        field_sources[field] = [source for _value, source in values]
        if field in STRICT_CONSENSUS_FIELDS and len(numeric_values) >= 2:
            max_deviation = _max_relative_deviation(numeric_values, merged)
            if max_deviation > STRICT_CONSENSUS_FIELDS[field]:
                conflicts.append(f"{field}: 多源偏差 {round(max_deviation * 100, 2)}%")

    source_count = int(result["source_count"])
    quality = 100.0 - min(60.0, len(conflicts) * 12.0)
    result["quality_score"] = round(max(0.0, min(100.0, quality)), 2)
    result["freshness_status"] = "fresh"
    result["conflicts"] = conflicts
    result["source_trace"] = {
        "raw_sources": result["raw_sources"],
        "source_count": source_count,
        "field_sources": field_sources,
        "conflicts": conflicts,
        "merged_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    return result


def _enrich_industries(settings: Any, merged: list[dict[str, Any]]) -> None:
    from app.services.market_data import fetch_tushare_industries

    industry_map = fetch_tushare_industries(settings)
    if not industry_map:
        return
    enriched = 0
    for quote in merged:
        code = str(quote.get("code") or "")
        if not code:
            continue
        industry = industry_map.get(code)
        if industry and str(quote.get("industry")) == "未分类":
            quote["industry"] = industry
            enriched += 1
    if enriched:
        logger.info("行业富化: %d 只股票从 Tushare stock_basic 获取行业分类", enriched)


def _merge_quality(
    stock_quality: dict[str, Any] | None,
    quote: dict[str, Any],
    kline_trace: dict[str, Any],
) -> dict[str, Any]:
    quality = dict(stock_quality or {})
    missing = set(quality.get("missing_fields") or [])
    source_count = int(quote.get("source_count") or 0)
    quality["quote_source"] = "unified_pool"
    quality["quote_raw_sources"] = quote.get("raw_sources", [])
    quality["quote_source_count"] = source_count
    quality["quote_quality_score"] = quote.get("quality_score", 0)
    quality["quote_conflicts"] = quote.get("conflicts", [])
    quality["quote_cache_status"] = quote.get("freshness_status")
    quality["bars_source"] = kline_trace.get("selected_source")
    quality["kline_cache_status"] = kline_trace.get("freshness_status")
    if quote.get("conflicts"):
        missing.add("quote_conflict")
    if quote.get("freshness_status") == "stale_cache":
        missing.add("quote_freshness")
    quality["missing_fields"] = sorted(missing)
    return quality


def _persist_stock_quotes(
    db: Session,
    rows: list[dict[str, Any]],
    *,
    batch_size: int = STOCK_QUOTE_UPSERT_BATCH_SIZE,
) -> None:
    if not rows:
        return

    trade_date = _latest_trade_date_from_quotes(rows)
    seen_batch: set[tuple] = set()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    payload: list[dict[str, Any]] = []
    for row in rows:
        key = (row["code"], trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        source = str(row.get("raw_sources", ["unknown"])[0]) if row.get("raw_sources") else "unknown"
        payload.append(
            {
                "code": row["code"],
                "trade_date": trade_date,
                "name": str(row.get("name") or ""),
                "price": float(row.get("price") or 0),
                "pct": float(row.get("pct") or 0),
                "volume": float(row.get("volume") or 0),
                "amount_yi": float(row.get("amount_yi") or 0),
                "turnover_rate": float(row.get("turnover_rate") or 0),
                "pe_ttm": float(row.get("pe_ttm")) if row.get("pe_ttm") is not None else None,
                "total_mv_yi": float(row.get("total_mv_yi")) if row.get("total_mv_yi") is not None else None,
                "free_mv_yi": float(row.get("free_mv_yi")) if row.get("free_mv_yi") is not None else None,
                "chg60": float(row.get("chg60")) if row.get("chg60") is not None else None,
                "high": float(row.get("high")) if row.get("high") is not None else None,
                "low": float(row.get("low")) if row.get("low") is not None else None,
                "open": float(row.get("open")) if row.get("open") is not None else None,
                "preclose": float(row.get("preclose")) if row.get("preclose") is not None else None,
                "industry": row.get("industry") or "",
                "source": source,
                "source_count": int(row.get("source_count") or 0),
                "quality_score": float(row.get("quality_score") or 0),
                "freshness_status": str(row.get("freshness_status") or "fresh"),
                "snapped_at": now,
            }
        )
    if payload:
        db.execute(
            update(StockQuote)
            .where(StockQuote.freshness_status != "inactive")
            .values(freshness_status="inactive")
        )
        size = max(1, int(batch_size or STOCK_QUOTE_UPSERT_BATCH_SIZE))
        for start in range(0, len(payload), size):
            chunk = payload[start:start + size]
            stmt = mysql_insert(StockQuote).values(chunk)
            db.execute(
                stmt.on_duplicate_key_update(
                    name=stmt.inserted.name,
                    price=stmt.inserted.price,
                    pct=stmt.inserted.pct,
                    volume=stmt.inserted.volume,
                    amount_yi=stmt.inserted.amount_yi,
                    turnover_rate=stmt.inserted.turnover_rate,
                    pe_ttm=stmt.inserted.pe_ttm,
                    total_mv_yi=stmt.inserted.total_mv_yi,
                    free_mv_yi=stmt.inserted.free_mv_yi,
                    chg60=stmt.inserted.chg60,
                    high=stmt.inserted.high,
                    low=stmt.inserted.low,
                    open=stmt.inserted.open,
                    preclose=stmt.inserted.preclose,
                    industry=stmt.inserted.industry,
                    source=stmt.inserted.source,
                    source_count=stmt.inserted.source_count,
                    quality_score=stmt.inserted.quality_score,
                    freshness_status=stmt.inserted.freshness_status,
                    snapped_at=stmt.inserted.snapped_at,
                )
            )


def _persist_kbars(db: Session, bars: list[dict[str, Any]]) -> None:
    if not bars:
        return
    latest_allowed_date = _latest_allowed_bar_date()
    seen_batch: set[tuple] = set()
    rows: list[dict[str, Any]] = []
    for bar in bars:
        code = str(bar.get("code") or "")
        trade_date = _normalize_trade_date(bar.get("trade_date"))
        if not code or not trade_date:
            continue
        if trade_date > latest_allowed_date:
            continue
        key = (code, trade_date)
        if key in seen_batch:
            continue
        seen_batch.add(key)
        rows.append({
            "code": code,
            "trade_date": trade_date,
            "open": float(bar.get("open") or 0),
            "high": float(bar.get("high") or 0),
            "low": float(bar.get("low") or 0),
            "close": float(bar.get("close") or 0),
            "volume": float(bar.get("volume") or 0),
            "amount": float(bar.get("amount") or 0),
            "pct_chg": normalize_quote_metric(bar.get("pct_chg")),
            "turnover_rate": normalize_quote_metric(bar.get("turnover_rate")),
            "ma5": _float_or_none(bar.get("ma5")),
            "ma10": _float_or_none(bar.get("ma10")),
            "ma20": _float_or_none(bar.get("ma20")),
            "ma60": _float_or_none(bar.get("ma60")),
            "ma250": _float_or_none(bar.get("ma250")),
            "macd_dif": _float_or_none(bar.get("macd_dif")),
            "macd_dea": _float_or_none(bar.get("macd_dea")),
            "macd_bar": _float_or_none(bar.get("macd_bar", bar.get("macd"))),
            "kdj_k": _float_or_none(bar.get("kdj_k")),
            "kdj_d": _float_or_none(bar.get("kdj_d")),
            "kdj_j": _float_or_none(bar.get("kdj_j")),
            "rsi_6": _float_or_none(bar.get("rsi_6")),
            "rsi_12": _float_or_none(bar.get("rsi_12")),
            "rsi_24": _float_or_none(bar.get("rsi_24")),
            "boll_upper": _float_or_none(bar.get("boll_upper")),
            "boll_mid": _float_or_none(bar.get("boll_mid")),
            "boll_lower": _float_or_none(bar.get("boll_lower")),
            "cci": _float_or_none(bar.get("cci")),
            "bias_5": _float_or_none(bar.get("bias_ma5", bar.get("bias_5"))),
            "bias_10": _float_or_none(bar.get("bias_ma10", bar.get("bias_10"))),
            "bias_20": _float_or_none(bar.get("bias_ma20", bar.get("bias_20"))),
            "vol_ratio_5": _float_or_none(bar.get("volume_ratio_5d", bar.get("vol_ratio_5"))),
            "vol_ratio_20": _float_or_none(bar.get("vol_ratio_20")),
            "breakout20": int(bar.get("breakout20") or 0),
            "breakout60": int(bar.get("breakout60") or 0),
            "max_dd20": _float_or_none(bar.get("max_dd20")),
            "max_dd60": _float_or_none(bar.get("max_dd60")),
            "upper_shadow_count20": int(bar.get("upper_shadow_count20") or 0),
            "source": bar.get("source", "tushare"),
        })
    if not rows:
        return

    # Chunked exactly like the StockQuote upsert above.  One giant
    # ``INSERT ... ON DUPLICATE KEY UPDATE`` takes a row lock per row, and
    # daily_kbars is an 80-column wide table, so a full-market batch overflowed
    # InnoDB's lock table (MySQL 1206 "total number of locks exceeds the lock
    # table size") and aborted the market_data stage — the single reason
    # single-strategy runs could not complete (L-141).
    if quote_metrics_look_unready(rows):
        logger.warning(
            "daily_kbar 快照写入 %d 行：整批 pct_chg 全为空/0，判定行情快照未带回涨跌幅与换手率，"
            "这两列本批不参与覆盖",
            len(rows),
        )
        for row in rows:
            row["pct_chg"] = None
            row["turnover_rate"] = None

    for group in group_rows_by_quote_metric_presence(rows):
        for start in range(0, len(group.rows), DAILY_KBAR_UPSERT_BATCH_SIZE):
            chunk = group.rows[start:start + DAILY_KBAR_UPSERT_BATCH_SIZE]
            stmt = mysql_insert(DailyKbar).values(chunk)
            # 权威行情列每次同步都带回新值，直接覆盖；派生列交给
            # _preserve_derived_on_duplicate 保护，计数型派生列（breakout/长上影）
            # 整列退出 update 由 backfill 独占写入（L-144）。
            # pct_chg / turnover_rate 只在当批真的带回数值时才进 update：快照缺这两个
            # 字段时若按 0 覆盖，会把当日真值抹成恒零（L-146）。
            db.execute(stmt.on_duplicate_key_update(
                open=stmt.inserted.open,
                high=stmt.inserted.high,
                low=stmt.inserted.low,
                close=stmt.inserted.close,
                volume=stmt.inserted.volume,
                amount=stmt.inserted.amount,
                **group.quote_metric_update(stmt),
                **_preserve_derived_on_duplicate(stmt),
            ))


def _load_cached_bars_batch(
    db: Session, codes: list[str], limit: int, as_of_date: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """批量加载日K（DailyKbar 单表，无需 JOIN）。as_of_date 用于历史日期补跑。"""
    if not codes:
        return {}
    latest_allowed_date = as_of_date or _latest_allowed_bar_date()

    selected_columns = tuple(
        getattr(DailyKbar, name)
        for name in (
            "code", "trade_date", "open", "high", "low", "close", "volume", "amount",
            "pct_chg", "turnover_rate", "ma5", "ma10", "ma20", "ma60", "vol_ratio_5",
            "chip_winner_rate", "chip_weight_avg", "chip_cost_15", "chip_cost_50", "chip_cost_85",
            "holder_pct", "profit_efficiency", "rsi_6", "rsi_12", "rsi_24", "macd_dif",
            "macd_dea", "macd_bar", "bias_5", "bias_10", "bias_20", "breakout20",
            "breakout60", "upper_shadow_count20", "max_dd20", "max_dd60",
        )
    )
    bars_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # Rank only a narrow common-calendar window first. Suspended/new listings
    # that produce fewer than ``limit`` rows are repaired below with an exact
    # per-code fallback, so historical replay remains byte-for-byte equivalent.
    safety_sessions = max(limit + 20, limit)
    broad_cutoff = db.scalar(
        select(DailyKbar.trade_date)
        .where(DailyKbar.trade_date <= latest_allowed_date)
        .distinct()
        .order_by(DailyKbar.trade_date.desc())
        .offset(max(0, safety_sessions - 1))
        .limit(1)
    )
    chunk_size = 500
    for start in range(0, len(codes), chunk_size):
        code_chunk = codes[start : start + chunk_size]
        ranked = (
            select(
                *selected_columns,
                func.row_number().over(
                    partition_by=DailyKbar.code,
                    order_by=DailyKbar.trade_date.desc(),
                ).label("row_number"),
            )
            .where(
                DailyKbar.code.in_(code_chunk),
                DailyKbar.trade_date <= latest_allowed_date,
                *(
                    (DailyKbar.trade_date >= broad_cutoff,)
                    if broad_cutoff is not None
                    else ()
                ),
            )
            .subquery()
        )
        rows = db.execute(
            select(ranked).where(ranked.c.row_number <= limit)
            .order_by(ranked.c.code, ranked.c.trade_date.asc())
        ).mappings()
        for row in rows:
            chip_cost_15 = row["chip_cost_15"]
            chip_cost_85 = row["chip_cost_85"]
            bars_by_code[str(row["code"])].append({
                "code": row["code"],
                "trade_date": _normalize_trade_date(row["trade_date"]),
                "open": float(row["open"] or 0),
                "high": float(row["high"] or 0),
                "low": float(row["low"] or 0),
                "close": float(row["close"] or 0),
                "volume": float(row["volume"] or 0),
                "amount": float(row["amount"] or 0),
                "pct_chg": float(row["pct_chg"] or 0),
                "turnover_rate": float(row["turnover_rate"] or 0),
                "ma5": row["ma5"], "ma10": row["ma10"], "ma20": row["ma20"], "ma60": row["ma60"],
                "volume_ratio_5d": row["vol_ratio_5"],
                "chip_winner_rate": row["chip_winner_rate"], "chip_weight_avg": row["chip_weight_avg"],
                "chip_cost_15": chip_cost_15, "chip_cost_50": row["chip_cost_50"], "chip_cost_85": chip_cost_85,
                "holder_pct": row["holder_pct"], "profit_efficiency": row["profit_efficiency"],
                "chip_concentration": round((float(chip_cost_85) - float(chip_cost_15)) / float(chip_cost_15) * 100, 2) if chip_cost_15 and float(chip_cost_15) > 0 and chip_cost_85 else None,
                "_indicator": {
                    "rsi_6": row["rsi_6"], "rsi_12": row["rsi_12"], "rsi_24": row["rsi_24"],
                    "macd_dif": row["macd_dif"], "macd_dea": row["macd_dea"], "macd_bar": row["macd_bar"],
                    "bias_ma5": row["bias_5"], "bias_ma10": row["bias_10"], "bias_ma20": row["bias_20"],
                    "breakout20": row["breakout20"], "breakout60": row["breakout60"],
                    "upper_shadow_count20": row["upper_shadow_count20"],
                    "max_dd20": row["max_dd20"], "max_dd60": row["max_dd60"],
                },
            })

    result: dict[str, list[dict[str, Any]]] = {}
    for code in codes:
        rows = bars_by_code.get(code, [])
        result[code] = (
            rows
            if len(rows) >= limit
            else _load_cached_bars(db, code, limit, as_of_date=latest_allowed_date)
        )
    return result


def _load_cached_bars(
    db: Session,
    code: str,
    limit: int,
    *,
    as_of_date: str | None = None,
) -> list[dict[str, Any]]:
    latest_allowed_date = as_of_date or _latest_allowed_bar_date()
    rows = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code == code, DailyKbar.trade_date <= latest_allowed_date)
        .order_by(DailyKbar.trade_date.desc())
        .limit(limit)
    ).all()

    result = []
    for row in reversed(rows):
        bar = {
            "code": row.code,
            "trade_date": _normalize_trade_date(row.trade_date),
            "open": float(row.open or 0),
            "high": float(row.high or 0),
            "low": float(row.low or 0),
            "close": float(row.close or 0),
            "volume": float(row.volume or 0),
            "amount": float(row.amount or 0),
            "pct_chg": float(row.pct_chg or 0),
            "turnover_rate": float(row.turnover_rate or 0),
            "ma5": row.ma5,
            "ma10": row.ma10,
            "ma20": row.ma20,
            "ma60": row.ma60,
            "volume_ratio_5d": row.vol_ratio_5,
            # 筹码分布字段（Tushare 真实数据）
            "chip_winner_rate": row.chip_winner_rate,
            "chip_weight_avg": row.chip_weight_avg,
            "chip_cost_15": row.chip_cost_15,
            "chip_cost_50": row.chip_cost_50,
            "chip_cost_85": row.chip_cost_85,
            "holder_pct": row.holder_pct,
            "profit_efficiency": row.profit_efficiency,
            # 筹码集中度（前移计算，供 prefilter 阶段使用，避免 chip-single-peak 策略 prefilter 死锁）
            "chip_concentration": round((float(row.chip_cost_85) - float(row.chip_cost_15)) / float(row.chip_cost_15) * 100, 2) if row.chip_cost_15 and float(row.chip_cost_15) > 0 and row.chip_cost_85 else None,
            "_indicator": {
                "rsi_6": row.rsi_6,
                "rsi_12": row.rsi_12,
                "rsi_24": row.rsi_24,
                "macd_dif": row.macd_dif,
                "macd_dea": row.macd_dea,
                "macd_bar": row.macd_bar,
                "bias_ma5": row.bias_5,
                "bias_ma10": row.bias_10,
                "bias_ma20": row.bias_20,
                "breakout20": row.breakout20,
                "breakout60": row.breakout60,
                "upper_shadow_count20": row.upper_shadow_count20,
                "max_dd20": row.max_dd20,
                "max_dd60": row.max_dd60,
            },
        }
        result.append(bar)
    return result


def _record_source_health(
    db: Session,
    source: str,
    source_type: str,
    status: str,
    error: str | None = None,
    latency_ms: float = 0.0,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    latency = round(float(latency_ms), 2)
    row = db.get(DataSourceHealth, {"source": source, "source_type": source_type})
    if row is None:
        row = DataSourceHealth(
            source=source,
            source_type=source_type,
            success_count=0,
            failure_count=0,
            empty_count=0,
            latency_ms=latency,
            updated_at=now,
        )
        db.add(row)

    if status == "success":
        row.success_count = int(row.success_count or 0) + 1
        row.last_success_at = now
        row.last_error = None
    elif status == "empty":
        row.empty_count = int(row.empty_count or 0) + 1
        row.last_failure_at = now
        row.last_error = error or "empty result"
    else:
        row.failure_count = int(row.failure_count or 0) + 1
        row.last_failure_at = now
        row.last_error = error or "unknown"
    row.latency_ms = latency
    row.updated_at = now

def _quote_meta(status: str, quotes: list[dict[str, Any]], errors: list[str] | None = None) -> dict[str, Any]:
    fetched_times = [
        datetime.fromisoformat(str(item.get("fetched_at")))
        for item in quotes
        if item.get("fetched_at") and "T" in str(item.get("fetched_at"))
    ]
    newest = max(fetched_times) if fetched_times else datetime.now(timezone.utc).replace(tzinfo=None)
    sources = sorted({source for quote in quotes for source in quote.get("raw_sources", [])})
    source_counts = [int(quote.get("source_count") or 0) for quote in quotes]
    return {
        "status": status,
        "count": len(quotes),
        "sources": sources,
        "source_count": round(statistics.fmean(source_counts), 2) if source_counts else 0,
        "cache_age_seconds": max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - newest).total_seconds())),
        "errors": errors or [],
    }


def _load_cached_bars_summary(
    db: Session, codes: list[str], min_bar_count: int
) -> dict[str, dict[str, Any]]:
    """Batch load bar summary for readiness check: count + latest trade_date per code.

    当候选码数量接近全市场总量时，全量 GROUP BY + Python 过滤比
    IN 子句逐码查找快 3x（220ms vs 669ms @ 5000 codes）。
    """
    if not codes:
        return {}
    code_set = set(codes)
    rows = db.execute(
        select(
            DailyKbar.code,
            func.count().label("bar_count"),
            func.max(DailyKbar.trade_date).label("latest_trade_date"),
        )
        .group_by(DailyKbar.code)
    ).all()
    return {
        row.code: {
            "bar_count": int(row.bar_count or 0),
            "latest_trade_date": _normalize_trade_date(row.latest_trade_date),
        }
        for row in rows
        if row.code in code_set
    }


def _bars_cover_quote_date(bars: list[dict[str, Any]], quote: dict[str, Any]) -> bool:
    quote_date = _required_bar_date_for_quote(quote)
    latest = max(_normalize_trade_date(bar.get("trade_date")) for bar in bars)
    return latest >= quote_date


def _source_rank(source: Any, source_priority: list[str]) -> int:
    try:
        return source_priority.index(str(source))
    except ValueError:
        return len(source_priority) + 1


def _first_non_empty(rows: list[dict[str, Any]], field: str, skip_values: set[str] | None = None) -> str:
    skip_values = skip_values or set()
    for row in rows:
        value = str(row.get(field) or "").strip()
        if value and value not in skip_values:
            return value
    return ""


def _robust_median(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _float_or_none(value: Any) -> float | None:
    if value in {None, "", "-"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_relative_deviation(values: list[float], center: float) -> float:
    denominator = max(abs(center), 0.0001)
    return max(abs(value - center) / denominator for value in values)

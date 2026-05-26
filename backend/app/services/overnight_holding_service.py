from __future__ import annotations

import json
import logging
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.tables import BacktestRun, DailyKbar, MinuteKbar, Stock
from app.services.tushare_client import (
    fetch_minute_kbars,
    fetch_recent_index_minute_kbars,
    get_pro,
)

logger = logging.getLogger(__name__)

STRATEGY_ID = "overnight-holding-method"
STRATEGY_NAME = "隔夜持股法"
BENCHMARK_CODE = "000300"
BENCHMARK_NAME = "沪深300"
TAIL_START_TIME = "14:30:00"
MORNING_EXIT_TIME = "09:35:00"
LIMIT_UP_PCT = 0.10
VWAP_HOLD_TOLERANCE_BPS = 12.0
TAIL_VWAP_MAX_GAP_BPS = 170.0
TAIL_RETEST_WINDOW_MINUTES = 8
SHRINK_LIMIT_VOLUME_RATIO = 0.85
DEFAULT_MIN_ABOVE_VWAP_RATIO = 0.86
MIN_PCT = 3.0
MAX_PCT = 5.0
MIN_TURNOVER = 5.0
MAX_TURNOVER = 10.0
MIN_MARKET_CAP_YI = 50.0
MAX_MARKET_CAP_YI = 200.0
MIN_INTRADAY_VOLUME_RATIO = 1.0
MIN_SIGNAL_SAMPLE = 30
WALK_FORWARD_MIN_TRAIN_DATES = 2


@dataclass(frozen=True)
class CostAssumption:
    buy_commission_bps: float = 2.0
    buy_slippage_bps: float = 5.0
    sell_commission_bps: float = 2.0
    sell_stamp_tax_bps: float = 5.0
    sell_slippage_bps: float = 5.0

    @property
    def round_trip_cost_bps(self) -> float:
        return (
            self.buy_commission_bps
            + self.buy_slippage_bps
            + self.sell_commission_bps
            + self.sell_stamp_tax_bps
            + self.sell_slippage_bps
        )


@dataclass(frozen=True)
class OvernightVariant:
    name: str
    label: str
    min_relative_strength_pct: float


BASELINE_VARIANT = OvernightVariant(
    name="baseline",
    label="基线版",
    min_relative_strength_pct=0.0,
)

CANDIDATE_VARIANT = OvernightVariant(
    name="candidate",
    label="增强版",
    min_relative_strength_pct=0.8,
)


def run_overnight_holding_backtest(
    db: Session,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    started_at = perf_counter()
    start = _normalize_trade_date(start_date)
    end = _normalize_trade_date(end_date)
    if not start or not end:
        raise ValueError("start_date and end_date are required")
    if start > end:
        raise ValueError("start_date must be <= end_date")

    cost = CostAssumption()
    signal_dates, date_lookup = _signal_trade_dates(db, start, end)
    if not signal_dates:
        return {
            "strategy_id": STRATEGY_ID,
            "strategy_name": STRATEGY_NAME,
            "status": "no_data",
            "dates_scanned": 0,
            "dates_succeeded": 0,
            "dates_failed": 0,
            "total_recommendations": 0,
            "total_elapsed_ms": 0,
            "labeled_count": 0,
            "aggregate_metrics": _empty_metrics(),
            "data_coverage": _build_data_coverage([], [], _local_minute_inventory(db), []),
            "results": [],
            "notes": ["区间内没有可用于隔夜策略验证的交易日，需至少存在次一交易日日线数据。"],
        }

    logger.info(
        "overnight holding backtest start: dates=%s~%s signal_dates=%d",
        start,
        end,
        len(signal_dates),
    )

    window_start = date_lookup[max(0, date_lookup.index(signal_dates[0]) - 25)]
    window_end = date_lookup[min(len(date_lookup) - 1, date_lookup.index(signal_dates[-1]) + 5)]
    daily_by_date, history_by_code = _load_daily_window(db, window_start, window_end)
    stock_meta = _load_stock_meta(db, history_by_code.keys())
    daily_basic_cache: dict[str, dict[str, dict[str, float]]] = {}
    minute_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    benchmark_cache: dict[str, dict[str, Any]] = {}
    minute_source_trace: list[dict[str, Any]] = []
    minute_inventory = _local_minute_inventory(db)

    comparisons: dict[str, dict[str, Any]] = {}
    for variant in (BASELINE_VARIANT, CANDIDATE_VARIANT):
        comparisons[variant.name] = _simulate_variant(
            db=db,
            signal_dates=signal_dates,
            date_lookup=date_lookup,
            daily_by_date=daily_by_date,
            history_by_code=history_by_code,
            stock_meta=stock_meta,
            daily_basic_cache=daily_basic_cache,
            minute_cache=minute_cache,
            benchmark_cache=benchmark_cache,
            minute_source_trace=minute_source_trace,
            variant=variant,
            cost=cost,
        )

    adopted_variant, adoption_reason = _choose_variant(signal_dates, comparisons)
    selected = comparisons[adopted_variant]

    response = {
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "status": "completed",
        "strategy_window": {"start_date": start, "end_date": end},
        "cost_assumption": {
            "buy_commission_bps": cost.buy_commission_bps,
            "buy_slippage_bps": cost.buy_slippage_bps,
            "sell_commission_bps": cost.sell_commission_bps,
            "sell_stamp_tax_bps": cost.sell_stamp_tax_bps,
            "sell_slippage_bps": cost.sell_slippage_bps,
            "round_trip_cost_bps": cost.round_trip_cost_bps,
        },
        "dates_scanned": len(signal_dates),
        "dates_succeeded": sum(1 for row in selected["date_summaries"] if row.get("status") == "ok"),
        "dates_failed": sum(1 for row in selected["date_summaries"] if row.get("status") != "ok"),
        "total_recommendations": selected["metrics"]["trade_count"],
        "total_elapsed_ms": int((perf_counter() - started_at) * 1000),
        "labeled_count": selected["metrics"]["trade_count"],
        "results": selected["date_summaries"],
        "aggregate_metrics": selected["metrics"],
        "comparison": {
            "baseline": comparisons["baseline"]["metrics"],
            "candidate": comparisons["candidate"]["metrics"],
            "selected_variant": adopted_variant,
            "selected_variant_label": comparisons[adopted_variant]["variant"].label,
            "adoption_reason": adoption_reason,
            "hypothesis": "把“强于大盘”从弱定义（仅跑赢沪深300）收紧为强定义（至少领先沪深300 0.8 个百分点）。",
            "splits": {
                "baseline": comparisons["baseline"]["split_metrics"],
                "candidate": comparisons["candidate"]["split_metrics"],
            },
            "walk_forward": {
                "baseline": comparisons["baseline"]["walk_forward"],
                "candidate": comparisons["candidate"]["walk_forward"],
            },
        },
        "data_coverage": _build_data_coverage(
            signal_dates,
            selected["date_summaries"],
            minute_inventory,
            minute_source_trace,
        ),
        "recommendation_records": _recommendation_records(selected["trades"]),
        "trade_samples": selected["trade_samples"],
        "false_positive_samples": selected["false_positive_samples"],
        "notes": selected["notes"],
    }
    _persist_backtest_run(db, start, end, response)
    logger.info(
        "overnight holding backtest done: variant=%s trades=%d accuracy=%.3f avg=%.3f",
        adopted_variant,
        selected["metrics"]["trade_count"],
        selected["metrics"]["recommendation_accuracy"],
        selected["metrics"]["average_net_return"],
    )
    return response


def _signal_trade_dates(db: Session, start_date: str, end_date: str) -> tuple[list[str], list[str]]:
    rows = list(
        db.execute(
            select(DailyKbar.trade_date)
            .distinct()
            .where(DailyKbar.trade_date <= end_date)
            .order_by(DailyKbar.trade_date.asc())
        ).scalars()
    )
    if not rows:
        return [], []
    signal_dates: list[str] = []
    for idx, trade_date in enumerate(rows):
        if trade_date < start_date or trade_date > end_date:
            continue
        if idx + 1 >= len(rows):
            continue
        signal_dates.append(trade_date)
    return signal_dates, rows


def _load_daily_window(
    db: Session,
    start_date: str,
    end_date: str,
) -> tuple[dict[str, dict[str, DailyKbar]], dict[str, list[DailyKbar]]]:
    rows = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.trade_date >= start_date, DailyKbar.trade_date <= end_date)
        .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
    ).all()
    daily_by_date: dict[str, dict[str, DailyKbar]] = defaultdict(dict)
    history_by_code: dict[str, list[DailyKbar]] = defaultdict(list)
    for row in rows:
        daily_by_date[str(row.trade_date)][str(row.code)] = row
        history_by_code[str(row.code)].append(row)
    return dict(daily_by_date), dict(history_by_code)


def _load_stock_meta(db: Session, codes: Any) -> dict[str, dict[str, Any]]:
    code_list = [str(code) for code in codes if str(code)]
    if not code_list:
        return {}
    rows = db.scalars(select(Stock).where(Stock.code.in_(code_list))).all()
    return {
        str(row.code): {
            "name": str(row.name or row.code),
            "industry": row.industry or "未分类",
            "is_st": bool(row.is_st),
        }
        for row in rows
    }


def _load_daily_basic_map(
    trade_date: str,
    cache: dict[str, dict[str, dict[str, float]]],
) -> dict[str, dict[str, float]]:
    if trade_date in cache:
        return cache[trade_date]
    pro = get_pro()
    df = pro.daily_basic(
        trade_date=trade_date.replace("-", ""),
        fields="ts_code,trade_date,turnover_rate,total_mv,circ_mv",
    )
    mapping: dict[str, dict[str, float]] = {}
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            code = str(row.get("ts_code", "")).split(".")[0]
            if len(code) != 6:
                continue
            mapping[code] = {
                "turnover_rate": _num(row.get("turnover_rate")),
                "total_mv_yi": round(_num(row.get("total_mv")) / 10000.0, 3),
                "circ_mv_yi": round(_num(row.get("circ_mv")) / 10000.0, 3),
            }
    cache[trade_date] = mapping
    return mapping


def _simulate_variant(
    *,
    db: Session,
    signal_dates: list[str],
    date_lookup: list[str],
    daily_by_date: dict[str, dict[str, DailyKbar]],
    history_by_code: dict[str, list[DailyKbar]],
    stock_meta: dict[str, dict[str, Any]],
    daily_basic_cache: dict[str, dict[str, dict[str, float]]],
    minute_cache: dict[tuple[str, str], list[dict[str, Any]]],
    benchmark_cache: dict[str, dict[str, Any]],
    minute_source_trace: list[dict[str, Any]],
    variant: OvernightVariant,
    cost: CostAssumption,
) -> dict[str, Any]:
    trades: list[dict[str, Any]] = []
    date_summaries: list[dict[str, Any]] = []

    benchmark_bundle = _load_benchmark_bundle(db, benchmark_cache, signal_dates, date_lookup, minute_source_trace)
    benchmark_by_date = _group_minutes_by_date(benchmark_bundle)

    for trade_date in signal_dates:
        basic_map = _load_daily_basic_map(trade_date, daily_basic_cache)
        daily_rows = daily_by_date.get(trade_date, {})
        candidates = _daily_candidates(
            trade_date=trade_date,
            daily_rows=daily_rows,
            history_by_code=history_by_code,
            stock_meta=stock_meta,
            daily_basic_map=basic_map,
        )
        if not candidates:
            date_summaries.append(
                {
                    "trade_date": trade_date,
                    "status": "skipped",
                    "candidate_count": 0,
                    "signal_count": 0,
                    "resolved_trade_count": 0,
                    "reason": "日线过滤后无候选",
                }
            )
            continue

        signals: list[dict[str, Any]] = []
        minute_ready_candidate_count = 0
        missing_minute_codes: list[str] = []
        benchmark_minutes = benchmark_by_date.get(trade_date) or []
        for candidate in candidates:
            code = candidate["code"]
            minutes = _load_stock_minutes(db, minute_cache, code, trade_date, minute_source_trace)
            if not minutes:
                if len(missing_minute_codes) < 5:
                    missing_minute_codes.append(code)
                continue
            minute_ready_candidate_count += 1
            signal = _locate_entry_signal(
                candidate=candidate,
                minutes=minutes,
                benchmark_minutes=benchmark_minutes,
                variant=variant,
            )
            if signal is not None:
                signals.append(signal)

        resolved_trades: list[dict[str, Any]] = []
        unresolved_count = 0
        for signal in signals:
            trade = _resolve_exit(
                db=db,
                date_lookup=date_lookup,
                history_by_code=history_by_code,
                minute_cache=minute_cache,
                benchmark_by_date=benchmark_by_date,
                minute_source_trace=minute_source_trace,
                signal=signal,
                cost=cost,
            )
            if trade.get("resolved"):
                trades.append(trade)
                resolved_trades.append(trade)
            else:
                unresolved_count += 1

        date_summaries.append(
            {
                "trade_date": trade_date,
                "status": "ok",
                "candidate_count": len(candidates),
                "minute_ready_candidate_count": minute_ready_candidate_count,
                "minute_missing_candidate_count": max(0, len(candidates) - minute_ready_candidate_count),
                "benchmark_minute_covered": bool(benchmark_minutes),
                "signal_count": len(signals),
                "resolved_trade_count": len(resolved_trades),
                "unresolved_trade_count": unresolved_count,
                "missing_minute_codes": missing_minute_codes,
                "recommendation_accuracy": round(
                    sum(1 for item in resolved_trades if item["accurate"]) / len(resolved_trades),
                    3,
                ) if resolved_trades else 0.0,
                "average_net_return": round(
                    sum(item["net_return_pct"] for item in resolved_trades) / len(resolved_trades),
                    3,
                ) if resolved_trades else 0.0,
            }
        )

    split_metrics = _build_split_metrics(signal_dates, trades)
    metrics = _build_metrics(trades)
    metrics["variant"] = variant.name
    metrics["variant_label"] = variant.label
    metrics["relative_strength_threshold"] = variant.min_relative_strength_pct
    return {
        "variant": variant,
        "metrics": metrics,
        "split_metrics": split_metrics,
        "walk_forward": _build_walk_forward_analysis(signal_dates, trades),
        "date_summaries": date_summaries,
        "trades": trades,
        "trade_samples": _trade_samples(trades),
        "false_positive_samples": _false_positive_samples(trades),
        "notes": _variant_notes(signal_dates, trades, variant),
    }


def _daily_candidates(
    *,
    trade_date: str,
    daily_rows: dict[str, DailyKbar],
    history_by_code: dict[str, list[DailyKbar]],
    stock_meta: dict[str, dict[str, Any]],
    daily_basic_map: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for code, bar in daily_rows.items():
        meta = stock_meta.get(code) or {}
        if not _is_main_board(code) or bool(meta.get("is_st")):
            continue
        pct = _num(bar.pct_chg)
        if pct < MIN_PCT or pct > MAX_PCT:
            continue
        basic = daily_basic_map.get(code)
        if basic is None:
            continue
        turnover_rate = _num(bar.turnover_rate, _num(basic.get("turnover_rate")))
        if turnover_rate < MIN_TURNOVER or turnover_rate > MAX_TURNOVER:
            continue
        total_mv_yi = _num(basic.get("total_mv_yi"))
        if total_mv_yi < MIN_MARKET_CAP_YI or total_mv_yi > MAX_MARKET_CAP_YI:
            continue

        history = history_by_code.get(code) or []
        hist_idx = next((idx for idx, item in enumerate(history) if item.trade_date == trade_date), -1)
        if hist_idx <= 0:
            continue
        recent_limit_up_count = sum(
            1 for item in history[max(0, hist_idx - 20) : hist_idx] if _is_limit_up_bar(item)
        )
        if recent_limit_up_count <= 0:
            continue

        recent_volumes = [_num(item.volume) for item in history[max(0, hist_idx - 5) : hist_idx] if _num(item.volume) > 0]
        avg_prev5_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0.0
        if avg_prev5_volume <= 0:
            continue

        prev_close = _num(history[hist_idx - 1].close)
        if prev_close <= 0:
            continue

        result.append(
            {
                "code": code,
                "name": meta.get("name", code),
                "industry": meta.get("industry", "未分类"),
                "trade_date": trade_date,
                "turnover_rate": turnover_rate,
                "pct": pct,
                "close": _num(bar.close),
                "daily_volume": _num(bar.volume),
                "signal_day_amount": _num(bar.amount),
                "prev_close": prev_close,
                "total_mv_yi": total_mv_yi,
                "recent_limit_up_count": recent_limit_up_count,
                "avg_prev5_volume": avg_prev5_volume,
            }
        )
    return result


def _locate_entry_signal(
    *,
    candidate: dict[str, Any],
    minutes: list[dict[str, Any]],
    benchmark_minutes: list[dict[str, Any]],
    variant: OvernightVariant,
) -> dict[str, Any] | None:
    if not minutes or not benchmark_minutes:
        return None

    benchmark_map = {str(item["trade_time"])[11:16]: item for item in benchmark_minutes}
    time_keys = [str(item["trade_time"])[11:16] for item in minutes]
    try:
        tail_start_index = next(idx for idx, value in enumerate(time_keys) if value >= "14:30")
    except StopIteration:
        return None

    cumulative_volume = 0.0
    cumulative_amount = 0.0
    tail_above_vwap_count = 0
    day_high = 0.0
    last_new_high_index: int | None = None

    for idx, item in enumerate(minutes):
        close_price = _num(item.get("close"))
        high_price = max(close_price, _num(item.get("high")))
        low_price = min(close_price, _num(item.get("low"), close_price))
        volume = _num(item.get("volume"))
        amount = _num(item.get("amount"))
        if high_price >= day_high:
            day_high = high_price
            last_new_high_index = idx
        cumulative_volume += volume
        cumulative_amount += amount
        vwap = cumulative_amount / (cumulative_volume * 100.0) if cumulative_volume > 0 and cumulative_amount > 0 else close_price
        if idx >= tail_start_index and close_price >= vwap * (1 - VWAP_HOLD_TOLERANCE_BPS / 10000.0):
            tail_above_vwap_count += 1

        if idx < tail_start_index or last_new_high_index is None:
            continue
        if idx - last_new_high_index > TAIL_RETEST_WINDOW_MINUTES:
            continue

        progress = (idx + 1) / max(1, len(minutes))
        full_day_volume_est = cumulative_volume / max(progress, 0.05)
        intraday_volume_ratio = full_day_volume_est / max(candidate["avg_prev5_volume"], 1.0)
        intraday_pct = ((close_price - candidate["prev_close"]) / candidate["prev_close"]) * 100 if candidate["prev_close"] > 0 else 0.0
        benchmark_row = benchmark_map.get(time_keys[idx])
        benchmark_ret = 0.0
        if benchmark_row:
            benchmark_ret = _pct_change(
                _num(benchmark_row.get("pre_close")),
                _num(benchmark_row.get("close")),
            )
        relative_strength = intraday_pct - benchmark_ret
        above_vwap_ratio = tail_above_vwap_count / max(1, idx - tail_start_index + 1)
        hold_ok = low_price >= vwap * (1 - VWAP_HOLD_TOLERANCE_BPS / 10000.0)
        tail_vwap_gap_bps = ((low_price / vwap) - 1.0) * 10000.0 if vwap > 0 else 0.0
        retest_ok = tail_vwap_gap_bps <= TAIL_VWAP_MAX_GAP_BPS
        if intraday_pct < MIN_PCT or intraday_pct > MAX_PCT:
            continue
        if intraday_volume_ratio < MIN_INTRADAY_VOLUME_RATIO:
            continue
        if relative_strength < variant.min_relative_strength_pct:
            continue
        if above_vwap_ratio < DEFAULT_MIN_ABOVE_VWAP_RATIO:
            continue
        if not hold_ok or not retest_ok or close_price < vwap:
            continue

        return {
            "code": candidate["code"],
            "name": candidate["name"],
            "industry": candidate["industry"],
            "trade_date": candidate["trade_date"],
            "recommendation_time": str(item["trade_time"]),
            "entry_time": str(item["trade_time"]),
            "entry_price": round(close_price, 3),
            "signal_day_volume": candidate["daily_volume"],
            "conditions": {
                "after_1430": True,
                "is_main_board": True,
                "pct_between_3_5": round(intraday_pct, 3),
                "recent_limit_up_count_20d": candidate["recent_limit_up_count"],
                "intraday_volume_ratio": round(intraday_volume_ratio, 3),
                "turnover_rate": round(candidate["turnover_rate"], 3),
                "total_mv_yi": round(candidate["total_mv_yi"], 3),
                "above_vwap_ratio": round(above_vwap_ratio, 3),
                "tail_above_vwap_ratio": round(above_vwap_ratio, 3),
                "relative_strength_vs_hs300": round(relative_strength, 3),
                "tail_new_high_retest_minutes": idx - last_new_high_index,
                "session_vwap": round(vwap, 3),
                "tail_vwap_gap_bps": round(tail_vwap_gap_bps, 1),
            },
            "entry_executable": volume > 0,
        }
    return None


def _resolve_exit(
    *,
    db: Session,
    date_lookup: list[str],
    history_by_code: dict[str, list[DailyKbar]],
    minute_cache: dict[tuple[str, str], list[dict[str, Any]]],
    benchmark_by_date: dict[str, list[dict[str, Any]]],
    minute_source_trace: list[dict[str, Any]] | None,
    signal: dict[str, Any],
    cost: CostAssumption,
) -> dict[str, Any]:
    code = str(signal["code"])
    history = history_by_code.get(code) or []
    hist_idx = next((idx for idx, item in enumerate(history) if item.trade_date == signal["trade_date"]), -1)
    if hist_idx < 0 or hist_idx + 1 >= len(history):
        return {**signal, "resolved": False, "reason": "missing_next_daily_bar"}

    exit_row: dict[str, Any] | None = None
    exit_daily_bar: DailyKbar | None = None
    exit_reason = "next_morning_exit"
    hit_one_word = False
    hit_shrink_limit = False
    for next_idx in range(hist_idx + 1, len(history)):
        bar = history[next_idx]
        prev_close = _num(history[next_idx - 1].close)
        up_limit = _main_board_up_limit(prev_close)
        one_word = _is_one_word_limit_up(bar, up_limit)
        shrink_limit = _is_shrinking_limit_up(bar, up_limit, signal["signal_day_volume"])
        if one_word:
            hit_one_word = True
            exit_reason = "one_word_limit_up_hold"
            continue
        if shrink_limit:
            hit_shrink_limit = True
            exit_reason = "shrinking_limit_up_hold"
            continue

        exit_daily_bar = bar
        minutes = _load_stock_minutes(db, minute_cache, code, str(bar.trade_date), minute_source_trace)
        exit_row = _minute_at_or_after(minutes, MORNING_EXIT_TIME)
        if exit_row is None and minutes:
            exit_row = minutes[0]
        exit_reason = "next_morning_exit" if next_idx == hist_idx + 1 else "deferred_after_limit_up"
        break

    if exit_daily_bar is None or exit_row is None:
        return {**signal, "resolved": False, "reason": "missing_exit_minute"}

    benchmark_exit_row = _minute_at_or_after(benchmark_by_date.get(str(exit_daily_bar.trade_date)) or [], MORNING_EXIT_TIME)
    benchmark_entry_row = _benchmark_row_for_entry(benchmark_by_date.get(signal["trade_date"]) or [], signal["entry_time"])
    benchmark_return_pct = 0.0
    if benchmark_entry_row and benchmark_exit_row:
        benchmark_return_pct = _pct_change(
            _num(benchmark_entry_row.get("close")),
            _num(benchmark_exit_row.get("close")),
        )

    entry_exec = _apply_entry_cost(signal["entry_price"], cost)
    exit_close = _num(exit_row.get("close"))
    exit_high = max(exit_close, _num(exit_row.get("high"), exit_close))
    exit_low = min(exit_close, _num(exit_row.get("low"), exit_close))
    exit_exec = _apply_exit_cost(exit_close, cost)
    achievable_exit_exec = _apply_exit_cost(exit_high, cost)
    drawdown_exit_exec = _apply_exit_cost(exit_low, cost)
    net_return_pct = _pct_change(entry_exec, exit_exec)
    achievable_return_pct = _pct_change(entry_exec, achievable_exit_exec)
    morning_drawdown_pct = min(0.0, _pct_change(entry_exec, drawdown_exit_exec))
    accurate = bool(signal.get("entry_executable")) and net_return_pct > 0
    false_positive_reason = None if accurate else _false_positive_reason(signal, exit_daily_bar, net_return_pct, benchmark_return_pct)
    return {
        **signal,
        "resolved": True,
        "exit_date": str(exit_daily_bar.trade_date),
        "exit_time": str(exit_row["trade_time"]),
        "exit_price": round(exit_close, 3),
        "gross_return_pct": round(_pct_change(signal["entry_price"], exit_close), 3),
        "net_return_pct": round(net_return_pct, 3),
        "achievable_return_pct": round(achievable_return_pct, 3),
        "morning_drawdown_pct": round(morning_drawdown_pct, 3),
        "benchmark_return_pct": round(benchmark_return_pct, 3),
        "accurate": accurate,
        "exit_reason": exit_reason,
        "one_word_limit_up": hit_one_word,
        "shrinking_limit_up": hit_shrink_limit,
        "holding_days": max(1, next_idx - hist_idx),
        "false_positive_reason": false_positive_reason,
    }


def _build_split_metrics(signal_dates: list[str], trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not signal_dates:
        return {"train": _empty_metrics(), "test": _empty_metrics()}
    split_index = max(1, int(len(signal_dates) * 0.6))
    if split_index >= len(signal_dates):
        split_index = max(1, len(signal_dates) - 1)
    train_dates = set(signal_dates[:split_index])
    test_dates = set(signal_dates[split_index:])
    train_trades = [item for item in trades if item["trade_date"] in train_dates]
    test_trades = [item for item in trades if item["trade_date"] in test_dates]
    return {
        "train": _build_metrics(train_trades),
        "test": _build_metrics(test_trades),
    }


def _build_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [item for item in trades if item.get("resolved")]
    returns = [float(item["net_return_pct"]) for item in resolved]
    gross_returns = [float(item.get("gross_return_pct") or 0.0) for item in resolved]
    achievable_returns = [float(item.get("achievable_return_pct") or item.get("gross_return_pct") or 0.0) for item in resolved]
    trade_drawdowns = [float(item.get("morning_drawdown_pct") or 0.0) for item in resolved]
    holding_days = [float(item.get("holding_days") or 1.0) for item in resolved]
    benchmark_returns = [float(item.get("benchmark_return_pct") or 0.0) for item in resolved]
    accurate_count = sum(1 for item in resolved if item.get("accurate"))
    trade_count = len(resolved)
    avg_return = round(sum(returns) / trade_count, 3) if trade_count else 0.0
    median_return = round(statistics.median(returns), 3) if returns else 0.0
    wins = [item for item in resolved if float(item["net_return_pct"]) > 0]
    losses = [item for item in resolved if float(item["net_return_pct"]) <= 0]
    avg_win = sum(float(item["net_return_pct"]) for item in wins) / len(wins) if wins else 0.0
    avg_loss = sum(float(item["net_return_pct"]) for item in losses) / len(losses) if losses else 0.0
    profit_loss_ratio = round(avg_win / max(abs(avg_loss), 0.001), 3) if wins else 0.0
    profit_factor = round(
        sum(float(item["net_return_pct"]) for item in wins) / max(abs(sum(float(item["net_return_pct"]) for item in losses)), 0.001),
        3,
    ) if wins else 0.0
    positive_month_ratio = _positive_month_ratio(resolved)
    portfolio_max_drawdown = _equity_max_drawdown(returns)
    strategy_cum = _compound_return(returns)
    benchmark_cum = _compound_return(benchmark_returns)
    false_positive_notes = _false_positive_notes(resolved)
    accuracy = round(accurate_count / trade_count, 3) if trade_count else 0.0
    confidence = "low" if trade_count < MIN_SIGNAL_SAMPLE else "normal"
    return {
        "total_recommendations": trade_count,
        "trade_count": trade_count,
        "triggered_count": trade_count,
        "labeled_count": trade_count,
        "trigger_rate": round(1.0 if trade_count else 0.0, 3),
        "success_count": accurate_count,
        "hit_rate": accuracy,
        "recommendation_accuracy": accuracy,
        "average_net_return": avg_return,
        "avg_return": avg_return,
        "avg_executed_return": avg_return,
        "avg_weighted_return": avg_return,
        "median_net_return": median_return,
        "profit_loss_ratio": profit_loss_ratio,
        "profit_factor": profit_factor,
        "win_loss_ratio": round(len(wins) / max(len(losses), 1), 3) if wins else 0.0,
        "avg_close_return": round(sum(gross_returns) / trade_count, 3) if trade_count else 0.0,
        "avg_max_return": round(sum(achievable_returns) / trade_count, 3) if trade_count else 0.0,
        "avg_achievable_return": round(sum(achievable_returns) / trade_count, 3) if trade_count else 0.0,
        "max_drawdown": round(portfolio_max_drawdown, 3),
        "avg_max_drawdown": round(sum(trade_drawdowns) / trade_count, 3) if trade_count else 0.0,
        "avg_holding_days": round(sum(holding_days) / trade_count, 3) if trade_count else 0.0,
        "trade_positive_count": len(wins),
        "trade_negative_count": len(losses),
        "positive_month_ratio": positive_month_ratio,
        "benchmark_excess_return": round(strategy_cum - benchmark_cum, 3),
        "strategy_compound_return": round(strategy_cum, 3),
        "benchmark_compound_return": round(benchmark_cum, 3),
        "false_positive_notes": false_positive_notes,
        "sample_confidence": confidence,
        "low_confidence": confidence == "low",
    }


def _build_walk_forward_analysis(signal_dates: list[str], trades: list[dict[str, Any]]) -> dict[str, Any]:
    if len(signal_dates) < 3:
        return {
            "eligible": False,
            "window_count": 0,
            "min_train_dates": 0,
            "test_aggregate": _empty_metrics(),
            "windows": [],
            "notes": ["信号日不足 3 天，无法形成时间顺序滚动验证窗口。"],
        }

    min_train_dates = min(WALK_FORWARD_MIN_TRAIN_DATES, len(signal_dates) - 1)
    windows: list[dict[str, Any]] = []
    aggregate_test_trades: list[dict[str, Any]] = []
    for split_index in range(min_train_dates, len(signal_dates)):
        train_dates = set(signal_dates[:split_index])
        test_dates = {signal_dates[split_index]}
        train_trades = [item for item in trades if item["trade_date"] in train_dates]
        test_trades = [item for item in trades if item["trade_date"] in test_dates]
        aggregate_test_trades.extend(test_trades)
        windows.append(
            {
                "train_start": signal_dates[0],
                "train_end": signal_dates[split_index - 1],
                "test_start": signal_dates[split_index],
                "test_end": signal_dates[split_index],
                "train": _build_metrics(train_trades),
                "test": _build_metrics(test_trades),
            }
        )

    notes: list[str] = []
    notes.append(f"滚动验证按最少 {min_train_dates} 个训练信号日构建 expanding windows。")
    if not aggregate_test_trades:
        notes.append("滚动窗口已生成，但样本外窗口内尚未形成可评估交易。")
    return {
        "eligible": bool(windows),
        "window_count": len(windows),
        "min_train_dates": min_train_dates,
        "test_aggregate": _build_metrics(aggregate_test_trades),
        "windows": windows,
        "notes": notes,
    }


def _choose_variant(
    signal_dates: list[str],
    comparisons: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    baseline_walk = comparisons["baseline"]["walk_forward"]["test_aggregate"]
    candidate_walk = comparisons["candidate"]["walk_forward"]["test_aggregate"]
    use_walk_forward = baseline_walk["trade_count"] > 0 and candidate_walk["trade_count"] > 0
    baseline_test = baseline_walk if use_walk_forward else comparisons["baseline"]["split_metrics"]["test"]
    candidate_test = candidate_walk if use_walk_forward else comparisons["candidate"]["split_metrics"]["test"]
    scope_label = "滚动样本外" if use_walk_forward else "单次时间切分"
    prefix = "" if use_walk_forward else "滚动样本外可比样本不足，暂按单次时间切分判断。"
    if candidate_test["trade_count"] == 0:
        return "baseline", f"{prefix}增强版在{scope_label}没有形成可评估交易，保留基线版。".strip()
    if _variant_outperforms(candidate_test, baseline_test):
        return "candidate", f"{prefix}增强版在{scope_label}准确率更高，且平均净收益与回撤未明显恶化，采用增强版。".strip()
    return "baseline", f"{prefix}增强版在{scope_label}优势不足或回撤代价偏大，保留基线版。".strip()


def _variant_outperforms(candidate_metrics: dict[str, Any], baseline_metrics: dict[str, Any]) -> bool:
    return (
        candidate_metrics["recommendation_accuracy"] > baseline_metrics["recommendation_accuracy"] + 0.02
        and candidate_metrics["average_net_return"] >= baseline_metrics["average_net_return"] - 0.10
        and candidate_metrics["max_drawdown"] >= baseline_metrics["max_drawdown"] - 0.50
    )


def _load_stock_minutes(
    db: Session,
    cache: dict[tuple[str, str], list[dict[str, Any]]],
    code: str,
    trade_date: str,
    minute_source_trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    key = (code, trade_date)
    if key in cache:
        _record_minute_source_trace(
            minute_source_trace,
            source="local_memory_cache",
            code=code,
            status="success",
            row_count=len(cache[key]),
            scope="stock",
        )
        return cache[key]

    rows = db.scalars(
        select(MinuteKbar)
        .where(
            MinuteKbar.code == code,
            MinuteKbar.trade_time >= f"{trade_date} 00:00:00",
            MinuteKbar.trade_time < f"{_next_date(trade_date)} 00:00:00",
        )
        .order_by(MinuteKbar.trade_time.asc())
    ).all()
    if rows:
        _record_minute_source_trace(
            minute_source_trace,
            source="local_minute_db",
            code=code,
            status="success",
            row_count=len(rows),
            scope="stock",
        )
    if not rows:
        recent_rows = fetch_minute_kbars(code, trade_date=trade_date, trace=minute_source_trace)
        if recent_rows:
            _persist_minute_rows(db, recent_rows)
            rows = db.scalars(
                select(MinuteKbar)
                .where(
                    MinuteKbar.code == code,
                    MinuteKbar.trade_time >= f"{trade_date} 00:00:00",
                    MinuteKbar.trade_time < f"{_next_date(trade_date)} 00:00:00",
                )
                .order_by(MinuteKbar.trade_time.asc())
            ).all()
            _record_minute_source_trace(
                minute_source_trace,
                source="local_minute_db_after_fetch",
                code=code,
                status="success" if rows else "empty",
                row_count=len(rows),
                scope="stock",
            )
        else:
            _record_minute_source_trace(
                minute_source_trace,
                source="external_minute_fetch",
                code=code,
                status="empty",
                row_count=0,
                scope="stock",
            )

    cache[key] = [
        {
            "code": row.code,
            "trade_date": row.trade_date,
            "trade_time": row.trade_time,
            "open": _num(row.open),
            "close": _num(row.close),
            "high": _num(row.high),
            "low": _num(row.low),
            "volume": _num(row.volume),
            "amount": _num(row.amount),
        }
        for row in rows
    ]
    return cache[key]


def _load_benchmark_bundle(
    db: Session,
    cache: dict[str, dict[str, Any]],
    signal_dates: list[str] | None = None,
    date_lookup: list[str] | None = None,
    minute_source_trace: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    required_dates = _benchmark_required_dates(signal_dates or [], date_lookup or signal_dates or [])
    cache_key = ",".join(required_dates) or "recent"
    if cache.get("cache_key") == cache_key:
        rows = list(cache.get("bundle", {}).get("rows") or [])
        _record_minute_source_trace(
            minute_source_trace,
            source="local_memory_cache",
            code=BENCHMARK_CODE,
            status="success" if rows else "empty",
            row_count=len(rows),
            scope="benchmark",
        )
        return rows

    local_rows = _query_minute_rows(db, BENCHMARK_CODE, min(required_dates), max(required_dates)) if required_dates else []
    covered_dates = {str(row.get("trade_date") or "") for row in local_rows}
    if not required_dates or required_dates.issubset(covered_dates):
        cache["cache_key"] = cache_key
        cache["bundle"] = {"rows": local_rows}
        _record_minute_source_trace(
            minute_source_trace,
            source="local_minute_db",
            code=BENCHMARK_CODE,
            status="success" if local_rows else "empty",
            row_count=len(local_rows),
            scope="benchmark",
        )
        return list(local_rows)

    ndays = max(8, len(required_dates) + 5)
    fetched_rows = fetch_recent_index_minute_kbars(BENCHMARK_CODE, ndays=ndays, trace=minute_source_trace)
    if fetched_rows:
        _persist_minute_rows(db, fetched_rows)
    refreshed_rows = _query_minute_rows(db, BENCHMARK_CODE, min(required_dates), max(required_dates)) if required_dates else fetched_rows
    if not refreshed_rows and fetched_rows:
        refreshed_rows = fetched_rows
    cache["cache_key"] = cache_key
    cache["bundle"] = {"rows": refreshed_rows}
    _record_minute_source_trace(
        minute_source_trace,
        source="benchmark_bundle",
        code=BENCHMARK_CODE,
        status="success" if refreshed_rows else "empty",
        row_count=len(refreshed_rows),
        scope="benchmark",
    )
    return list(refreshed_rows)


def _query_minute_rows(db: Session, code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(MinuteKbar)
        .where(
            MinuteKbar.code == code,
            MinuteKbar.trade_time >= f"{start_date} 00:00:00",
            MinuteKbar.trade_time < f"{_next_date(end_date)} 00:00:00",
        )
        .order_by(MinuteKbar.trade_time.asc())
    ).all()
    return [
        {
            "code": row.code,
            "trade_date": row.trade_date,
            "trade_time": row.trade_time,
            "open": _num(row.open),
            "close": _num(row.close),
            "high": _num(row.high),
            "low": _num(row.low),
            "volume": _num(row.volume),
            "amount": _num(row.amount),
        }
        for row in rows
    ]


def _benchmark_required_dates(signal_dates: list[str], date_lookup: list[str]) -> set[str]:
    lookup_index = {trade_date: idx for idx, trade_date in enumerate(date_lookup)}
    required = set(signal_dates)
    for trade_date in signal_dates:
        idx = lookup_index.get(trade_date)
        if idx is not None and idx + 1 < len(date_lookup):
            required.add(date_lookup[idx + 1])
    return required


def _persist_minute_rows(db: Session, rows: list[dict[str, Any]]) -> None:
    payload = []
    for row in rows:
        trade_time = str(row.get("trade_time") or "").strip()
        trade_date = _normalize_trade_date(row.get("trade_date") or trade_time)
        if not trade_time or not trade_date:
            continue
        payload.append(
            {
                "code": str(row.get("code") or ""),
                "trade_date": trade_date,
                "trade_time": trade_time if len(trade_time) >= 19 else f"{trade_time}:00",
                "open": _num(row.get("open")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "close": _num(row.get("close")),
                "volume": _num(row.get("volume")),
                "amount": _num(row.get("amount")),
            }
        )
    if not payload:
        return
    stmt = mysql_insert(MinuteKbar).values(payload).prefix_with("IGNORE")
    db.execute(stmt)
    db.commit()


def _persist_backtest_run(db: Session, start_date: str, end_date: str, result: dict[str, Any]) -> None:
    row = BacktestRun(
        backtest_id=f"bt_{STRATEGY_ID}_{uuid.uuid4().hex[:10]}",
        skill_id=STRATEGY_ID,
        date_from=start_date,
        date_to=end_date,
        status="completed",
        result_json=json.dumps(result, ensure_ascii=False),
    )
    db.add(row)
    db.commit()


def _local_minute_inventory(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(
            func.min(MinuteKbar.trade_date),
            func.max(MinuteKbar.trade_date),
            func.count(func.distinct(MinuteKbar.trade_date)),
            func.count(func.distinct(MinuteKbar.code)),
        )
    ).one()
    start_date, end_date, trade_date_count, code_count = row
    return {
        "trade_date_start": str(start_date or ""),
        "trade_date_end": str(end_date or ""),
        "trade_date_count": int(trade_date_count or 0),
        "code_count": int(code_count or 0),
    }


def _build_data_coverage(
    signal_dates: list[str],
    date_summaries: list[dict[str, Any]],
    minute_inventory: dict[str, Any],
    minute_source_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    total_candidates = sum(int(item.get("candidate_count") or 0) for item in date_summaries)
    minute_ready_candidates = sum(int(item.get("minute_ready_candidate_count") or 0) for item in date_summaries)
    benchmark_ready_dates = sum(1 for item in date_summaries if item.get("benchmark_minute_covered"))
    missing_examples: list[str] = []
    for item in date_summaries:
        trade_date = str(item.get("trade_date") or "")
        for code in item.get("missing_minute_codes") or []:
            if len(missing_examples) >= 8:
                break
            missing_examples.append(f"{trade_date} {code}")
    return {
        "signal_date_count": len(signal_dates),
        "candidate_count": total_candidates,
        "minute_ready_candidate_count": minute_ready_candidates,
        "minute_missing_candidate_count": max(0, total_candidates - minute_ready_candidates),
        "candidate_minute_coverage_rate": round(minute_ready_candidates / total_candidates, 3) if total_candidates else 0.0,
        "benchmark_minute_covered_dates": benchmark_ready_dates,
        "benchmark_minute_coverage_rate": round(benchmark_ready_dates / len(signal_dates), 3) if signal_dates else 0.0,
        "missing_minute_examples": missing_examples,
        "local_minute_inventory": minute_inventory,
        "minute_source_diagnostics": _summarize_minute_source_trace(minute_source_trace or []),
    }


def _record_minute_source_trace(
    trace: list[dict[str, Any]] | None,
    *,
    source: str,
    code: str,
    status: str,
    row_count: int,
    scope: str,
    reason: str | None = None,
) -> None:
    if trace is None:
        return
    trace.append(
        {
            "source": source,
            "code": code,
            "status": status,
            "row_count": int(row_count or 0),
            "scope": scope,
            "reason": (reason or "")[:160],
        }
    )


def _summarize_minute_source_trace(trace: list[dict[str, Any]]) -> dict[str, Any]:
    source_map: dict[tuple[str, str], dict[str, Any]] = {}
    success_sources: set[str] = set()
    fallback_success_sources: set[str] = set()
    tushare_statuses: set[str] = set()
    samples: list[dict[str, Any]] = []
    for item in trace:
        source = str(item.get("source") or "unknown")
        status = str(item.get("status") or "unknown")
        scope = str(item.get("scope") or "external")
        key = (source, status)
        bucket = source_map.setdefault(
            key,
            {
                "source": source,
                "status": status,
                "scope": scope,
                "attempt_count": 0,
                "row_count": 0,
                "example_reason": "",
            },
        )
        bucket["attempt_count"] += 1
        bucket["row_count"] += int(item.get("row_count") or 0)
        if item.get("reason") and not bucket["example_reason"]:
            bucket["example_reason"] = str(item.get("reason") or "")
        if status == "success" and int(item.get("row_count") or 0) > 0:
            success_sources.add(source)
            if source not in {"tushare_stk_mins", "local_memory_cache", "local_minute_db", "local_minute_db_after_fetch"}:
                fallback_success_sources.add(source)
        if source == "tushare_stk_mins":
            tushare_statuses.add(status)
        if len(samples) < 8 and (status != "success" or source == "tushare_stk_mins"):
            samples.append(
                {
                    "source": source,
                    "status": status,
                    "code": item.get("code"),
                    "reason": item.get("reason") or "",
                }
            )
    by_source = sorted(
        source_map.values(),
        key=lambda row: (str(row["source"]), str(row["status"])),
    )
    tushare_permission_denied = bool({"permission_denied", "skipped"} & tushare_statuses)
    return {
        "attempt_count": len(trace),
        "by_source": by_source,
        "successful_sources": sorted(success_sources),
        "fallback_used": bool(fallback_success_sources),
        "tushare_stk_mins_status": ",".join(sorted(tushare_statuses)) if tushare_statuses else "not_attempted",
        "tushare_permission_denied": tushare_permission_denied,
        "samples": samples,
    }


def _group_minutes_by_date(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("trade_date") or "")].append(row)
    return {key: sorted(value, key=lambda item: str(item["trade_time"])) for key, value in grouped.items()}


def _minute_at_or_after(rows: list[dict[str, Any]], hhmmss: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row["trade_time"])[11:19] >= hhmmss:
            return row
    return rows[-1] if rows else None


def _benchmark_row_for_entry(rows: list[dict[str, Any]], trade_time: str) -> dict[str, Any] | None:
    hhmm = str(trade_time)[11:16]
    for row in rows:
        if str(row["trade_time"])[11:16] >= hhmm:
            return row
    return rows[-1] if rows else None


def _positive_month_ratio(trades: list[dict[str, Any]]) -> float:
    by_month: dict[str, float] = defaultdict(float)
    for item in trades:
        month = str(item.get("trade_date") or "")[:7]
        if month:
            by_month[month] += float(item.get("net_return_pct") or 0.0)
    if not by_month:
        return 0.0
    positive = sum(1 for value in by_month.values() if value > 0)
    return round(positive / len(by_month), 3)


def _compound_return(returns: list[float]) -> float:
    equity = 1.0
    for value in returns:
        equity *= 1 + float(value) / 100.0
    return (equity - 1) * 100.0


def _equity_max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + float(value) / 100.0
        peak = max(peak, equity)
        if peak <= 0:
            continue
        drawdown = (equity - peak) / peak * 100.0
        max_drawdown = min(max_drawdown, drawdown)
    return max_drawdown


def _trade_samples(trades: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    ordered = sorted(
        trades,
        key=lambda item: (item["trade_date"], item["entry_time"], item["code"]),
    )
    return _recommendation_records(ordered[:limit])


def _recommendation_records(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "trade_date": item["trade_date"],
            "code": item["code"],
            "name": item["name"],
            "recommendation_time": item["recommendation_time"],
            "entry_time": item["entry_time"],
            "entry_price": item["entry_price"],
            "exit_date": item["exit_date"],
            "exit_time": item["exit_time"],
            "exit_price": item["exit_price"],
            "net_return_pct": item["net_return_pct"],
            "gross_return_pct": item["gross_return_pct"],
            "benchmark_return_pct": item.get("benchmark_return_pct"),
            "accurate": item["accurate"],
            "conditions": item["conditions"],
            "exit_reason": item["exit_reason"],
            "false_positive_reason": item.get("false_positive_reason"),
        }
        for item in trades
    ]


def _false_positive_samples(trades: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    losers = [item for item in trades if item.get("resolved") and not item.get("accurate")]
    losers.sort(key=lambda item: (item.get("net_return_pct") or 0.0))
    return [
        {
            "trade_date": item["trade_date"],
            "code": item["code"],
            "name": item["name"],
            "entry_time": item["entry_time"],
            "exit_time": item["exit_time"],
            "net_return_pct": item["net_return_pct"],
            "false_positive_reason": item["false_positive_reason"],
            "conditions": item["conditions"],
        }
        for item in losers[:limit]
    ]


def _variant_notes(
    signal_dates: list[str],
    trades: list[dict[str, Any]],
    variant: OvernightVariant,
) -> list[str]:
    notes = [
        f"策略按尾盘 14:30 后扫描，分时均线采用分钟累计 VWAP，站线稳定度只统计 14:30 后窗口，基准采用 {BENCHMARK_NAME} 分时涨幅。",
        f"当前版本的“强于大盘”阈值：相对 {BENCHMARK_NAME} 至少领先 {variant.min_relative_strength_pct:.1f} 个百分点。",
        f"第 8 条“回踩均线不跌破”工程化为：尾盘窗口内最低价始终不破 VWAP，且与 VWAP 的最大偏离不超过 {TAIL_VWAP_MAX_GAP_BPS / 100:.1f}%。",
        f"次日早盘卖出工程定义为 {MORNING_EXIT_TIME} 的首个可成交分钟收盘价；若出现一字涨停或缩量涨停，则递延到下一可卖早盘。",
    ]
    if len(trades) < MIN_SIGNAL_SAMPLE:
        notes.append("当前样本数低于 30 笔，按规则标记为低置信度。")
    if signal_dates:
        notes.append(f"本次仅验证可取到分钟线/次日数据的区间：{signal_dates[0]} 至 {signal_dates[-1]}。")
    return notes


def _false_positive_notes(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    example_map: dict[str, list[str]] = defaultdict(list)
    for item in trades:
        if item.get("accurate"):
            continue
        reason = str(item.get("false_positive_reason") or "成本后净收益不为正")
        counter[reason] += 1
        if len(example_map[reason]) < 3:
            example_map[reason].append(f"{item['trade_date']} {item['code']} {item['net_return_pct']:.2f}%")
    return [
        {"reason": reason, "count": count, "examples": example_map.get(reason, [])}
        for reason, count in counter.most_common(5)
    ]


def _false_positive_reason(
    signal: dict[str, Any],
    exit_daily_bar: DailyKbar,
    net_return_pct: float,
    benchmark_return_pct: float,
) -> str:
    if _num(exit_daily_bar.open) < signal["entry_price"]:
        return "次日开盘承接偏弱，隔夜溢价被回吐"
    if benchmark_return_pct > net_return_pct:
        return "个股虽强于大盘但超额收益不足覆盖成本"
    return "早盘兑现后净收益不为正"


def _is_main_board(code: str) -> bool:
    if code.startswith(("300", "301", "688", "689", "8", "4", "9")):
        return False
    return code.startswith(("000", "001", "002", "003", "600", "601", "603", "605"))


def _is_limit_up_bar(bar: DailyKbar) -> bool:
    return _num(bar.pct_chg) >= 9.8


def _is_one_word_limit_up(bar: DailyKbar, up_limit: float) -> bool:
    return _num(bar.open) >= up_limit * 0.995 and _num(bar.low) >= up_limit * 0.995


def _is_shrinking_limit_up(bar: DailyKbar, up_limit: float, signal_day_volume: float) -> bool:
    return (
        _num(bar.close) >= up_limit * 0.995
        and _num(bar.high) >= up_limit * 0.995
        and _num(bar.volume) > 0
        and signal_day_volume > 0
        and _num(bar.volume) <= signal_day_volume * SHRINK_LIMIT_VOLUME_RATIO
    )


def _main_board_up_limit(prev_close: float) -> float:
    return round(prev_close * (1 + LIMIT_UP_PCT), 2)


def _apply_entry_cost(price: float, cost: CostAssumption) -> float:
    return price * (1 + (cost.buy_commission_bps + cost.buy_slippage_bps) / 10000.0)


def _apply_exit_cost(price: float, cost: CostAssumption) -> float:
    return price * (1 - (cost.sell_commission_bps + cost.sell_stamp_tax_bps + cost.sell_slippage_bps) / 10000.0)


def _pct_change(base: float, target: float) -> float:
    if base <= 0:
        return 0.0
    return (target - base) / base * 100.0


def _empty_metrics() -> dict[str, Any]:
    return {
        "total_recommendations": 0,
        "trade_count": 0,
        "triggered_count": 0,
        "labeled_count": 0,
        "trigger_rate": 0.0,
        "success_count": 0,
        "hit_rate": 0.0,
        "recommendation_accuracy": 0.0,
        "average_net_return": 0.0,
        "avg_return": 0.0,
        "avg_executed_return": 0.0,
        "avg_weighted_return": 0.0,
        "median_net_return": 0.0,
        "profit_loss_ratio": 0.0,
        "profit_factor": 0.0,
        "win_loss_ratio": 0.0,
        "avg_close_return": 0.0,
        "avg_max_return": 0.0,
        "avg_achievable_return": 0.0,
        "avg_holding_days": 0.0,
        "max_drawdown": 0.0,
        "avg_max_drawdown": 0.0,
        "positive_month_ratio": 0.0,
        "benchmark_excess_return": 0.0,
        "strategy_compound_return": 0.0,
        "benchmark_compound_return": 0.0,
        "false_positive_notes": [],
        "sample_confidence": "low",
        "low_confidence": True,
    }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


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


def _next_date(trade_date: str) -> str:
    return (date.fromisoformat(trade_date) + timedelta(days=1)).isoformat()

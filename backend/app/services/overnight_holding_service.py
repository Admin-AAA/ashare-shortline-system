from __future__ import annotations

import json
import logging
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
from app.engine.board_rules import (
    is_one_word_limit_up_open_low as _is_one_word_limit_up,
    main_board_up_limit as _main_board_up_limit,
)
from app.services.evaluation_metrics import (
    MIN_SIGNAL_SAMPLE as _SHARED_MIN_SAMPLE,
    compute_full_metrics,
    _empty_metrics as _shared_empty_metrics,
)
from app.services.minute_data_prep import (
    build_data_coverage as _build_data_coverage,
    compare_minute_gap_remediation as _compare_minute_gap_remediation,
    local_minute_inventory as _local_minute_inventory,
    normalize_trade_date as _normalize_trade_date,
    record_minute_source_trace as _record_minute_source_trace,
)
from app.services.tushare_client import (
    fetch_index_minute_kbars,
    fetch_minute_kbars,
    fetch_recent_index_minute_kbars,
    get_pro,
)
from app.services.temporal_consistency import check_run_temporal_consistency, check_backtest_temporal_consistency

logger = logging.getLogger(__name__)

# ── 分钟数据源优先级 ──
# 15min 优先（全市场已回填），1min 兜底（历史存量），FORCE_LOCAL 关闭 Tushare API 回退
_MINUTE_FREQ_PRIORITY: list[str] = ["15min", "1min"]
_FORCE_LOCAL_ONLY: bool = True  # 回测/信号日扫描时只读本地 DB，不调 Tushare
MINUTE_GATE_MIN_COVERAGE_RATE = 0.8
MINUTE_GATE_MIN_READY_CANDIDATES = 10

STRATEGY_ID = "overnight-holding-method"
STRATEGY_NAME = "隔夜持股法"
BENCHMARK_CODE = "000300"
BENCHMARK_NAME = "沪深300"
TAIL_START_TIME = "14:30:00"
MORNING_EXIT_TIME = "09:35:00"
VWAP_HOLD_TOLERANCE_BPS = 12.0
TAIL_VWAP_MAX_GAP_BPS = 500.0
TAIL_RETEST_WINDOW_MINUTES = 15
SHRINK_LIMIT_VOLUME_RATIO = 0.85
DEFAULT_MIN_ABOVE_VWAP_RATIO = 0.70
MIN_PCT = 2.5
MAX_PCT = 5.5
MIN_TURNOVER = 3.0
MAX_TURNOVER = 25.0
MIN_MARKET_CAP_YI = 30.0
MAX_MARKET_CAP_YI = 500.0
MIN_INTRADAY_VOLUME_RATIO = 0.6
WALK_FORWARD_MIN_TRAIN_DATES = 2
MIN_RECENT_LIMIT_UP_COUNT = 0  # 信号条件本身做质量守门；涨停预过滤交给信号层


@dataclass(frozen=True)
class CostAssumption:
    """交易成本假设（A股标准费率）。

    单边成本（买入）：佣金0.01% + 滑点0.1% = 0.11%
    单边成本（卖出）：印花税0.05% + 佣金0.01% + 滑点0.1% = 0.16%
    往返成本总计：0.11% + 0.16% = 0.27%
    """
    buy_commission_bps: float = 1.0     # 佣金 0.01%（万1）
    buy_slippage_bps: float = 10.0      # 滑点 0.1%
    sell_commission_bps: float = 1.0    # 佣金 0.01%（万1）
    sell_stamp_tax_bps: float = 5.0    # 印花税 0.05% (仅卖出)
    sell_slippage_bps: float = 10.0    # 滑点 0.1%

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
    exit_time: str = MORNING_EXIT_TIME
    min_pct: float = MIN_PCT
    max_pct: float = MAX_PCT


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

DELAYED_EXIT_VARIANT = OvernightVariant(
    name="delayed_exit",
    label="延迟退出版",
    min_relative_strength_pct=0.8,
    exit_time="09:45:00",
)

RELAXED_PCT_VARIANT = OvernightVariant(
    name="relaxed_pct",
    label="放宽版",
    min_relative_strength_pct=0.0,
    min_pct=2.0,
    max_pct=7.0,
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
    gate = _evaluate_preflight_minute_gate(
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
    )
    if not gate["passed"]:
        blocked_response = {
            "strategy_id": STRATEGY_ID,
            "strategy_name": STRATEGY_NAME,
            "status": "insufficient_minute_coverage",
            "strategy_window": {"start_date": start, "end_date": end},
            "dates_scanned": len(signal_dates),
            "dates_succeeded": 0,
            "dates_failed": len(signal_dates),
            "total_recommendations": 0,
            "total_elapsed_ms": int((perf_counter() - started_at) * 1000),
            "labeled_count": 0,
            "aggregate_metrics": _empty_metrics(),
            "results": gate["date_summaries"],
            "data_coverage": _build_data_coverage(
                signal_dates,
                gate["date_summaries"],
                minute_inventory,
                minute_source_trace,
            ),
            "minute_gate": gate,
            "recommendation_records": [],
            "trade_samples": [],
            "false_positive_samples": [],
            "notes": [
                "分钟线先验覆盖不足，已阻止本次隔夜回测，避免基于残缺样本输出结论。",
                *gate["notes"],
            ],
        }
        _persist_backtest_run(db, start, end, blocked_response, status="insufficient_minute_coverage")
        return blocked_response

    comparisons: dict[str, dict[str, Any]] = {}
    for variant in (BASELINE_VARIANT, CANDIDATE_VARIANT, DELAYED_EXIT_VARIANT, RELAXED_PCT_VARIANT):
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
            "delayed_exit": comparisons["delayed_exit"]["metrics"],
            "relaxed_pct": comparisons["relaxed_pct"]["metrics"],
            "selected_variant": adopted_variant,
            "selected_variant_label": comparisons[adopted_variant]["variant"].label,
            "adoption_reason": adoption_reason,
            "hypothesis": "增强版：把\"强于大盘\"从弱定义（仅跑赢沪深300）收紧为强定义（至少领先沪深300 0.8 个百分点）。延迟退出版：次日早盘从09:35推迟到09:45卖出，避开开盘初期流动性冲击。放宽版：daily pct范围从[2.5%,5.5%]扩至[2.0%,7.0%]，VWAP回踩容差从300bps扩至500bps。",
            "splits": {
                "baseline": comparisons["baseline"]["split_metrics"],
                "candidate": comparisons["candidate"]["split_metrics"],
                "delayed_exit": comparisons["delayed_exit"]["split_metrics"],
                "relaxed_pct": comparisons["relaxed_pct"]["split_metrics"],
            },
            "walk_forward": {
                "baseline": comparisons["baseline"]["walk_forward"],
                "candidate": comparisons["candidate"]["walk_forward"],
                "delayed_exit": comparisons["delayed_exit"]["walk_forward"],
                "relaxed_pct": comparisons["relaxed_pct"]["walk_forward"],
            },
        },
        "data_coverage": _build_data_coverage(
            signal_dates,
            selected["date_summaries"],
            minute_inventory,
            minute_source_trace,
        ),
        "minute_gate": gate,
        "temporal_consistency": _run_overnight_temporal_check(db, signal_dates),
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


def preview_minute_gap_remediation(
    db: Session,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start = _normalize_trade_date(start_date)
    end = _normalize_trade_date(end_date)
    if not start or not end:
        raise ValueError("start_date and end_date are required")
    if start > end:
        raise ValueError("start_date must be <= end_date")

    signal_dates, date_lookup = _signal_trade_dates(db, start, end)
    if not signal_dates:
        return {
            "status": "no_data",
            "strategy_window": {"start_date": start, "end_date": end},
            "minute_gate": {
                "passed": False,
                "reason": "区间内没有可用于隔夜策略验证的交易日。",
                "notes": ["至少需要存在可入场日及其后续交易日。"],
            },
            "data_coverage": _build_data_coverage([], [], _local_minute_inventory(db), []),
        }

    window_start = date_lookup[max(0, date_lookup.index(signal_dates[0]) - 25)]
    window_end = date_lookup[min(len(date_lookup) - 1, date_lookup.index(signal_dates[-1]) + 5)]
    daily_by_date, history_by_code = _load_daily_window(db, window_start, window_end)
    stock_meta = _load_stock_meta(db, history_by_code.keys())
    daily_basic_cache: dict[str, dict[str, dict[str, float]]] = {}
    minute_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    benchmark_cache: dict[str, dict[str, Any]] = {}
    minute_source_trace: list[dict[str, Any]] = []
    gate = _evaluate_preflight_minute_gate(
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
    )
    return {
        "status": "ready" if gate["passed"] else "insufficient_minute_coverage",
        "strategy_window": {"start_date": start, "end_date": end},
        "minute_gate": gate,
        "data_coverage": _build_data_coverage(
            signal_dates,
            gate["date_summaries"],
            _local_minute_inventory(db),
            minute_source_trace,
        ),
    }


def remediate_minute_gaps(
    db: Session,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    start = _normalize_trade_date(start_date)
    end = _normalize_trade_date(end_date)
    before = preview_minute_gap_remediation(db, start_date, end_date)
    gate_before = before["minute_gate"]
    if gate_before.get("passed"):
        return {
            "status": "already_ready",
            "strategy_id": STRATEGY_ID,
            "strategy_name": STRATEGY_NAME,
            "strategy_window": {"start_date": start, "end_date": end},
            "minute_gate": before.get("minute_gate"),
            "data_coverage": before.get("data_coverage"),
            "before": before,
            "after": before,
            "actions": [],
            "comparison": _compare_minute_gap_remediation(before, before),
        }

    actions = _execute_minute_gap_remediation(
        db,
        gate_before,
    )
    after = preview_minute_gap_remediation(db, start_date, end_date)
    comparison = _compare_minute_gap_remediation(before, after)
    return {
        "status": "completed" if actions else "no_action",
        "strategy_id": STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "strategy_window": {"start_date": start, "end_date": end},
        "minute_gate": after.get("minute_gate"),
        "data_coverage": after.get("data_coverage"),
        "before": before,
        "after": after,
        "actions": actions,
        "comparison": comparison,
    }


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
    mapping: dict[str, dict[str, float]] = {}
    try:
        pro = get_pro()
        df = pro.daily_basic(
            trade_date=trade_date.replace("-", ""),
            fields="ts_code,trade_date,turnover_rate,total_mv,circ_mv",
        )
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
    except Exception as e:
        logger.warning("daily_basic 获取失败 %s: %s — 降级跳过换手率/市值过滤", trade_date, e)
    cache[trade_date] = mapping
    return mapping


def _evaluate_preflight_minute_gate(
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
) -> dict[str, Any]:
    benchmark_required_dates = sorted(_benchmark_required_dates(signal_dates, date_lookup))
    benchmark_bundle = _load_benchmark_bundle(db, benchmark_cache, signal_dates, date_lookup, minute_source_trace)
    benchmark_by_date = _group_minutes_by_date(benchmark_bundle)
    benchmark_missing_dates = [
        trade_date for trade_date in benchmark_required_dates
        if not benchmark_by_date.get(trade_date)
    ]

    total_candidates = 0
    minute_ready_candidates = 0
    per_date_missing_codes: dict[str, list[str]] = {}
    date_summaries: list[dict[str, Any]] = []
    missing_pairs: list[dict[str, str]] = []

    for trade_date in signal_dates:
        basic_map = _load_daily_basic_map(trade_date, daily_basic_cache)
        filter_trace: dict[str, int] = {}
        candidates = _daily_candidates(
            trade_date=trade_date,
            daily_rows=daily_by_date.get(trade_date, {}),
            history_by_code=history_by_code,
            stock_meta=stock_meta,
            daily_basic_map=basic_map,
            filter_trace=filter_trace,
            variant=BASELINE_VARIANT,
        )
        total_candidates += len(candidates)
        missing_codes: list[str] = []
        missing_count = 0
        for candidate in candidates:
            code = candidate["code"]
            minutes = _load_stock_minutes(db, minute_cache, code, trade_date, minute_source_trace)
            if minutes:
                minute_ready_candidates += 1
                continue
            missing_count += 1
            if len(missing_codes) < 8:
                missing_codes.append(code)
            if len(missing_pairs) < 50:
                missing_pairs.append({"trade_date": trade_date, "code": code})
        if missing_codes:
            per_date_missing_codes[trade_date] = missing_codes
        date_summaries.append(
            {
                "trade_date": trade_date,
                "status": "precheck",
                "candidate_count": len(candidates),
                "minute_ready_candidate_count": max(0, len(candidates) - missing_count),
                "minute_missing_candidate_count": missing_count,
                "benchmark_minute_covered": bool(benchmark_by_date.get(trade_date)),
                "missing_minute_codes": missing_codes,
                "filter_trace": filter_trace,
            }
        )

    coverage_rate = round(minute_ready_candidates / total_candidates, 3) if total_candidates else 0.0
    benchmark_coverage_rate = round(
        (len(benchmark_required_dates) - len(benchmark_missing_dates)) / len(benchmark_required_dates),
        3,
    ) if benchmark_required_dates else 0.0

    reasons: list[str] = []
    if benchmark_missing_dates:
        reasons.append(f"基准指数分钟线缺失 {len(benchmark_missing_dates)} 个必要交易日。")
    if total_candidates and coverage_rate < MINUTE_GATE_MIN_COVERAGE_RATE:
        reasons.append(
            f"候选池分钟线覆盖率仅 {coverage_rate:.1%}，低于 {MINUTE_GATE_MIN_COVERAGE_RATE:.0%} 门槛。"
        )
    if total_candidates and minute_ready_candidates < MINUTE_GATE_MIN_READY_CANDIDATES:
        reasons.append(
            f"分钟线可验证候选仅 {minute_ready_candidates} 笔，低于 {MINUTE_GATE_MIN_READY_CANDIDATES} 笔最低样本要求。"
        )
    if total_candidates == 0:
        reasons.append("日线预筛后没有候选池，无法形成分钟线门禁判断。")

    passed = total_candidates > 0 and not reasons
    notes = [
        f"先验扫描到 {total_candidates} 个候选，分钟线就绪 {minute_ready_candidates} 个，覆盖率 {coverage_rate:.1%}。",
        f"基准分钟线必要日期 {len(benchmark_required_dates)} 个，已覆盖 {len(benchmark_required_dates) - len(benchmark_missing_dates)} 个，覆盖率 {benchmark_coverage_rate:.1%}。",
    ]
    if missing_pairs:
        notes.append(
            "样例缺口: " + ", ".join(f"{item['trade_date']} {item['code']}" for item in missing_pairs[:6])
        )

    return {
        "passed": passed,
        "reason": "分钟线覆盖满足先验门禁。" if passed else "；".join(reasons),
        "coverage_rate": coverage_rate,
        "benchmark_coverage_rate": benchmark_coverage_rate,
        "candidate_count": total_candidates,
        "minute_ready_candidate_count": minute_ready_candidates,
        "missing_pair_count": max(0, total_candidates - minute_ready_candidates),
        "missing_pair_sample_count": len(missing_pairs),
        "benchmark_required_dates": benchmark_required_dates,
        "benchmark_missing_dates": benchmark_missing_dates,
        "missing_pairs": missing_pairs,
        "missing_codes_by_date": per_date_missing_codes,
        "date_summaries": date_summaries,
        "notes": notes,
    }


def _execute_minute_gap_remediation(
    db: Session,
    gate: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    missing_pairs = gate.get("missing_pairs") or []
    if missing_pairs:
        by_date: dict[str, list[str]] = defaultdict(list)
        for item in missing_pairs:
            trade_date = str(item.get("trade_date") or "")
            code = str(item.get("code") or "")
            if trade_date and code and code not in by_date[trade_date]:
                by_date[trade_date].append(code)
        for trade_date, codes in by_date.items():
            fetched = _backfill_stock_minutes_for_date(db, codes, trade_date)
            actions.append(
                {
                    "scope": "stock",
                    "trade_date": trade_date,
                    "requested_code_count": len(codes),
                    "row_count": fetched,
                    "status": "completed" if fetched else "empty",
                }
            )

    benchmark_missing_dates = sorted(set(gate.get("benchmark_missing_dates") or []))
    if benchmark_missing_dates:
        fetched = _backfill_benchmark_minutes(db, benchmark_missing_dates)
        actions.append(
            {
                "scope": "benchmark",
                "trade_date_start": benchmark_missing_dates[0],
                "trade_date_end": benchmark_missing_dates[-1],
                "requested_date_count": len(benchmark_missing_dates),
                "row_count": fetched,
                "status": "completed" if fetched else "empty",
            }
        )
    return actions


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
        filter_trace: dict[str, int] = {}
        candidates = _daily_candidates(
            trade_date=trade_date,
            daily_rows=daily_rows,
            history_by_code=history_by_code,
            stock_meta=stock_meta,
            daily_basic_map=basic_map,
            filter_trace=filter_trace,
            variant=variant,
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
                    "filter_trace": filter_trace,
                }
            )
            continue

        signals: list[dict[str, Any]] = []
        minute_ready_candidate_count = 0
        missing_minute_codes: list[str] = []
        condition_failures: dict[str, int] = {}
        benchmark_minutes = benchmark_by_date.get(trade_date) or []
        for candidate in candidates:
            code = candidate["code"]
            minutes = _load_stock_minutes(db, minute_cache, code, trade_date, minute_source_trace)
            if not minutes:
                if len(missing_minute_codes) < 5:
                    missing_minute_codes.append(code)
                continue
            minute_ready_candidate_count += 1
            condition_failures["_candidates_scanned"] = condition_failures.get("_candidates_scanned", 0) + 1
            signal = _locate_entry_signal(
                candidate=candidate,
                minutes=minutes,
                benchmark_minutes=benchmark_minutes,
                variant=variant,
                condition_failures=condition_failures,
            )
            if signal is not None:
                signals.append(signal)

        if condition_failures:
            total_bars = condition_failures.pop("_bars_checked", 0)
            scanned = condition_failures.pop("_candidates_scanned", 0)
            sigs = condition_failures.pop("_signals_found", 0)
            sorted_fails = sorted(
                [(k, v) for k, v in condition_failures.items()],
                key=lambda x: -x[1],
            )
            logger.info(
                "diag variant=%s date=%s candidates_scanned=%d bars_checked=%d signals=%d fail_rank=%s",
                variant.name, trade_date, scanned, total_bars, sigs,
                dict(sorted_fails),
            )

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
                variant=variant,
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
                "filter_trace": filter_trace,
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
    filter_trace: dict[str, int] | None = None,
    variant: OvernightVariant | None = None,
) -> list[dict[str, Any]]:
    if filter_trace is None:
        filter_trace = {}
    trace = filter_trace
    trace.setdefault("total_raw", len(daily_rows))
    trace.setdefault("filtered_main_board", 0)
    trace.setdefault("filtered_pct_range", 0)
    trace.setdefault("filtered_turnover", 0)
    trace.setdefault("filtered_market_cap", 0)
    trace.setdefault("filtered_hist_idx", 0)
    trace.setdefault("filtered_limit_up_count", 0)
    trace.setdefault("filtered_avg_volume", 0)
    trace.setdefault("passed_candidates", 0)

    _min_pct = variant.min_pct if variant is not None else MIN_PCT
    _max_pct = variant.max_pct if variant is not None else MAX_PCT

    result: list[dict[str, Any]] = []
    for code, bar in daily_rows.items():
        meta = stock_meta.get(code) or {}
        if not _is_main_board(code) or bool(meta.get("is_st")):
            trace["filtered_main_board"] += 1
            continue
        pct = _num(bar.pct_chg)
        if pct < _min_pct or pct > _max_pct:
            trace["filtered_pct_range"] += 1
            continue
        basic = daily_basic_map.get(code)
        # daily_basic 不可用时降级：换手率过滤放宽（本地日K数据通常缺 turnover_rate）
        # 市值过滤因无法本地计算而跳过，避免因 Tushare 代理超时导致全天候选清零
        turnover_rate = _num(bar.turnover_rate, _num(basic.get("turnover_rate") if basic else None))
        if basic is not None:
            if turnover_rate < MIN_TURNOVER or turnover_rate > MAX_TURNOVER:
                trace["filtered_turnover"] += 1
                continue
            total_mv_yi = _num(basic.get("total_mv_yi"))
            if total_mv_yi < MIN_MARKET_CAP_YI or total_mv_yi > MAX_MARKET_CAP_YI:
                trace["filtered_market_cap"] += 1
                continue
        else:
            total_mv_yi = 0.0  # 占位，不参与过滤

        history = history_by_code.get(code) or []
        hist_idx = next((idx for idx, item in enumerate(history) if item.trade_date == trade_date), -1)
        if hist_idx <= 0:
            trace["filtered_hist_idx"] += 1
            continue
        recent_limit_up_count = sum(
            1 for item in history[max(0, hist_idx - 20) : hist_idx] if _is_limit_up_bar(item)
        )
        if recent_limit_up_count < MIN_RECENT_LIMIT_UP_COUNT:
            trace["filtered_limit_up_count"] += 1
            continue

        recent_volumes = [_num(item.volume) for item in history[max(0, hist_idx - 5) : hist_idx] if _num(item.volume) > 0]
        avg_prev5_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0.0
        if avg_prev5_volume <= 0:
            trace["filtered_avg_volume"] += 1
            continue

        prev_close = _num(history[hist_idx - 1].close)
        if prev_close <= 0:
            continue

        trace["passed_candidates"] += 1
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
    condition_failures: dict[str, int] | None = None,
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

        if last_new_high_index is None:
            continue
        # 修复：不再要求尾盘内必须有新高，只要日内有过新高即可。
        # 原逻辑 idx < tail_start_index 跳过导致全天有新高但不在尾盘的被排除。

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
        if condition_failures is not None:
            condition_failures["_bars_checked"] = condition_failures.get("_bars_checked", 0) + 1
        if intraday_pct < variant.min_pct or intraday_pct > variant.max_pct:
            if condition_failures is not None: condition_failures["pct_out_of_range"] = condition_failures.get("pct_out_of_range", 0) + 1
            continue
        if intraday_volume_ratio < MIN_INTRADAY_VOLUME_RATIO:
            if condition_failures is not None: condition_failures["volume_ratio_too_low"] = condition_failures.get("volume_ratio_too_low", 0) + 1
            continue
        if relative_strength < variant.min_relative_strength_pct:
            if condition_failures is not None: condition_failures["relative_strength_too_low"] = condition_failures.get("relative_strength_too_low", 0) + 1
            continue
        if above_vwap_ratio < DEFAULT_MIN_ABOVE_VWAP_RATIO:
            if condition_failures is not None and idx >= tail_start_index:
                condition_failures["above_vwap_ratio_too_low"] = condition_failures.get("above_vwap_ratio_too_low", 0) + 1
            continue
        if not hold_ok or not retest_ok or close_price < vwap:
            if condition_failures is not None and idx >= tail_start_index:
                if not hold_ok: condition_failures["hold_ok_failed"] = condition_failures.get("hold_ok_failed", 0) + 1
                if not retest_ok: condition_failures["retest_ok_failed"] = condition_failures.get("retest_ok_failed", 0) + 1
                if close_price < vwap: condition_failures["close_below_vwap"] = condition_failures.get("close_below_vwap", 0) + 1
            continue

        if condition_failures is not None:
            condition_failures["_signals_found"] = condition_failures.get("_signals_found", 0) + 1
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
    variant: OvernightVariant = BASELINE_VARIANT,
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
        exit_row = _minute_at_or_after(minutes, variant.exit_time)
        if exit_row is None and minutes:
            exit_row = minutes[0]
        exit_reason = "next_morning_exit" if next_idx == hist_idx + 1 else "deferred_after_limit_up"
        break

    if exit_daily_bar is None or exit_row is None:
        return {**signal, "resolved": False, "reason": "missing_exit_minute"}

    benchmark_exit_row = _minute_at_or_after(benchmark_by_date.get(str(exit_daily_bar.trade_date)) or [], variant.exit_time)
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


def _build_split_metrics(
    signal_dates: list[str],
    trades: list[dict[str, Any]],
    train_ratio: float = 0.6,
) -> dict[str, dict[str, Any]]:
    """按时间切分训练集/测试集。

    与 _build_walk_forward_analysis 互补：
    - walk-forward: 多个 expanding window 测试稳定性
    - time-split: 单一固定比例切分，更简洁的 train vs test 对比
    """
    if not signal_dates:
        return {
            "train": _empty_metrics(),
            "test": _empty_metrics(),
            "train_ratio": train_ratio,
        }
    split_index = max(1, int(len(signal_dates) * train_ratio))
    if split_index >= len(signal_dates):
        split_index = max(1, len(signal_dates) - 1)
    train_dates = set(signal_dates[:split_index])
    test_dates = set(signal_dates[split_index:])
    train_trades = [item for item in trades if item["trade_date"] in train_dates]
    test_trades = [item for item in trades if item["trade_date"] in test_dates]
    return {
        "train": _build_metrics(train_trades),
        "test": _build_metrics(test_trades),
        "train_ratio": train_ratio,
        "train_date_range": {
            "start": signal_dates[0],
            "end": signal_dates[split_index - 1],
        },
        "test_date_range": {
            "start": signal_dates[split_index],
            "end": signal_dates[-1],
        },
        "train_trade_count": len(train_trades),
        "test_trade_count": len(test_trades),
    }


def _build_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """从交易记录计算完整 9-KPI 指标，委托给共享的 evaluation_metrics 模块。"""
    return compute_full_metrics(trades)


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
    baseline = comparisons["baseline"]
    baseline_walk = baseline["walk_forward"]["test_aggregate"]
    any_non_baseline = any(k != "baseline" for k in comparisons)
    use_walk_forward = baseline_walk["trade_count"] > 0 and any_non_baseline and any(
        comparisons[k]["walk_forward"]["test_aggregate"]["trade_count"] > 0
        for k in comparisons if k != "baseline"
    )
    scope_label = "滚动样本外" if use_walk_forward else "单次时间切分"
    prefix = "" if use_walk_forward else "滚动样本外可比样本不足，暂按单次时间切分判断。"

    baseline_test = baseline_walk if use_walk_forward else baseline["split_metrics"]["test"]
    best_variant = "baseline"
    best_reasons: list[str] = []

    for name, comp in comparisons.items():
        if name == "baseline":
            continue
        variant_test = (
            comp["walk_forward"]["test_aggregate"]
            if use_walk_forward
            else comp["split_metrics"]["test"]
        )
        if variant_test["trade_count"] == 0:
            best_reasons.append(f"{comp['variant'].label}在{scope_label}无交易，跳过。")
            continue
        if _variant_outperforms(variant_test, baseline_test):
            if best_variant == "baseline":
                best_variant = name
                best_reasons.append(
                    f"{comp['variant'].label}在{scope_label}准确率更高，且平均净收益与回撤未明显恶化，采用{comp['variant'].label}。"
                )
            else:
                best_reasons.append(
                    f"{comp['variant'].label}也优于基线，但{comparisons[best_variant]['variant'].label}已选。"
                )
        else:
            best_reasons.append(
                f"{comp['variant'].label}在{scope_label}优势不足或回撤代价偏大。"
            )

    if best_variant == "baseline":
        return "baseline", (f"{prefix}各变体在{scope_label}优势不足，保留基线版。".strip() + " " + " ".join(best_reasons)).strip()
    return best_variant, (prefix + " " + best_reasons[0] if best_reasons else f"采用{comparisons[best_variant]['variant'].label}。").strip()


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

    # 按频率优先级查询本地 DB：15min（全量）→ 1min（兜底）
    rows = []
    checked_freqs = []
    for freq in _MINUTE_FREQ_PRIORITY:
        checked_freqs.append(freq)
        rows = db.scalars(
            select(MinuteKbar)
            .where(
                MinuteKbar.code == code,
                MinuteKbar.freq == freq,
                MinuteKbar.trade_time >= f"{trade_date} 00:00:00",
                MinuteKbar.trade_time < f"{_next_date(trade_date)} 00:00:00",
            )
            .order_by(MinuteKbar.trade_time.asc())
        ).all()
        if rows:
            _record_minute_source_trace(
                minute_source_trace,
                source=f"local_minute_db_{freq}",
                code=code,
                status="success",
                row_count=len(rows),
                scope="stock",
            )
            break

    if not rows and not _FORCE_LOCAL_ONLY:
        recent_rows = fetch_minute_kbars(code, trade_date=trade_date, trace=minute_source_trace)
        if recent_rows:
            _persist_minute_rows(db, recent_rows)
            for freq in _MINUTE_FREQ_PRIORITY:
                rows = db.scalars(
                    select(MinuteKbar)
                    .where(
                        MinuteKbar.code == code,
                        MinuteKbar.freq == freq,
                        MinuteKbar.trade_time >= f"{trade_date} 00:00:00",
                        MinuteKbar.trade_time < f"{_next_date(trade_date)} 00:00:00",
                    )
                    .order_by(MinuteKbar.trade_time.asc())
                ).all()
                if rows:
                    break
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
    elif not rows:
        _record_minute_source_trace(
            minute_source_trace,
            source=f"local_minute_miss_{','.join(checked_freqs)}",
            code=code,
            status="empty_force_local",
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

    if _FORCE_LOCAL_ONLY:
        logger.warning("benchmark bundle missing dates=%d (force_local), returning partial data", len(required_dates - covered_dates))
        cache["cache_key"] = cache_key
        cache["bundle"] = {"rows": local_rows}
        _record_minute_source_trace(
            minute_source_trace,
            source="local_minute_db_partial",
            code=BENCHMARK_CODE,
            status="partial",
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
    for freq in _MINUTE_FREQ_PRIORITY:
        rows = db.scalars(
            select(MinuteKbar)
            .where(
                MinuteKbar.code == code,
                MinuteKbar.freq == freq,
                MinuteKbar.trade_time >= f"{start_date} 00:00:00",
                MinuteKbar.trade_time < f"{_next_date(end_date)} 00:00:00",
            )
            .order_by(MinuteKbar.trade_time.asc())
        ).all()
        if rows:
            break
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
                "freq": "1min",
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


def _backfill_stock_minutes_for_date(
    db: Session,
    codes: list[str],
    trade_date: str,
) -> int:
    total_rows = 0
    for code in codes:
        rows = fetch_minute_kbars(code, trade_date=trade_date) or []
        rows = [row for row in rows if _normalize_trade_date(row.get("trade_date")) == trade_date]
        if not rows:
            continue
        _persist_minute_rows(db, rows)
        total_rows += len(rows)
    return total_rows


def _backfill_benchmark_minutes(db: Session, required_dates: list[str]) -> int:
    if not required_dates:
        return 0
    start_date = min(required_dates)
    end_date = max(required_dates)
    rows = fetch_index_minute_kbars(BENCHMARK_CODE, start_date, end_date) or []
    rows = [
        row for row in rows
        if _normalize_trade_date(row.get("trade_date")) in set(required_dates)
    ]
    if not rows:
        return 0
    _persist_minute_rows(db, rows)
    return len(rows)


def _persist_backtest_run(
    db: Session,
    start_date: str,
    end_date: str,
    result: dict[str, Any],
    *,
    status: str = "completed",
) -> None:
    row = BacktestRun(
        backtest_id=f"bt_{STRATEGY_ID}_{uuid.uuid4().hex[:10]}",
        skill_id=STRATEGY_ID,
        date_from=start_date,
        date_to=end_date,
        status=status,
        result_json=json.dumps(result, ensure_ascii=False),
    )
    db.add(row)
    db.commit()


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
    if len(trades) < _SHARED_MIN_SAMPLE:
        notes.append("当前样本数低于 30 笔，按规则标记为低置信度。")
    if signal_dates:
        notes.append(f"本次仅验证可取到分钟线/次日数据的区间：{signal_dates[0]} 至 {signal_dates[-1]}。")
    return notes


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


def _is_shrinking_limit_up(bar: DailyKbar, up_limit: float, signal_day_volume: float) -> bool:
    return (
        _num(bar.close) >= up_limit * 0.995
        and _num(bar.high) >= up_limit * 0.995
        and _num(bar.volume) > 0
        and signal_day_volume > 0
        and _num(bar.volume) <= signal_day_volume * SHRINK_LIMIT_VOLUME_RATIO
    )


def _apply_entry_cost(price: float, cost: CostAssumption) -> float:
    return price * (1 + (cost.buy_commission_bps + cost.buy_slippage_bps) / 10000.0)


def _apply_exit_cost(price: float, cost: CostAssumption) -> float:
    return price * (1 - (cost.sell_commission_bps + cost.sell_stamp_tax_bps + cost.sell_slippage_bps) / 10000.0)


def _pct_change(base: float, target: float) -> float:
    if base <= 0:
        return 0.0
    return (target - base) / base * 100.0


def _empty_metrics() -> dict[str, Any]:
    """委托给共享模块。"""
    return _shared_empty_metrics()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


def _next_date(trade_date: str) -> str:
    return (date.fromisoformat(trade_date) + timedelta(days=1)).isoformat()


def _run_overnight_temporal_check(
    db: Session,
    signal_dates: list[str],
) -> dict[str, Any]:
    """对隔夜持股法回测结果做自动时态一致性检查。

    检查维度：
    1. 信号日期的日K线数据存在性
    2. 分钟线数据时态顺序（entry <= exit）
    3. 没有未来数据穿越

    Returns:
        dict with passed, violation_count, error_count, warning_count
    """
    violations: list[dict[str, Any]] = []

    # 1. 检查每个信号日期的日K线存在性
    for td in signal_dates:
        bar_count = db.scalar(
            select(func.count())
            .select_from(DailyKbar)
            .where(DailyKbar.trade_date == td)
        )
        if bar_count == 0:
            violations.append({
                "check": "overnight_daily_kbar_exists",
                "severity": "error",
                "trade_date": td,
                "message": f"信号日期 {td} 在 daily_kbars 中无日K线数据",
            })

    # 2. 检查 backtest 整体时态
    bt_result = check_backtest_temporal_consistency(
        db, skill_id=STRATEGY_ID,
    )
    if bt_result.get("status") == "completed":
        violations.extend(bt_result.get("violations", []))

    error_count = sum(1 for v in violations if v.get("severity") == "error")
    warning_count = sum(1 for v in violations if v.get("severity") == "warning")

    return {
        "status": "completed",
        "passed": error_count == 0,
        "violation_count": len(violations),
        "error_count": error_count,
        "warning_count": warning_count,
        "violations": violations[:20],
    }


# ═════════════════════════════════════════════════════════════════════════════
# 参数敏感性分析 / Grid Search
# ═════════════════════════════════════════════════════════════════════════════

_DEFAULT_PARAM_GRID: list[dict[str, Any]] = [
    {
        "label": "基线版 (基准参数)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
        "MIN_RECENT_LIMIT_UP_COUNT": 0,
    },
    {
        "label": "V1: 放宽涨跌幅 (2~6%)",
        "MIN_PCT": 2.0, "MAX_PCT": 6.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V2: 收紧VWAP回踩 (+90%站线率)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.90,
        "TAIL_VWAP_MAX_GAP_BPS": 120.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V3: 放宽容换手率 (3~15%)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 3.0, "MAX_TURNOVER": 15.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V4: 大市值偏好 (100~300亿)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 100.0, "MAX_MARKET_CAP_YI": 300.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V5: 缩量涨停更严格 (0.7倍阈值)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.70,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V6: 高成交量验证 (量比>=1.2)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.2,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V7: 强于大盘增强版 (+1%领先)",
        "MIN_PCT": 3.0, "MAX_PCT": 5.0,
        "MIN_TURNOVER": 5.0, "MAX_TURNOVER": 10.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.86,
        "TAIL_VWAP_MAX_GAP_BPS": 170.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 1.0,
    },
    {
        "label": "V8: 综合收紧 (涨跌幅+VWAP+换手+量比)",
        "MIN_PCT": 2.5, "MAX_PCT": 4.5,
        "MIN_TURNOVER": 4.0, "MAX_TURNOVER": 12.0,
        "MIN_MARKET_CAP_YI": 50.0, "MAX_MARKET_CAP_YI": 200.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.1,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.88,
        "TAIL_VWAP_MAX_GAP_BPS": 150.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.80,
        "MIN_RELATIVE_STRENGTH_PCT": 0.3,
    },
    {
        "label": "V9: 窄幅 + 低换手 (保守型)",
        "MIN_PCT": 3.5, "MAX_PCT": 4.5,
        "MIN_TURNOVER": 4.0, "MAX_TURNOVER": 8.0,
        "MIN_MARKET_CAP_YI": 60.0, "MAX_MARKET_CAP_YI": 150.0,
        "MIN_INTRADAY_VOLUME_RATIO": 1.0,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.90,
        "TAIL_VWAP_MAX_GAP_BPS": 140.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.75,
        "MIN_RELATIVE_STRENGTH_PCT": 0.5,
    },
    {
        "label": "V10: 宽幅 + 高换手 (进攻型)",
        "MIN_PCT": 2.0, "MAX_PCT": 6.0,
        "MIN_TURNOVER": 6.0, "MAX_TURNOVER": 18.0,
        "MIN_MARKET_CAP_YI": 30.0, "MAX_MARKET_CAP_YI": 300.0,
        "MIN_INTRADAY_VOLUME_RATIO": 0.8,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.82,
        "TAIL_VWAP_MAX_GAP_BPS": 200.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
    },
    {
        "label": "V11: 放松候选池 (去涨停限制, V10+MIN_RECENT_LIMIT_UP_COUNT=0)",
        "MIN_PCT": 2.0, "MAX_PCT": 6.0,
        "MIN_TURNOVER": 6.0, "MAX_TURNOVER": 18.0,
        "MIN_MARKET_CAP_YI": 30.0, "MAX_MARKET_CAP_YI": 300.0,
        "MIN_INTRADAY_VOLUME_RATIO": 0.8,
        "DEFAULT_MIN_ABOVE_VWAP_RATIO": 0.82,
        "TAIL_VWAP_MAX_GAP_BPS": 200.0,
        "SHRINK_LIMIT_VOLUME_RATIO": 0.85,
        "MIN_RELATIVE_STRENGTH_PCT": 0.0,
        "MIN_RECENT_LIMIT_UP_COUNT": 0,
    },
]


def run_overnight_param_sensitivity(
    db: Session,
    start_date: str,
    end_date: str,
    param_grid: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """隔夜持股法参数敏感性分析 / Grid Search。

    对 param_grid 中的每个参数组合运行完整回测，
    产出多参数组合的对比矩阵（含 walk-forward 样本外验证）。

    Args:
        db: 数据库会话
        start_date: 起始日期
        end_date: 结束日期
        param_grid: 参数组合列表，每项包含 label + 需覆盖的模块级常量名和值。
                    若为 None，使用 _DEFAULT_PARAM_GRID。

    Returns:
        dict 含全部参数组合的 results 矩阵 + 排名
    """
    import sys

    started_at = perf_counter()
    grid = param_grid if param_grid is not None else _DEFAULT_PARAM_GRID

    if not grid:
        return {"status": "no_data", "message": "参数网格为空", "combination_count": 0}

    logger.info("参数敏感性分析开始: %d 组参数组合, %s~%s", len(grid), start_date, end_date)
    mod = sys.modules[__name__]

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, params in enumerate(grid):
        label = params.get("label", f"组合_{idx}")
        combo_start = perf_counter()

        # 保存并覆盖模块级常量
        saved: dict[str, Any] = {}
        for key, val in params.items():
            if key == "label":
                continue
            if hasattr(mod, key):
                saved[key] = getattr(mod, key)
                setattr(mod, key, val)

        try:
            bt_result = run_overnight_holding_backtest(db, start_date, end_date)
            if bt_result.get("status") == "completed":
                metrics = bt_result.get("aggregate_metrics") or _shared_empty_metrics()
                wf = bt_result.get("comparison", {}).get("walk_forward", {})
                test_agg = wf.get("test_aggregate") if wf.get("eligible") else {}
                results.append({
                    "label": label,
                    "params": {k: v for k, v in params.items() if k != "label"},
                    "elapsed_ms": int((perf_counter() - combo_start) * 1000),
                    "trade_count": metrics.get("trade_count", 0),
                    "accuracy": metrics.get("recommendation_accuracy", 0),
                    "avg_return": metrics.get("average_net_return", 0),
                    "profit_loss_ratio": metrics.get("profit_loss_ratio", 0),
                    "max_drawdown": metrics.get("max_drawdown", 0),
                    "excess_return": metrics.get("benchmark_excess_return", 0),
                    "sample_confidence": metrics.get("sample_confidence", "low"),
                    "walk_forward_trade_count": test_agg.get("trade_count", 0),
                    "walk_forward_accuracy": test_agg.get("recommendation_accuracy", 0),
                    "walk_forward_avg_return": test_agg.get("average_net_return", 0),
                    "walk_forward_max_drawdown": test_agg.get("max_drawdown", 0),
                })
            else:
                errors.append({
                    "label": label,
                    "status": bt_result.get("status"),
                    "message": bt_result.get("message", "未知错误"),
                })
        except Exception as exc:
            logger.exception("参数组合 %s 执行失败", label)
            errors.append({"label": label, "error": str(exc)})
        finally:
            # 恢复模块级常量
            for key, val in saved.items():
                setattr(mod, key, val)

        logger.info(
            "  组合 %d/%d [%s] 完成 (%.1fs)",
            idx + 1, len(grid), label,
            (perf_counter() - combo_start),
        )

    # ── 排名：按样本外准确率降序 ──
    ranked = sorted(
        [r for r in results if r.get("walk_forward_trade_count", 0) >= 2 or r.get("trade_count", 0) >= 2],
        key=lambda r: (
            r.get("walk_forward_accuracy", 0)
            if r.get("walk_forward_trade_count", 0) >= 2
            else r.get("accuracy", 0)
        ),
        reverse=True,
    )

    top_n = ranked[:5]
    baseline = next((r for r in results if "基线" in r.get("label", "")), ranked[0] if ranked else None)

    best_pick = ranked[0] if ranked else None
    adoption_reason = ""
    if best_pick and baseline and best_pick["label"] != baseline["label"]:
        adoption_reason = (
            f"选中 [{best_pick['label']}]："
            f"样本外准确率 {best_pick.get('walk_forward_accuracy', 0):.1%}"
            f" vs 基线 {baseline.get('walk_forward_accuracy', 0):.1%}"
        )
    elif best_pick:
        adoption_reason = "基线版仍是当前最优组合"
    else:
        adoption_reason = "无可用结果"

    response = {
        "status": "completed",
        "strategy_id": STRATEGY_ID,
        "strategy_name": f"{STRATEGY_NAME} 参数敏感性分析",
        "strategy_window": {"start_date": start_date, "end_date": end_date},
        "combination_count": len(grid),
        "success_count": len(results),
        "error_count": len(errors),
        "total_elapsed_ms": int((perf_counter() - started_at) * 1000),
        "results": results,
        "ranking": [
            {
                "rank": i + 1,
                "label": r["label"],
                "walk_forward_accuracy": r.get("walk_forward_accuracy"),
                "accuracy": r.get("accuracy"),
                "avg_return": r.get("avg_return"),
                "walk_forward_avg_return": r.get("walk_forward_avg_return"),
                "trade_count": r.get("trade_count"),
                "max_drawdown": r.get("max_drawdown"),
            }
            for i, r in enumerate(ranked)
        ],
        "errors": errors,
        "recommendation": {
            "top_5": [r["label"] for r in top_n],
            "best_pick": best_pick["label"] if best_pick else None,
            "baseline_label": baseline["label"] if baseline else None,
            "adoption_reason": adoption_reason,
        },
        "notes": [
            f"参数敏感性分析覆盖 {len(grid)} 组参数组合（{len(results)} 组成功, {len(errors)} 组失败）。",
            "排名优先使用 walk-forward (样本外) 准确率；样本外笔数 < 2 时降级到全量准确率。",
            "最终推荐基于样本外准确率 + 交易笔数 + 回撤综合判断。",
        ],
    }

    # 如果 baseline 存在，加对比信息
    if baseline:
        response["comparison"] = {
            "baseline_label": baseline["label"],
            "best_pick_label": best_pick["label"] if best_pick else None,
            "baseline_accuracy": baseline.get("accuracy"),
            "baseline_avg_return": baseline.get("avg_return"),
            "best_accuracy": best_pick.get("accuracy") if best_pick else None,
            "best_avg_return": best_pick.get("avg_return") if best_pick else None,
            "adoption_reason": adoption_reason,
        }

    logger.info(
        "参数敏感性分析完成: %d/%d 组合成功, 推荐=%s",
        len(results), len(grid),
        response["recommendation"].get("best_pick", "N/A"),
    )
    return response

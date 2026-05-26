import json
import threading
import time
from bisect import bisect_left
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services.evaluation_metrics import compute_full_metrics as _compute_full_metrics

ENTRY_LOOKAHEAD_DAYS = 2
ENTRY_SLIPPAGE_BPS = 10.0
EXIT_SLIPPAGE_BPS = 10.0
SUCCESS_RETURN_PCT = 3.0
DEFAULT_STOP_LOSS_PCT = -4.0
DEFAULT_TAKE_PROFIT_PCT = 8.0
MAX_STOP_LOSS_PCT = -8.0       # 止损最宽（-8%），防止 feature_builder 设置过宽的止损
MAX_TAKE_PROFIT_PCT = 15.0     # 止盈上限（+15%），防止 feature_builder 设置过高的止盈

OUTCOME_PENDING = "pending"
OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_MISSED = "missed"
EXECUTED_OUTCOME_LABELS = {OUTCOME_SUCCESS, OUTCOME_FAILED}

# 短时缓存
_metrics_cache: dict[str, tuple[float, Any]] = {}
_metrics_cache_lock = threading.Lock()
_METRICS_CACHE_TTL = 5.0


def _cached(key: str, factory):
    now = time.time()
    with _metrics_cache_lock:
        entry = _metrics_cache.get(key)
        if entry is not None and (now - entry[0]) < _METRICS_CACHE_TTL:
            return entry[1]
    result = factory()
    with _metrics_cache_lock:
        _metrics_cache[key] = (time.time(), result)
    return result


def invalidate_outcome_metrics_cache() -> None:
    with _metrics_cache_lock:
        _metrics_cache.clear()


def normalized_outcome_label(outcome: RecommendationOutcome | Any | None) -> str:
    if outcome is None:
        return OUTCOME_PENDING
    raw_label = str(getattr(outcome, "outcome_label", OUTCOME_PENDING) or OUTCOME_PENDING).lower()
    if raw_label == OUTCOME_PENDING:
        return OUTCOME_PENDING
    if raw_label in {OUTCOME_MISSED, "not_triggered"}:
        return OUTCOME_MISSED
    if raw_label == "risk_hit":
        return OUTCOME_FAILED
    if str(getattr(outcome, "label_source", "") or "").lower() == "manual" and raw_label in {
        OUTCOME_SUCCESS,
        OUTCOME_FAILED,
    }:
        return raw_label
    if not bool(getattr(outcome, "entry_triggered", False)):
        return OUTCOME_MISSED
    return OUTCOME_SUCCESS if raw_label == OUTCOME_SUCCESS else OUTCOME_FAILED


def is_outcome_labeled(outcome: RecommendationOutcome | Any | None) -> bool:
    return normalized_outcome_label(outcome) != OUTCOME_PENDING


def is_trade_executed(outcome: RecommendationOutcome | Any | None) -> bool:
    return bool(outcome is not None and getattr(outcome, "entry_triggered", False))


def resolved_trade_return(outcome: RecommendationOutcome | Any | None) -> float:
    if outcome is None:
        return 0.0
    executed = getattr(outcome, "executed_return", None)
    if executed is not None:
        return float(executed or 0.0)
    return float(getattr(outcome, "close_return", 0.0) or 0.0)


def label_pending_outcomes(
    db: Session,
    horizon_days: int = 5,
    run_mode: str | None = None,
) -> dict[str, Any]:
    """对 pending 推荐做执行口径标注。

    保留原有静态窗口指标（close/max/min/achievable），同时新增：
    - 是否真实触发买点
    - 进出场价格/日期
    - 执行收益（含双边滑点）
    - 持仓天数 / 仓位权重 / 退出原因
    """
    stmt = (
        select(Recommendation)
        .outerjoin(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(
            or_(
                RecommendationOutcome.recommendation_id.is_(None),
                RecommendationOutcome.outcome_label == OUTCOME_PENDING,
                Recommendation.status == OUTCOME_PENDING,
            )
        )
    )
    if run_mode:
        stmt = stmt.join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id).where(
            RecommendationRun.run_mode == run_mode
        )
    recs = db.scalars(stmt.order_by(Recommendation.trade_date.asc())).unique().all()
    if not recs:
        return {"labeled": 0}

    codes = sorted({str(rec.code) for rec in recs})
    min_trade_date = min(str(rec.trade_date) for rec in recs)
    bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date >= min_trade_date)
        .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
    ).all()

    bars_by_code: dict[str, list[DailyKbar]] = {}
    dates_by_code: dict[str, list[str]] = {}
    for bar in bars:
        code = str(bar.code)
        bars_by_code.setdefault(code, []).append(bar)
        dates_by_code.setdefault(code, []).append(str(bar.trade_date))

    labeled = 0
    for rec in recs:
        simulation = _simulate_outcome(
            rec,
            bars_by_code.get(str(rec.code), []),
            dates_by_code.get(str(rec.code), []),
            horizon_days=horizon_days,
        )
        if simulation is None:
            continue

        outcome = db.get(RecommendationOutcome, rec.recommendation_id)
        if outcome is None:
            outcome = RecommendationOutcome(recommendation_id=rec.recommendation_id)
            db.add(outcome)

        outcome.horizon_days = horizon_days
        outcome.entry_triggered = bool(simulation["entry_triggered"])
        outcome.entry_date = simulation["entry_date"]
        outcome.entry_price = simulation["entry_price"]
        outcome.exit_date = simulation["exit_date"]
        outcome.exit_price = simulation["exit_price"]
        outcome.executed_return = simulation["executed_return"]
        outcome.weighted_return = simulation["weighted_return"]
        outcome.position_size = simulation["position_size"]
        outcome.holding_days = simulation["holding_days"]
        outcome.entry_slippage_bps = simulation["entry_slippage_bps"]
        outcome.exit_slippage_bps = simulation["exit_slippage_bps"]
        outcome.stop_loss_price = simulation["stop_loss_price"]
        outcome.take_profit_price = simulation["take_profit_price"]
        outcome.exit_reason = simulation["exit_reason"]
        outcome.max_return = simulation["max_return"]
        outcome.min_return = simulation["min_return"]
        outcome.close_return = simulation["close_return"]
        outcome.achievable_return = simulation["achievable_return"]
        outcome.max_drawdown = simulation["max_drawdown"]
        outcome.hit_target = bool(simulation["hit_target"])
        outcome.hit_invalid = bool(simulation["hit_invalid"])
        outcome.outcome_label = str(simulation["outcome_label"])
        outcome.outcome_label_legacy = str(simulation["outcome_label_legacy"])
        outcome.outcome_reason = str(simulation["outcome_reason"])
        outcome.label_source = "auto"
        outcome.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        rec.status = normalized_outcome_label(outcome)
        labeled += 1

    db.commit()
    if labeled:
        invalidate_outcome_metrics_cache()
    return {"labeled": labeled}


def overview_metrics(db: Session, run_mode: str = "formal") -> dict[str, Any]:
    return _cached(f"overview:{run_mode}", lambda: _overview_metrics(db, run_mode))


def _overview_metrics(db: Session, run_mode: str) -> dict[str, Any]:
    rows = _scoped_recommendation_rows(db, run_mode)
    total_recs = len(rows)

    labeled = [row for row in rows if normalized_outcome_label(row) != OUTCOME_PENDING]
    triggered = [row for row in labeled if is_trade_executed(row)]
    success_count = sum(1 for row in rows if normalized_outcome_label(row) == OUTCOME_SUCCESS)
    failed_count = sum(1 for row in rows if normalized_outcome_label(row) == OUTCOME_FAILED)
    missed_count = sum(1 for row in rows if normalized_outcome_label(row) == OUTCOME_MISSED)
    n = len(labeled)
    executed_n = len(triggered)

    if not triggered:
        return {
            "mode": run_mode,
            "total_recommendations": total_recs,
            "labeled_count": n,
            "triggered_count": 0,
            "trigger_rate": 0.0,
            "missed_count": missed_count,
            "pending_count": max(0, total_recs - n),
            "success_count": 0,
            "failed_count": 0,
            "hit_rate": 0.0,
            "avg_return": 0.0,
            "avg_executed_return": 0.0,
            "avg_weighted_return": 0.0,
            "avg_close_return": 0.0,
            "avg_mfe": 0.0,
            "avg_mae": 0.0,
            "avg_holding_days": 0.0,
            "recommendation_accuracy": 0.0,
            "average_net_return": 0.0,
            "median_net_return": 0.0,
            "profit_loss_ratio": 0.0,
            "max_drawdown": 0.0,
            "positive_month_ratio": 0.0,
            "benchmark_excess_return": 0.0,
            "sample_confidence": "low",
            "false_positive_notes": [],
            "strategy_compound_return": 0.0,
            "benchmark_compound_return": 0.0,
        }

    # ── 收集交易日期 → 批量查询基准指数 ──
    all_dates: set[str] = set()
    trade_date_pairs: list[tuple[str, str]] = []
    for row in triggered:
        entry_date = str(getattr(row, "entry_date", "") or getattr(row, "trade_date", ""))
        exit_date = str(getattr(row, "exit_date", "") or getattr(row, "trade_date", ""))
        all_dates.add(entry_date)
        all_dates.add(exit_date)
        trade_date_pairs.append((entry_date, exit_date))

    from app.services.benchmark_service import compute_benchmark_return as _bench_return
    from app.services.benchmark_service import get_benchmark_close_map as _get_close_map
    close_map = _get_close_map(db, list(all_dates))

    # ── 构建交易记录 → 统一指标引擎 ──
    trades: list[dict[str, Any]] = []
    for i, row in enumerate(triggered):
        executed_return = resolved_trade_return(row)
        entry_date, exit_date = trade_date_pairs[i]
        benchmark_ret = _bench_return(close_map, entry_date, exit_date)
        trades.append({
            "net_return_pct": float(executed_return),
            "gross_return_pct": float(getattr(row, "close_return", 0) or 0),
            "achievable_return_pct": float(getattr(row, "achievable_return", 0) or 0),
            "morning_drawdown_pct": float(getattr(row, "max_drawdown", 0) or 0),
            "holding_days": float(getattr(row, "holding_days", 1) or 1),
            "benchmark_return_pct": benchmark_ret,
            "accurate": bool(executed_return > 0),
            "trade_date": str(getattr(row, "trade_date", "")),
            "code": getattr(row, "code", ""),
            "name": getattr(row, "name", ""),
            "false_positive_reason": getattr(row, "outcome_reason", None) if executed_return <= 0 else None,
            "resolved": True,
        })

    full_metrics = _compute_full_metrics(trades) if trades else {}

    return {
        "mode": run_mode,
        "total_recommendations": total_recs,
        "labeled_count": n,
        "triggered_count": executed_n,
        "trigger_rate": round(executed_n / n, 3) if n else 0.0,
        "missed_count": missed_count,
        "pending_count": max(0, total_recs - n),
        "success_count": success_count,
        "failed_count": failed_count,
        # 9-KPI 来自统一指标引擎
        "hit_rate": full_metrics.get("hit_rate", 0.0),
        "avg_return": full_metrics.get("avg_return", 0.0),
        "avg_executed_return": full_metrics.get("avg_executed_return", 0.0),
        "avg_weighted_return": full_metrics.get("avg_weighted_return", 0.0),
        "avg_close_return": full_metrics.get("avg_close_return", 0.0),
        "avg_mfe": full_metrics.get("avg_max_return", 0.0),
        "avg_mae": full_metrics.get("avg_max_drawdown", 0.0),
        "avg_holding_days": full_metrics.get("avg_holding_days", 0.0),
        "recommendation_accuracy": full_metrics.get("recommendation_accuracy", 0.0),
        "average_net_return": full_metrics.get("average_net_return", 0.0),
        "median_net_return": full_metrics.get("median_net_return", 0.0),
        "profit_loss_ratio": full_metrics.get("profit_loss_ratio", 0.0),
        "max_drawdown": full_metrics.get("max_drawdown", 0.0),
        "positive_month_ratio": full_metrics.get("positive_month_ratio", 0.0),
        "benchmark_excess_return": full_metrics.get("benchmark_excess_return", 0.0),
        "sample_confidence": full_metrics.get("sample_confidence", "low"),
        "false_positive_notes": full_metrics.get("false_positive_notes", []),
        "strategy_compound_return": full_metrics.get("strategy_compound_return", 0.0),
        "benchmark_compound_return": full_metrics.get("benchmark_compound_return", 0.0),
    }


def metrics_by_strategy(db: Session, run_mode: str = "formal") -> list[dict[str, Any]]:
    return _cached(f"by_strategy:{run_mode}", lambda: _metrics_by_strategy(db, run_mode))


def _metrics_by_strategy(db: Session, run_mode: str) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, run_mode)

    buckets: dict[str, dict[str, Any]] = {}
    for rec in rows:
        executed = is_trade_executed(rec)
        trade_return = resolved_trade_return(rec)
        label = normalized_outcome_label(rec)
        for signal in _loads(rec.skill_breakdown_json, []):
            sid = signal.get("strategy_id", "unknown")
            item = buckets.setdefault(
                sid,
                {
                    "strategy_id": sid,
                    "sample_count": 0,
                    "triggered_count": 0,
                    "success_count": 0,
                    "avg_score": 0.0,
                    "avg_return": 0.0,
                },
            )
            item["sample_count"] += 1
            item["avg_score"] += float(signal.get("score", 0))
            if executed:
                item["triggered_count"] += 1
                item["avg_return"] += trade_return
                if label == OUTCOME_SUCCESS:
                    item["success_count"] += 1

    result = []
    for item in buckets.values():
        count = max(1, item["sample_count"])
        triggered = int(item["triggered_count"])
        item["hit_rate"] = round(item["success_count"] / triggered, 3) if triggered else 0.0
        item["trigger_rate"] = round(triggered / count, 3)
        item["avg_score"] = round(item["avg_score"] / count, 2)
        item["avg_return"] = round(item["avg_return"] / triggered, 2) if triggered else 0.0
        result.append(item)
    return sorted(result, key=lambda x: (x["sample_count"], x["hit_rate"], x["avg_score"]), reverse=True)


def metrics_by_setup(db: Session, run_mode: str = "formal") -> list[dict[str, Any]]:
    return _cached(f"by_setup:{run_mode}", lambda: _metrics_by_setup(db, run_mode))


def _metrics_by_setup(db: Session, run_mode: str) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, run_mode)
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        setup = str(row.setup_type or "未命中买点")
        item = buckets.setdefault(
            setup,
            {
                "setup_type": setup,
                "sample_count": 0,
                "labeled_count": 0,
                "triggered_count": 0,
                "success_count": 0,
                "avg_score": 0.0,
                "avg_return": 0.0,
                "avg_drawdown": 0.0,
            },
        )
        item["sample_count"] += 1
        item["avg_score"] += float(row.final_score or 0)
        label = normalized_outcome_label(row)
        if label != OUTCOME_PENDING:
            item["labeled_count"] += 1
            item["avg_drawdown"] += float(row.max_drawdown or 0)
        if is_trade_executed(row):
            item["triggered_count"] += 1
            item["avg_return"] += resolved_trade_return(row)
            if label == OUTCOME_SUCCESS:
                item["success_count"] += 1

    result = []
    for item in buckets.values():
        sample_count = max(1, int(item["sample_count"]))
        labeled_count = int(item["labeled_count"])
        triggered_count = int(item["triggered_count"])
        missed_count = max(0, labeled_count - triggered_count)
        result.append(
            {
                "setup_type": item["setup_type"],
                "sample_count": item["sample_count"],
                "labeled_count": labeled_count,
                "triggered_count": triggered_count,
                "missed_count": missed_count,
                "success_count": item["success_count"],
                "trigger_rate": round(triggered_count / labeled_count, 3) if labeled_count else 0.0,
                "hit_rate": round(item["success_count"] / triggered_count, 3) if triggered_count else 0.0,
                "avg_score": round(item["avg_score"] / sample_count, 2),
                "avg_return": round(item["avg_return"] / triggered_count, 2) if triggered_count else 0.0,
                "avg_drawdown": round(item["avg_drawdown"] / labeled_count, 2) if labeled_count else 0.0,
            }
        )
    return sorted(result, key=lambda item: (item["sample_count"], item["hit_rate"], item["avg_score"]), reverse=True)


def precision_at_k(
    db: Session,
    run_mode: str = "formal",
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> list[dict[str, Any]]:
    return _cached(f"precision_at_k:{run_mode}:{','.join(str(k) for k in ks)}", lambda: _precision_at_k(db, run_mode, ks))


def _precision_at_k(db: Session, run_mode: str, ks: tuple[int, ...]) -> list[dict[str, Any]]:
    recs = db.execute(
        select(
            Recommendation.run_id,
            Recommendation.recommendation_id,
            Recommendation.final_score,
            RecommendationOutcome.outcome_label,
            RecommendationOutcome.entry_triggered,
        )
        .select_from(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .where(RecommendationRun.run_mode == run_mode)
        .order_by(Recommendation.run_id.asc(), Recommendation.final_score.desc(), Recommendation.recommendation_id.asc())
    ).all()
    by_run: dict[str, list[Any]] = {}
    for row in recs:
        by_run.setdefault(row.run_id, []).append(row)

    result: list[dict[str, Any]] = []
    for k in ks:
        sample_runs = 0
        evaluated_count = 0
        success_count = 0
        for rows in by_run.values():
            top_rows = rows[:k]
            labeled_rows = [row for row in top_rows if normalized_outcome_label(row) != OUTCOME_PENDING]
            if not labeled_rows:
                continue
            sample_runs += 1
            evaluated_count += len(labeled_rows)
            success_count += sum(1 for row in labeled_rows if normalized_outcome_label(row) == OUTCOME_SUCCESS)
        result.append(
            {
                "k": k,
                "sample_runs": sample_runs,
                "evaluated_count": evaluated_count,
                "success_count": success_count,
                "precision": round(success_count / evaluated_count, 3) if evaluated_count else 0.0,
            }
        )
    return result


def metrics_by_score_line(
    db: Session,
    run_mode: str = "formal",
    score_lines: tuple[int, ...] = (55, 60, 65, 70, 75, 80),
) -> list[dict[str, Any]]:
    key = f"by_score_line:{run_mode}:{','.join(str(line) for line in score_lines)}"
    return _cached(key, lambda: _metrics_by_score_line(db, run_mode, score_lines))


def _metrics_by_score_line(db: Session, run_mode: str, score_lines: tuple[int, ...]) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, run_mode)
    if not rows:
        return []
    result: list[dict[str, Any]] = []
    total_count = len(rows)
    for score_line in score_lines:
        selected = [row for row in rows if float(row.final_score or 0) >= score_line]
        labeled = [row for row in selected if normalized_outcome_label(row) != OUTCOME_PENDING]
        executed = [row for row in labeled if is_trade_executed(row)]
        missed_count = max(0, len(labeled) - len(executed))
        success_count = sum(1 for row in executed if normalized_outcome_label(row) == OUTCOME_SUCCESS)
        result.append(
            {
                "score_line": float(score_line),
                "sample_count": len(selected),
                "activation_rate": round(len(selected) / total_count, 3) if total_count else 0.0,
                "labeled_count": len(labeled),
                "triggered_count": len(executed),
                "missed_count": missed_count,
                "success_count": success_count,
                "trigger_rate": round(len(executed) / len(labeled), 3) if labeled else 0.0,
                "hit_rate": round(success_count / len(executed), 3) if executed else 0.0,
                "avg_return": round(sum(resolved_trade_return(row) for row in executed) / len(executed), 2) if executed else 0.0,
                "avg_drawdown": round(sum(float(row.max_drawdown or 0) for row in labeled) / len(labeled), 2) if labeled else 0.0,
                "avg_close_return": round(sum(float(row.close_return or 0) for row in labeled) / len(labeled), 2) if labeled else 0.0,
            }
        )
    return result


def _scoped_recommendation_rows(db: Session, run_mode: str) -> list[Any]:
    return list(
        db.execute(
            select(
                Recommendation.recommendation_id,
                Recommendation.run_id,
                Recommendation.code,
                Recommendation.name,
                Recommendation.final_score,
                Recommendation.setup_type,
                Recommendation.skill_breakdown_json,
                Recommendation.trade_date,
                Recommendation.market_context_json,
                RecommendationOutcome.outcome_label,
                RecommendationOutcome.entry_triggered,
                RecommendationOutcome.executed_return,
                RecommendationOutcome.weighted_return,
                RecommendationOutcome.close_return,
                RecommendationOutcome.achievable_return,
                RecommendationOutcome.max_return,
                RecommendationOutcome.min_return,
                RecommendationOutcome.max_drawdown,
                RecommendationOutcome.holding_days,
                RecommendationOutcome.outcome_reason,
                RecommendationOutcome.entry_date,
                RecommendationOutcome.exit_date,
            )
            .select_from(Recommendation)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .outerjoin(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .where(RecommendationRun.run_mode == run_mode)
            .order_by(Recommendation.trade_date.asc(), Recommendation.final_score.desc(), Recommendation.recommendation_id.asc())
        ).all()
    )


def _simulate_outcome(
    rec: Recommendation,
    bars: list[DailyKbar],
    dates: list[str],
    horizon_days: int,
) -> dict[str, Any] | None:
    if not bars:
        return None

    signal_index = bisect_left(dates, str(rec.trade_date))
    signal_bar = bars[signal_index] if signal_index < len(bars) and dates[signal_index] == str(rec.trade_date) else None
    future_start = signal_index + 1 if signal_bar is not None else signal_index
    future_bars = bars[future_start:]
    if not future_bars:
        return None

    feature_snapshot = _loads(getattr(rec, "feature_snapshot_json", None), {})
    static_metrics = _static_window_metrics(rec, signal_bar, future_bars, horizon_days)

    entry_result = _resolve_entry(rec, feature_snapshot, future_bars, horizon_days)
    position_size = _resolve_position_size(feature_snapshot)

    if entry_result is None:
        return {
            **static_metrics,
            "entry_triggered": False,
            "entry_date": None,
            "entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "executed_return": 0.0,
            "weighted_return": 0.0,
            "position_size": position_size,
            "holding_days": 0,
            "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
            "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
            "exit_reason": "not_triggered",
            "hit_target": False,
            "hit_invalid": False,
            "outcome_label": OUTCOME_MISSED,
            "outcome_label_legacy": OUTCOME_MISSED,
            "outcome_reason": "未触发买点，未建仓；静态窗口结果仅作旁路参考",
        }

    entry_index, raw_entry_price, entry_date = entry_result
    executed_entry_price = round(raw_entry_price * (1 + ENTRY_SLIPPAGE_BPS / 10000.0), 4)
    stop_loss_price = _resolve_stop_loss_price(raw_entry_price, feature_snapshot)
    take_profit_price = _resolve_take_profit_price(raw_entry_price, feature_snapshot)

    holding_bars = future_bars[entry_index : entry_index + max(1, horizon_days)]
    if not holding_bars:
        return None

    exit_price, exit_date, exit_reason = _resolve_exit(holding_bars, stop_loss_price, take_profit_price)
    executed_exit_price = round(exit_price * (1 - EXIT_SLIPPAGE_BPS / 10000.0), 4)
    executed_return = round(((executed_exit_price - executed_entry_price) / executed_entry_price) * 100, 2)
    weighted_return = round(executed_return * position_size, 2)
    hit_invalid = exit_reason.startswith("stop_loss")
    hit_target = exit_reason.startswith("take_profit") or executed_return >= SUCCESS_RETURN_PCT
    outcome_label = OUTCOME_SUCCESS if (executed_return >= SUCCESS_RETURN_PCT and not hit_invalid) else OUTCOME_FAILED

    return {
        **static_metrics,
        "entry_triggered": True,
        "entry_date": entry_date,
        "entry_price": round(executed_entry_price, 2),
        "exit_date": exit_date,
        "exit_price": round(executed_exit_price, 2),
        "executed_return": executed_return,
        "weighted_return": weighted_return,
        "position_size": position_size,
        "holding_days": len(holding_bars),
        "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
        "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
        "stop_loss_price": round(stop_loss_price, 2),
        "take_profit_price": round(take_profit_price, 2),
        "exit_reason": exit_reason,
        "hit_target": hit_target,
        "hit_invalid": hit_invalid,
        "outcome_label": outcome_label,
        "outcome_reason": _build_outcome_reason(exit_reason, executed_return, static_metrics["close_return"]),
    }


def _static_window_metrics(
    rec: Recommendation,
    signal_bar: DailyKbar | None,
    future_bars: list[DailyKbar],
    horizon_days: int,
) -> dict[str, Any]:
    window = future_bars[: max(1, horizon_days)]
    if not window:
        return {
            "max_return": 0.0,
            "min_return": 0.0,
            "close_return": 0.0,
            "achievable_return": 0.0,
            "max_drawdown": 0.0,
            "outcome_label_legacy": OUTCOME_MISSED,
        }
    baseline_entry = _as_float(rec.recommend_price) or _as_float(signal_bar.close if signal_bar else None) or _as_float(window[0].open) or _as_float(window[0].close)
    if baseline_entry <= 0:
        baseline_entry = max(0.01, _as_float(window[0].close))
    returns = [((_as_float(bar.high) - baseline_entry) / baseline_entry) * 100 for bar in window]
    lows = [((_as_float(bar.low) - baseline_entry) / baseline_entry) * 100 for bar in window]
    close_return = ((_as_float(window[-1].close) - baseline_entry) / baseline_entry) * 100
    max_return = max(returns) if returns else 0.0
    min_return = min(lows) if lows else 0.0
    achievable_return = (close_return + min(max_return, close_return * 1.5)) / 2.0
    legacy_label = OUTCOME_SUCCESS if (max_return >= 5 and min_return > -4) else OUTCOME_FAILED
    return {
        "max_return": round(max_return, 2),
        "min_return": round(min_return, 2),
        "close_return": round(close_return, 2),
        "achievable_return": round(achievable_return, 2),
        "max_drawdown": round(min_return, 2),
        "outcome_label_legacy": legacy_label,
    }


def _resolve_entry(
    rec: Recommendation,
    feature_snapshot: dict[str, Any],
    future_bars: list[DailyKbar],
    horizon_days: int,
) -> tuple[int, float, str] | None:
    entry_targets = [
        value for value in (
            _as_float(feature_snapshot.get("ideal_buy")),
            _as_float(feature_snapshot.get("secondary_buy")),
        )
        if value > 0
    ]
    entry_window = future_bars[: max(1, min(ENTRY_LOOKAHEAD_DAYS, horizon_days, len(future_bars)))]
    for bar_index, bar in enumerate(entry_window):
        low = _as_float(bar.low)
        bar_open = _as_float(bar.open)
        for target in entry_targets:
            if low <= target:
                return bar_index, min(bar_open, target) if bar_open > 0 else target, str(bar.trade_date)

    if entry_targets:
        return None

    first_bar = future_bars[0]
    raw_entry_price = _as_float(first_bar.open) or _as_float(first_bar.close)
    if raw_entry_price <= 0:
        return None
    return 0, raw_entry_price, str(first_bar.trade_date)


def _resolve_exit(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
) -> tuple[float, str, str]:
    for bar in holding_bars:
        bar_open = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        if bar_open > 0 and low > 0:
            if bar_open <= stop_loss_price:
                return bar_open, str(bar.trade_date), "stop_loss_gap"
            if bar_open >= take_profit_price:
                return bar_open, str(bar.trade_date), "take_profit_gap"
        if low <= stop_loss_price and high >= take_profit_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_intraday_both_hit"
        if low <= stop_loss_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_hit"
        if high >= take_profit_price:
            return take_profit_price, str(bar.trade_date), "take_profit_hit"

    last_bar = holding_bars[-1]
    return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"


def _resolve_stop_loss_price(raw_entry_price: float | None, feature_snapshot: dict[str, Any]) -> float:
    """解析止损价，加入归一化逻辑防止过紧的止损。

    止损价必须在 [min_stop, max_stop] 范围内：
    - max_stop = entry * (1 + DEFAULT_STOP_LOSS_PCT/100) = 默认 -4%（最紧）
    - min_stop = entry * (1 + MIN_STOP_LOSS_PCT/100) = 最小 -8%（最宽）
    """
    raw_stop = _as_float(feature_snapshot.get("stop_loss"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_stop, 2) if raw_stop > 0 else 0.0
    max_stop = raw_entry_price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)
    if raw_stop <= 0 or raw_stop >= raw_entry_price:
        return round(max_stop, 2)
    min_stop = raw_entry_price * (1 + MAX_STOP_LOSS_PCT / 100.0)
    normalized = min(raw_stop, max_stop)   # 不紧于 -4%
    normalized = max(normalized, min_stop) # 不宽于 -8%
    return round(normalized, 2)


def _resolve_take_profit_price(raw_entry_price: float | None, feature_snapshot: dict[str, Any]) -> float:
    """解析止盈价，加入上限归一化防止过高的止盈目标。"""
    raw_target = _as_float(feature_snapshot.get("take_profit"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_target, 2) if raw_target > 0 else 0.0
    fallback = raw_entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0)
    if raw_target <= raw_entry_price:
        return round(fallback, 2)
    max_tp = raw_entry_price * (1 + MAX_TAKE_PROFIT_PCT / 100.0)
    normalized = min(raw_target, max_tp)
    return round(normalized, 2)


def _resolve_position_size(feature_snapshot: dict[str, Any]) -> float:
    label = str(feature_snapshot.get("suggested_position") or "")
    if "大仓" in label:
        return 0.75
    if "中仓" in label:
        return 0.55
    if "小仓" in label:
        return 0.35
    if "观察" in label:
        return 0.15
    return 1.0


def _build_outcome_reason(exit_reason: str, executed_return: float, close_return: float) -> str:
    label = {
        "stop_loss_gap": "开盘跳空止损",
        "take_profit_gap": "开盘跳空止盈",
        "stop_loss_intraday_both_hit": "同日同时触发止盈止损，按保守止损处理",
        "stop_loss_hit": "盘中触发止损",
        "take_profit_hit": "盘中触发止盈",
        "horizon_close": "持有到观察期结束",
    }.get(exit_reason, exit_reason)
    return f"{label}；执行收益 {executed_return:.2f}%；静态收盘收益 {close_return:.2f}%"


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return {} if default is None else default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {} if default is None else default

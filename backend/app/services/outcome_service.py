import json
import threading
import time
from bisect import bisect_left
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun

ENTRY_LOOKAHEAD_DAYS = 2
ENTRY_SLIPPAGE_BPS = 10.0
EXIT_SLIPPAGE_BPS = 10.0
SUCCESS_RETURN_PCT = 3.0
DEFAULT_STOP_LOSS_PCT = -4.0
DEFAULT_TAKE_PROFIT_PCT = 8.0

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
    stmt = select(Recommendation).where(Recommendation.status == "pending")
    if run_mode:
        stmt = stmt.join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id).where(
            RecommendationRun.run_mode == run_mode
        )
    recs = db.scalars(stmt.order_by(Recommendation.trade_date.asc())).all()
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
        rec.status = outcome.outcome_label
        labeled += 1

    db.commit()
    if labeled:
        invalidate_outcome_metrics_cache()
    return {"labeled": labeled}


def overview_metrics(db: Session) -> dict[str, Any]:
    return _cached("overview", lambda: _overview_metrics(db))


def _overview_metrics(db: Session) -> dict[str, Any]:
    total_recs = db.scalar(select(func.count()).select_from(Recommendation)) or 0
    outcomes = db.scalars(select(RecommendationOutcome)).all()

    if not outcomes:
        return {
            "total_recommendations": total_recs,
            "labeled_count": 0,
            "triggered_count": 0,
            "trigger_rate": 0,
            "hit_rate": 0,
            "legacy_hit_rate": 0,
            "avg_return": 0,
            "avg_executed_return": 0,
            "avg_weighted_return": 0,
            "avg_close_return": 0,
            "avg_achievable_return": 0,
            "avg_mfe": 0,
            "avg_mae": 0,
            "avg_holding_days": 0,
        }

    triggered = [o for o in outcomes if is_trade_executed(o)]
    success_new = sum(1 for o in triggered if o.outcome_label == "success")
    success_legacy = sum(1 for o in outcomes if o.outcome_label_legacy == "success")
    n = len(outcomes)
    executed_n = len(triggered)

    return {
        "total_recommendations": total_recs,
        "labeled_count": n,
        "triggered_count": executed_n,
        "trigger_rate": round(executed_n / n, 3) if n else 0.0,
        "hit_rate": round(success_new / executed_n, 3) if executed_n else 0.0,
        "legacy_hit_rate": round(success_legacy / n, 3) if n else 0.0,
        "avg_return": round(sum(resolved_trade_return(o) for o in triggered) / executed_n, 2) if executed_n else 0.0,
        "avg_executed_return": round(sum(resolved_trade_return(o) for o in triggered) / executed_n, 2) if executed_n else 0.0,
        "avg_weighted_return": round(sum(float(o.weighted_return or 0) for o in triggered) / executed_n, 2) if executed_n else 0.0,
        "avg_close_return": round(sum(float(o.close_return or 0) for o in outcomes) / n, 2) if n else 0.0,
        "avg_achievable_return": round(sum(float(o.achievable_return or 0) for o in outcomes) / n, 2) if n else 0.0,
        "avg_mfe": round(sum(float(o.max_return or 0) for o in outcomes) / n, 2) if n else 0.0,
        "avg_mae": round(sum(float(o.min_return or 0) for o in outcomes) / n, 2) if n else 0.0,
        "avg_holding_days": round(sum(float(o.holding_days or 0) for o in triggered) / executed_n, 2) if executed_n else 0.0,
    }


def metrics_by_strategy(db: Session) -> list[dict[str, Any]]:
    return _cached("by_strategy", lambda: _metrics_by_strategy(db))


def _metrics_by_strategy(db: Session) -> list[dict[str, Any]]:
    rec_rows = db.execute(
        select(Recommendation.recommendation_id, Recommendation.skill_breakdown_json)
    ).all()

    outcome_rows = db.execute(
        select(
            RecommendationOutcome.recommendation_id,
            RecommendationOutcome.outcome_label,
            RecommendationOutcome.entry_triggered,
            RecommendationOutcome.executed_return,
        )
    ).all()
    outcome_map = {row.recommendation_id: row for row in outcome_rows}

    buckets: dict[str, dict[str, Any]] = {}
    for rec in rec_rows:
        outcome = outcome_map.get(rec.recommendation_id)
        executed = is_trade_executed(outcome)
        trade_return = resolved_trade_return(outcome)
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
                if outcome and outcome.outcome_label == "success":
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
            "outcome_label": "failed",
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
    outcome_label = "success" if (executed_return >= SUCCESS_RETURN_PCT and not hit_invalid) else "failed"

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
            "outcome_label_legacy": "failed",
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
    legacy_label = "success" if (max_return >= 5 and min_return > -4) else "failed"
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
    raw_stop = _as_float(feature_snapshot.get("stop_loss"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_stop, 2) if raw_stop > 0 else 0.0
    fallback = raw_entry_price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)
    if raw_stop <= 0 or raw_stop >= raw_entry_price:
        return fallback
    return raw_stop


def _resolve_take_profit_price(raw_entry_price: float | None, feature_snapshot: dict[str, Any]) -> float:
    raw_target = _as_float(feature_snapshot.get("take_profit"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_target, 2) if raw_target > 0 else 0.0
    fallback = raw_entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0)
    if raw_target <= raw_entry_price:
        return fallback
    return raw_target


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

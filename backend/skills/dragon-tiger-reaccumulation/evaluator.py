from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date
from math import isfinite
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from dateutil.relativedelta import relativedelta

from app.engine.board_rules import limit_pct_for_stock, limit_price, previous_close_from_price
from app.engine.skill_loader import load_skill
from app.engine.strategy_runner import run_strategy


SKILL = load_skill(Path(__file__).resolve().parent)


def build_context(
    code: str,
    signal_date: str,
    bars: Sequence[Any],
    events: Sequence[Any],
    metadata: Mapping[str, Any],
    calendar: Sequence[str],
) -> dict[str, Any]:
    start = (date.fromisoformat(signal_date) - relativedelta(months=1)).isoformat()
    history = [bar for bar in bars if start <= str(bar.trade_date) <= signal_date]
    context = {**dict(metadata), "code": code, "exchange": metadata.get("market"), "dtr_data_ready": False}
    if not history or str(history[-1].trade_date) != signal_date:
        return context
    if not metadata or bool(metadata.get("is_st")):
        return context
    expected = [day for day in calendar if start <= day <= signal_date]
    if [str(bar.trade_date) for bar in history] != expected or len(history) < 10:
        return context
    for bar in history:
        values = [float(getattr(bar, key)) for key in ("open", "high", "low", "close", "volume", "pct_chg")]
        if not all(isfinite(value) for value in values) or min(values[:5]) <= 0:
            return context
    for before, after in zip(history, history[1:]):
        reference = previous_close_from_price(after.close, after.pct_chg)
        if abs(reference / float(before.close) - 1) > 0.02:
            return context
    prior_events = [event for event in events if start <= str(event.trade_date) < signal_date]
    prior_events.sort(key=lambda event: str(event.trade_date))
    dates = [str(bar.trade_date) for bar in history]
    # 龙虎榜金额只保留原始事件说明；多日榜与单日榜不累加推断持仓。
    limit_pct = limit_pct_for_stock(dict(context))
    limits = []
    for bar in history[:-1]:
        reference = previous_close_from_price(bar.close, bar.pct_chg)
        upper = limit_price(reference, limit_pct, up=True)
        if float(bar.pct_chg) >= limit_pct - 0.2 and abs(float(bar.close) - upper) <= 0.011:
            limits.append(str(bar.trade_date))
    context.update({"dtr_event_count": len(prior_events), "dtr_limit_count": len(limits)})
    if not prior_events or not limits:
        return context
    event = prior_events[-1]
    event_index = bisect_left(dates, str(event.trade_date))
    if event_index >= len(history) or dates[event_index] != str(event.trade_date):
        return context
    current = history[-1]
    event_bar = history[event_index]
    after_event = history[event_index + 1:-1]
    if len(after_event) < 2:
        return context
    consolidation = after_event[-3:]
    low = min(float(bar.low) for bar in consolidation)
    high = max(float(bar.high) for bar in consolidation)
    day_range = float(current.high) - float(current.low)
    age = bisect_right(calendar, signal_date) - bisect_right(calendar, str(event.trade_date))
    context.update({
        "dtr_data_ready": True,
        "dtr_event_age": age,
        "dtr_volume_contraction": mean(float(bar.volume) for bar in consolidation) / float(event_bar.volume),
        "dtr_range_pct": (high / low - 1) * 100,
        "dtr_support_ratio": min(float(bar.low) for bar in after_event) / float(event_bar.low),
        "dtr_event_return_pct": (float(current.close) / float(event_bar.close) - 1) * 100,
        "dtr_restart_volume": float(current.volume) / mean(float(bar.volume) for bar in consolidation),
        "dtr_close_location": (float(current.close) - float(current.low)) / day_range if day_range > 0 else 0.0,
        "dtr_near_range_high": float(current.close) >= high * 0.98,
        "pct": float(current.pct_chg),
        "price": float(current.close),
        "close": float(current.close),
        "open": float(current.open),
        "high": float(current.high),
        "low": float(current.low),
        "volume": float(current.volume),
        "amount_yi": float(current.amount) / 1e8,
        "is_limit_up": float(current.pct_chg) >= limit_pct - 0.2,
        "dtr_event_date": str(event.trade_date),
        "dtr_event_pct": float(event_bar.pct_chg),
        "dtr_event_reason": event.reason,
        "dtr_limit_dates": limits,
        "dtr_history_start": dates[0],
        "dtr_as_of": signal_date,
        "dtr_event_buy_yi": event.buy_amount_yi,
        "dtr_event_sell_yi": event.sell_amount_yi,
        "dtr_event_net_yi": event.net_amount_yi,
    })
    return context


def evaluate_history(**kwargs: Any):
    context = build_context(**kwargs)
    return context, run_strategy(SKILL, context, [])

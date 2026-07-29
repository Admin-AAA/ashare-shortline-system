"""执行确认对结算与取消反事实的统一政策。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.trading_costs import ADDITIONAL_COST_PCT
from app.models.tables import DailyKbar


_CANCEL_HORIZONS = (1, 3, 5)


def apply_execution_outcome_policy(
    simulation: dict[str, Any],
    execution_state: Any | None,
) -> dict[str, Any]:
    """将行情模拟投影为可计入交易统计的结果。

    买入前已取消的推荐没有真实执行资格：静态行情指标仍保留用于复盘，但所有交易
    字段归零，并投影为 missed，从 success/failed 和实际收益中排除。
    """
    if not _cancelled_before_entry(execution_state):
        return simulation
    return {
        **simulation,
        "entry_triggered": False,
        "entry_date": None,
        "entry_price": None,
        "exit_date": None,
        "exit_price": None,
        "executed_return": 0.0,
        "weighted_return": 0.0,
        "position_size": 0.0,
        "holding_days": 0,
        "entry_slippage_bps": 0.0,
        "exit_slippage_bps": 0.0,
        "exit_reason": "execution_cancelled_before_entry",
        "hit_target": False,
        "hit_invalid": False,
        "outcome_label": "missed",
        "outcome_label_legacy": "missed",
        "outcome_reason": "实时买入确认在入场前已取消，本票未计入交易胜负",
    }


def _cancelled_before_entry(state: Any | None) -> bool:
    return bool(
        state is not None
        and getattr(state, "cancelled_before_entry", False)
        and not getattr(state, "ever_buy_confirmed", False)
    )


def build_cancel_observations(
    db: Session,
    recommendations: list[Any],
    execution_states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """批量计算取消后 1/3/5 个交易日的反事实净收益及汇总。"""
    candidates: list[dict[str, Any]] = []
    items: dict[str, dict[str, Any]] = {}
    for rec in recommendations:
        recommendation_id = str(_value(rec, "recommendation_id") or "")
        state = execution_states.get(recommendation_id) or {}
        if not state.get("cancelled_before_entry") or state.get("ever_buy_confirmed"):
            continue
        cancel_at = _parse_cancel_datetime(state.get("first_cancelled_at"))
        cancel_price = _positive_float(state.get("first_cancelled_price"))
        base = {
            "reference_at": state.get("first_cancelled_at"),
            "reference_price": cancel_price,
            "cost_pct": ADDITIONAL_COST_PCT,
            "horizons": {f"{horizon}d": None for horizon in _CANCEL_HORIZONS},
        }
        items[recommendation_id] = {**base, "status": "pending"}
        if cancel_at is None or cancel_price is None:
            continue
        candidates.append({
            "recommendation_id": recommendation_id,
            "code": str(_value(rec, "code") or ""),
            "cancel_date": (cancel_at + timedelta(hours=8)).date().isoformat(),
            "cancel_price": cancel_price,
        })

    if candidates:
        codes = sorted({candidate["code"] for candidate in candidates if candidate["code"]})
        min_cancel_date = min(candidate["cancel_date"] for candidate in candidates)
        bars = db.scalars(
            select(DailyKbar)
            .where(DailyKbar.code.in_(codes), DailyKbar.trade_date > min_cancel_date)
            .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
        ).all()
        bars_by_code: dict[str, list[DailyKbar]] = {}
        for bar in bars:
            if float(bar.close or 0) > 0:
                bars_by_code.setdefault(str(bar.code), []).append(bar)

        for candidate in candidates:
            future = [
                bar for bar in bars_by_code.get(candidate["code"], [])
                if str(bar.trade_date) > candidate["cancel_date"]
            ]
            horizons: dict[str, Any] = {}
            for horizon in _CANCEL_HORIZONS:
                if len(future) < horizon:
                    horizons[f"{horizon}d"] = None
                    continue
                bar = future[horizon - 1]
                net_return = (
                    (float(bar.close) / candidate["cancel_price"] - 1.0) * 100.0
                    - ADDITIONAL_COST_PCT
                )
                horizons[f"{horizon}d"] = {
                    "trade_date": str(bar.trade_date),
                    "close": float(bar.close),
                    "net_return_pct": round(net_return, 2),
                }
            observed_count = sum(value is not None for value in horizons.values())
            items[candidate["recommendation_id"]] = {
                **items[candidate["recommendation_id"]],
                "status": "complete" if observed_count == len(_CANCEL_HORIZONS) else "partial",
                "horizons": horizons,
            }

    return {"items": items, "summary": _summarize_cancel_observations(items)}


def _summarize_cancel_observations(items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {"cancelled_count": len(items)}
    for horizon in _CANCEL_HORIZONS:
        key = f"{horizon}d"
        returns = [
            float(value["net_return_pct"])
            for item in items.values()
            if isinstance((value := item.get("horizons", {}).get(key)), dict)
            and value.get("net_return_pct") is not None
        ]
        summary[f"observed_{key}_count"] = len(returns)
        summary[f"average_{key}_return"] = round(sum(returns) / len(returns), 2) if returns else None
        if horizon == 5:
            avoided = sum(value <= 0 for value in returns)
            summary["avoided_loss_count_5d"] = avoided
            summary["avoided_loss_rate_5d"] = round(avoided * 100.0 / len(returns), 1) if returns else None
            summary["missed_gain_count_5d"] = len(returns) - avoided
    return summary


def _parse_cancel_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _value(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


__all__ = ["apply_execution_outcome_policy", "build_cancel_observations"]

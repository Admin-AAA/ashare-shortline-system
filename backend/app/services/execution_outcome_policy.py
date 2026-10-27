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

    买入前已取消，或从未确认买入且最终过期的推荐没有真实执行资格：静态行情指标
    仍保留用于复盘，但所有交易字段归零，并投影为 missed，从 success/failed 和实际
    收益中排除。
    """
    if not _excluded_before_entry(execution_state):
        return simulation
    expired = str(getattr(execution_state, "latest_decision_code", "") or "") == "expired"
    exit_reason = "execution_expired_before_entry" if expired else "execution_cancelled_before_entry"
    outcome_reason = (
        "实时买入确认从未命中且信号已过期，本票未计入交易胜负"
        if expired
        else "实时买入确认在入场前已取消，本票未计入交易胜负"
    )
    return {
        **simulation,
        "entry_triggered": False,
        "entry_date": None,
        "entry_price": None,
        "exit_date": None,
        "exit_price": None,
        "executed_return": 0.0,
        "net_return_pct": None,
        "weighted_return": 0.0,
        "position_size": 0.0,
        "holding_days": 0,
        "canonical_exit_evidence": False,
        "intraday_exit_evidence": None,
        "entry_slippage_bps": 0.0,
        "exit_slippage_bps": 0.0,
        "exit_reason": exit_reason,
        "hit_target": False,
        "hit_invalid": False,
        "outcome_label": "missed",
        "outcome_label_legacy": "missed",
        "outcome_reason": outcome_reason,
    }


def _excluded_before_entry(state: Any | None) -> bool:
    return bool(
        state is not None
        and not getattr(state, "ever_buy_confirmed", False)
        and (
            getattr(state, "cancelled_before_entry", False)
            or str(getattr(state, "latest_decision_code", "") or "") == "expired"
        )
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
        # 只取用到的三列（整行 ORM 要拉十几列 + 实例化，上千行时是主要耗时）
        bars = db.execute(
            select(DailyKbar.code, DailyKbar.trade_date, DailyKbar.close)
            .where(DailyKbar.code.in_(codes), DailyKbar.trade_date > min_cancel_date)
            .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
        ).all()
        bars_by_code: dict[str, list[Any]] = {}
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


def build_missed_observations(
    db: Session,
    recommendations: list[Any],
    outcomes: dict[str, Any],
) -> dict[str, Any]:
    """未触发买点（missed）的旁路参考观察。

    未触发买点的推荐没有真实成交，永远不进胜率/净收益口径；但"没买到到底是躲过亏损还是
    错过盈利"是复盘必需的信息。口径与「取消后观察」（``build_cancel_observations``）同构：
    以推荐价（``recommend_price``，信号日参考价，也是 outcome 静态指标的基准）为成本，
    持有到观察期末（第 ``horizon_days`` 个交易日）收盘，扣掉统一附加成本。

    完整性：只有行情已覆盖满 ``horizon_days`` 个交易日的样本才给数值，未满的返回
    ``pending`` 与已观察天数——绝不用被截断的窗口冒充"持有到期"。
    """
    items: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []
    for rec in recommendations:
        recommendation_id = str(_value(rec, "recommendation_id") or "")
        outcome = outcomes.get(recommendation_id)
        if outcome is None:
            continue
        if str(_value(outcome, "outcome_label") or "").lower() != "missed":
            continue
        if bool(_value(outcome, "entry_triggered")):
            continue
        horizon_days = int(_value(outcome, "horizon_days") or 5)
        items[recommendation_id] = {
            "status": "pending",
            "observed_days": 0,
            "horizon_days": horizon_days,
            "cost_pct": ADDITIONAL_COST_PCT,
            "net_return_pct": None,
        }
        code = str(_value(rec, "code") or "")
        trade_date = str(_value(rec, "trade_date") or "")
        if code and trade_date:
            candidates.append({
                "recommendation_id": recommendation_id,
                "code": code,
                "trade_date": trade_date,
                "horizon_days": horizon_days,
                "static_close_return": _optional_float(_value(outcome, "close_return")),
            })

    if candidates:
        codes = sorted({candidate["code"] for candidate in candidates})
        min_trade_date = min(candidate["trade_date"] for candidate in candidates)
        # 只取用到的三列：推荐历史全量 500 条时这里要扫 1.7 万根日K，整行 ORM 约 330ms
        bars = db.execute(
            select(DailyKbar.code, DailyKbar.trade_date, DailyKbar.close)
            .where(DailyKbar.code.in_(codes), DailyKbar.trade_date > min_trade_date)
            .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
        ).all()
        dates_by_code: dict[str, list[str]] = {}
        for bar in bars:
            if float(bar.close or 0) > 0:
                dates_by_code.setdefault(str(bar.code), []).append(str(bar.trade_date))
        for candidate in candidates:
            observed_days = min(
                candidate["horizon_days"],
                sum(
                    1
                    for trade_date in dates_by_code.get(candidate["code"], [])
                    if trade_date > candidate["trade_date"]
                ),
            )
            item = items[candidate["recommendation_id"]]
            item["observed_days"] = observed_days
            if observed_days < candidate["horizon_days"]:
                continue
            static_close_return = candidate["static_close_return"]
            if static_close_return is None:
                continue
            item["status"] = "complete"
            item["net_return_pct"] = round(static_close_return - ADDITIONAL_COST_PCT, 2)

    return {"items": items, "summary": _summarize_missed_observations(items)}


def _summarize_missed_observations(items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    returns = [
        float(item["net_return_pct"])
        for item in items.values()
        if item.get("net_return_pct") is not None
    ]
    avoided = sum(value <= 0 for value in returns)
    return {
        "missed_count": len(items),
        "observed_count": len(returns),
        "pending_count": len(items) - len(returns),
        "average_reference_return": round(sum(returns) / len(returns), 2) if returns else None,
        "avoided_loss_count": avoided,
        "avoided_loss_rate": round(avoided * 100.0 / len(returns), 1) if returns else None,
        "missed_gain_count": len(returns) - avoided,
        "return_basis": "static_close_return_at_recommend_price_minus_additional_cost",
    }


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


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _value(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


__all__ = [
    "apply_execution_outcome_policy",
    "build_cancel_observations",
    "build_missed_observations",
]

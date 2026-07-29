"""实时买入确认状态仓储：保存最新有效判断和首次 buy 事实。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import RecommendationExecutionState


_PERSISTABLE_DECISIONS = frozenset({"buy", "wait", "cancel", "expired"})


def persist_execution_assessments(db: Session, assessments: list[dict[str, Any]]) -> int:
    """幂等更新有效 assessment；not_ready/error 不覆盖已有状态。"""
    persisted = 0
    for assessment in assessments:
        decision = assessment.get("entry_decision") or {}
        decision_code = str(decision.get("code") or "")
        if (
            assessment.get("status") != "ok"
            or not assessment.get("fresh")
            or decision_code not in _PERSISTABLE_DECISIONS
        ):
            continue
        recommendation_id = str(assessment.get("recommendation_id") or "").strip()
        checked_at = _parse_datetime(assessment.get("checked_at"))
        if not recommendation_id or checked_at is None:
            continue

        state = db.get(RecommendationExecutionState, recommendation_id)
        is_first_buy = decision_code == "buy" and not bool(
            state and (state.ever_buy_confirmed or state.cancelled_before_entry)
        )
        if state is None:
            state = RecommendationExecutionState(
                recommendation_id=recommendation_id,
                latest_decision_code=decision_code,
                latest_checked_at=checked_at,
            )
            db.add(state)

        signal = assessment.get("strategy_signal") or {}
        state.latest_decision_code = decision_code
        state.latest_decision_label = str(decision.get("label") or "")
        state.latest_reason = str(decision.get("reason") or "") or None
        state.latest_checked_at = checked_at
        state.latest_quote_time = str(assessment.get("quote_time") or "") or None
        state.latest_data_as_of = str(assessment.get("data_as_of") or "") or None
        state.latest_price = _optional_float(assessment.get("current_price"))
        state.latest_strategy_id = str(assessment.get("strategy_id") or "") or None
        state.latest_setup_match = str(signal.get("setup_match") or "") or None
        state.latest_condition_json = json.dumps(
            assessment.get("condition_summary") or {}, ensure_ascii=False, default=str
        )
        if decision_code == "buy" and not bool(state.cancelled_before_entry):
            state.ever_buy_confirmed = True
        if is_first_buy:
            state.first_buy_confirmed_at = checked_at
            state.first_buy_confirmed_price = _optional_float(assessment.get("current_price"))
        if decision_code == "cancel" and not bool(state.ever_buy_confirmed):
            state.cancelled_before_entry = True
            if state.first_cancelled_at is None:
                state.first_cancelled_at = checked_at
                state.first_cancelled_price = _optional_float(assessment.get("current_price"))
                state.first_cancel_reason = str(decision.get("reason") or "") or None
        persisted += 1

    db.flush()
    return persisted


def load_execution_state_map(
    db: Session,
    recommendation_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """批量加载历史页需要的执行确认状态，避免逐行查询。"""
    if not recommendation_ids:
        return {}
    states = db.scalars(
        select(RecommendationExecutionState).where(
            RecommendationExecutionState.recommendation_id.in_(recommendation_ids)
        )
    ).all()
    return {
        state.recommendation_id: serialize_execution_state(state)
        for state in states
    }


def serialize_execution_state(state: RecommendationExecutionState) -> dict[str, Any]:
    """投影稳定的历史 API 契约；损坏的旧 JSON 按空对象降级。"""
    try:
        condition = json.loads(state.latest_condition_json or "{}")
    except (TypeError, ValueError):
        condition = {}
    if not isinstance(condition, dict):
        condition = {}
    legacy_cancel_reference = bool(
        state.cancelled_before_entry
        and not state.ever_buy_confirmed
        and state.latest_decision_code == "cancel"
    )
    first_cancelled_at = state.first_cancelled_at or (
        state.latest_checked_at if legacy_cancel_reference else None
    )
    first_cancelled_price = state.first_cancelled_price
    if first_cancelled_price is None and legacy_cancel_reference:
        first_cancelled_price = state.latest_price
    first_cancel_reason = state.first_cancel_reason or (
        state.latest_reason if legacy_cancel_reference else None
    )
    return {
        "latest_decision_code": state.latest_decision_code,
        "latest_decision_label": state.latest_decision_label,
        "latest_reason": state.latest_reason,
        "latest_checked_at": _isoformat(state.latest_checked_at),
        "latest_quote_time": state.latest_quote_time,
        "latest_data_as_of": state.latest_data_as_of,
        "latest_price": state.latest_price,
        "latest_strategy_id": state.latest_strategy_id,
        "latest_setup_match": state.latest_setup_match,
        "condition_summary": condition,
        "ever_buy_confirmed": bool(state.ever_buy_confirmed),
        "first_buy_confirmed_at": _isoformat(state.first_buy_confirmed_at),
        "first_buy_confirmed_price": state.first_buy_confirmed_price,
        "cancelled_before_entry": bool(state.cancelled_before_entry),
        "first_cancelled_at": _isoformat(first_cancelled_at),
        "first_cancelled_price": first_cancelled_price,
        "first_cancel_reason": first_cancel_reason,
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = [
    "load_execution_state_map",
    "persist_execution_assessments",
    "serialize_execution_state",
]

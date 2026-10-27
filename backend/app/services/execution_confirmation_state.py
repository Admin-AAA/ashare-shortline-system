"""实时买入确认状态仓储：保存最新有效判断和首次 buy 事实。

latest_condition_json 每次写入会换成最新一次的条件证据，但两类事实要跨写入保留（评审第 5 条）：
- ``coverage_gap*``：入场日盘中没被检查就过期的「轮询缺口」标记，一旦写上不被后续写入覆盖；
- ``session_checks``：每个交易日 09:30–15:00（北京时间）内最后一次「结论不是过期」的检查时间，
  覆盖率判断「入场日盘中是否被检查过」只认这里（收盘后的过期写入不算检查）。
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tables import RecommendationExecutionState


_PERSISTABLE_DECISIONS = frozenset({"buy", "wait", "cancel", "expired"})
_CST = timezone(timedelta(hours=8))
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(15, 0)
STICKY_CONDITION_KEYS = ("coverage_gap", "coverage_gap_entry_day", "coverage_gap_reason")
_MAX_SESSION_CHECK_DAYS = 5


def session_check_date(checked_at_utc: datetime | None) -> str | None:
    """naive UTC 检查时间若落在某交易日 09:30–15:00（北京时间）内，返回该日 YYYY-MM-DD，否则 None。"""
    if not isinstance(checked_at_utc, datetime):
        return None
    aware = checked_at_utc if checked_at_utc.tzinfo else checked_at_utc.replace(tzinfo=timezone.utc)
    local = aware.astimezone(_CST)
    if SESSION_OPEN <= local.time() <= SESSION_CLOSE:
        return local.date().isoformat()
    return None


def load_condition(state: Any) -> dict[str, Any]:
    try:
        value = json.loads(getattr(state, "latest_condition_json", None) or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _merge_condition(previous: dict[str, Any], new: dict[str, Any], *, decision_code: str,
                     checked_at: datetime) -> dict[str, Any]:
    merged = dict(new)
    if previous.get("coverage_gap"):
        for key in STICKY_CONDITION_KEYS:
            if key in previous:
                merged[key] = previous[key]
    checks = {str(k): str(v) for k, v in (previous.get("session_checks") or {}).items()} \
        if isinstance(previous.get("session_checks"), dict) else {}
    day = session_check_date(checked_at) if decision_code != "expired" else None
    if day is not None:
        checks[day] = checked_at.isoformat()
    if checks:
        merged["session_checks"] = dict(sorted(checks.items())[-_MAX_SESSION_CHECK_DAYS:])
    return merged


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
        if state is None:
            state = _insert_or_load(db, recommendation_id, decision_code, checked_at)
        is_first_buy = decision_code == "buy" and not bool(state.ever_buy_confirmed)

        signal = assessment.get("strategy_signal") or {}
        previous = load_condition(state)
        if previous.get("coverage_gap") and decision_code == "expired":
            # 轮询缺口标记已写上：后续再写「过期」不能把原因改回普通过期
            decision = {
                **decision,
                "label": "盘中漏查已过期",
                "reason": str(previous.get("coverage_gap_reason") or decision.get("reason") or ""),
            }
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
            _merge_condition(
                previous, dict(assessment.get("condition_summary") or {}),
                decision_code=decision_code, checked_at=checked_at,
            ),
            ensure_ascii=False, default=str,
        )
        # 入场窗口内一旦拿到买入确认就记为「确认过买入」，先前的取消不再屏蔽它：
        # 取消判断是窗口内某一时刻的采样，同一窗口后续恢复到买入是有效事实，否则一次
        # 采样就把这笔推荐永久踢出交易统计。
        if decision_code == "buy":
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


def _insert_or_load(db: Session, recommendation_id: str, decision_code: str, checked_at: datetime) -> RecommendationExecutionState:
    """新建状态行；若别的写入方（盘中轮询进程 / 前端接口）刚好先插入了同一主键，改为加载那一行再更新（upsert）。

    2026-10-26 09:31 实测：轮询与接口并发处理同一推荐，db.get 在 MySQL 可重复读快照里看不到对方刚提交的行，
    随后 INSERT 撞主键，整批 flush 失败。这里用 SAVEPOINT 隔离这次插入：撞键只回滚这一行的插入，
    不影响同批其他推荐；再用加锁读（读到最新已提交版本）取回对方那行继续更新。"""
    try:
        with db.begin_nested():
            state = RecommendationExecutionState(
                recommendation_id=recommendation_id,
                latest_decision_code=decision_code,
                latest_checked_at=checked_at,
            )
            db.add(state)
            db.flush()
        return state
    except IntegrityError:
        existing = db.execute(
            select(RecommendationExecutionState)
            .where(RecommendationExecutionState.recommendation_id == recommendation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if existing is None:
            raise
        return existing


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
    "STICKY_CONDITION_KEYS",
    "load_condition",
    "load_execution_state_map",
    "session_check_date",
    "persist_execution_assessments",
    "serialize_execution_state",
]

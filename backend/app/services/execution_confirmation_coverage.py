"""盘中买入确认覆盖率：入场日到点仍未被检查的待确认推荐要告警、进数据健康，不能收盘后悄悄判过期。

背景（2026-10-27 诊断）：10-14 以来单策略实盘 19 只推荐里有 7 只被判「过期」，原因都是入场日盘中
一次都没被检查——2026-10-20 22:55（078e5e0d）之前买入确认只在有人打开复盘页时由前端触发，
没人看页面的交易日，待确认推荐收盘后被一次性判成过期（missed）。服务端每 5 分钟轮询上线后，
仍可能因行情缺失/单票报错（不写状态）而漏查，所以这里补一道覆盖率检查：

- 入场日 09:45（首轮 09:30 轮询后留 15 分钟）起，入场窗口内、今天还没有任何检查记录的推荐 = 漏查；
- 轮询每轮结束时调用 ``report_coverage``：有漏查发告警 ``execution_confirmation_unchecked``，全覆盖则解除；
- 数据健康 ``check_confirmation_coverage``：今天有漏查 → 红；近 5 个交易日有「入场日盘中没被检查就过期」→ 黄；
- 批量确认判过期时若入场日没有盘中检查，原因写明「入场日盘中未被检查（轮询缺口）」，不再只写「今日已收盘」。

时间口径：执行状态表 ``latest_checked_at`` 存 UTC（L-151），本模块按北京时间算会话起点再换成 UTC 比较。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.models.recommendation import RecommendationExecutionState
from app.services.entry_day_checks import entry_window_days_for_rec

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
SESSION_OPEN = time(9, 30)
COVERAGE_GRACE = time(9, 45)
SESSION_CLOSE = time(15, 0)
ALERT_KIND = "execution_confirmation_unchecked"
RECENT_GAP_TRADE_DAYS = 5


def session_start_utc(trade_date: str) -> datetime:
    """入场日 09:30 北京时间 → naive UTC（与执行状态表时间口径一致）。"""
    start = datetime.combine(date.fromisoformat(trade_date), SESSION_OPEN, tzinfo=CST)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def was_checked_in_session(state: Any | None, trade_date: str) -> bool:
    """该推荐在 trade_date 的盘中（09:30 后）是否至少被检查过一次。"""
    checked_at = getattr(state, "latest_checked_at", None) if state is not None else None
    if not isinstance(checked_at, datetime):
        return False
    checked_at = checked_at.replace(tzinfo=None) if checked_at.tzinfo else checked_at
    return checked_at >= session_start_utc(trade_date)


def _market_days_after(db: Session, start: str, end: str) -> list[str]:
    return sorted(
        str(value)
        for value in db.scalars(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date > start, DailyKbar.trade_date <= end)
            .distinct()
        ).all()
    )


def entry_day_unchecked(
    db: Session,
    today: str,
    *,
    run_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    """今天处于入场窗口（推荐后第 1 个交易日；或第 2 日且第 1 日被回测规则挡住）却还没被检查的未结算推荐。"""
    from app.services.execution_confirmation import _first_trade_day_blocked, is_unsettled_formal_rec
    from app.services.outcome_simulation import _is_intraday_recommendation

    if not run_ids:
        return []
    rows = db.execute(
        select(Recommendation, RecommendationOutcome, RecommendationExecutionState)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .outerjoin(RecommendationExecutionState, RecommendationExecutionState.recommendation_id == Recommendation.recommendation_id)
        .where(Recommendation.run_id.in_(run_ids), Recommendation.trade_date < today)
    ).all()
    out: list[dict[str, Any]] = []
    for rec, outcome, state in rows:
        if not is_unsettled_formal_rec(rec, outcome) or _is_intraday_recommendation(rec):
            continue
        days = _market_days_after(db, str(rec.trade_date), today)
        age = len(set(days) | {today})
        if age > entry_window_days_for_rec(rec):
            continue
        if age >= 2 and not _first_trade_day_blocked(
            db, str(rec.code), str(rec.trade_date), today, float(rec.recommend_price or 0)
        ):
            continue
        if state is not None and (
            bool(getattr(state, "ever_buy_confirmed", False))
            or str(getattr(state, "latest_decision_code", "") or "") in {"expired", "cancel"}
        ):
            continue  # 已有最终结论（已确认买入 / 已过期 / 已取消）
        if was_checked_in_session(state, today):
            continue
        out.append({"recommendation_id": str(rec.recommendation_id), "code": str(rec.code), "name": rec.name,
                    "signal_date": str(rec.trade_date), "strategy_id": str(rec.bucket or "")})
    return out


def _eligible_run_ids(db: Session) -> tuple[str, ...]:
    from app.services.evidence.validation import canonical_live_forward_run_ids, catchup_formal_run_ids

    return tuple(canonical_live_forward_run_ids(db)) + tuple(catchup_formal_run_ids(db))


def report_coverage(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """轮询每轮结束时调用：入场日 09:45 后仍有漏查 → 发告警；全覆盖 → 解除告警。"""
    from app.core.alerts import emit_alert, resolve_alert

    now_cst = (now or datetime.now(timezone.utc)).astimezone(CST)
    today = now_cst.date().isoformat()
    if now_cst.time() < COVERAGE_GRACE:
        return {"checked": False, "reason": "before_grace"}
    unchecked = entry_day_unchecked(db, today, run_ids=_eligible_run_ids(db))
    if unchecked:
        sample = "、".join(f"{u['code']}({u['signal_date'][5:]})" for u in unchecked[:8])
        message = f"{today} 有 {len(unchecked)} 只待确认推荐入场日盘中尚未被检查：{sample}"
        logger.warning("盘中买入确认覆盖缺口：%s", message)
        emit_alert(ALERT_KIND, today, message)
    else:
        resolve_alert(ALERT_KIND, today, f"{today} 待确认推荐已全部检查")
    return {"checked": True, "unchecked": unchecked}


def annotate_unchecked_expiries(db: Session, assessments: list[dict[str, Any]]) -> int:
    """批量确认要写入「过期」时：若入场日盘中从没被检查过，原因改写为轮询缺口并告警（不再悄悄过期）。

    以写入前的已有状态判断：没有状态，或上次检查早于入场日 09:30 → 入场日盘中没被检查。
    返回改写条数。只改原因文字与 condition_summary 标记，不改过期这个结论（没确认买入就不能冒充成交）。
    """
    from app.core.alerts import emit_alert

    expired = [a for a in assessments if (a.get("entry_decision") or {}).get("code") == "expired"]
    if not expired:
        return 0
    ids = [str(a.get("recommendation_id")) for a in expired]
    states = {
        str(s.recommendation_id): s
        for s in db.scalars(
            select(RecommendationExecutionState).where(RecommendationExecutionState.recommendation_id.in_(ids))
        ).all()
    }
    changed = 0
    for item in expired:
        signal_date = str(item.get("trade_date") or (item.get("original") or {}).get("trade_date") or "")
        data_as_of = str(item.get("data_as_of") or "")
        if not signal_date or not data_as_of:
            continue
        later = _market_days_after(db, signal_date, data_as_of) or ([data_as_of] if data_as_of > signal_date else [])
        if not later:
            continue
        entry_day = later[0]
        state = states.get(str(item.get("recommendation_id")))
        if state is not None and was_checked_in_session(state, entry_day):
            continue
        reason = f"入场日 {entry_day} 盘中未被检查（轮询缺口），本票按规则过期，已告警"
        item["entry_decision"] = {"code": "expired", "label": "盘中漏查已过期", "reason": reason}
        summary = dict(item.get("condition_summary") or {})
        summary.update({"coverage_gap": True, "label": reason})
        item["condition_summary"] = summary
        emit_alert(ALERT_KIND, f"expired:{item.get('recommendation_id')}", f"{item.get('code')} {reason}")
        changed += 1
    return changed


def recent_expired_without_check(db: Session, today: str, *, trade_days: int = RECENT_GAP_TRADE_DAYS) -> list[dict[str, Any]]:
    """近 N 个交易日内「入场日盘中没被检查就被判过期」的推荐（静默失败证据）。"""
    days = sorted(
        str(v) for v in db.scalars(
            select(DailyKbar.trade_date).where(DailyKbar.trade_date <= today).distinct()
            .order_by(DailyKbar.trade_date.desc()).limit(trade_days + 1)
        ).all()
    )
    if not days:
        return []
    rows = db.execute(
        select(Recommendation, RecommendationExecutionState)
        .join(RecommendationExecutionState, RecommendationExecutionState.recommendation_id == Recommendation.recommendation_id)
        .where(Recommendation.trade_date >= days[0], Recommendation.trade_date < today,
               Recommendation.run_id.in_(_eligible_run_ids(db)),
               RecommendationExecutionState.latest_decision_code == "expired",
               RecommendationExecutionState.ever_buy_confirmed.is_(False))
    ).all()
    out = []
    for rec, state in rows:
        later = [d for d in days if d > str(rec.trade_date)]
        if not later:
            continue
        entry_day = later[0]
        # 执行状态只保留首建时间与最新检查时间：首建已在入场日收盘之后 = 收盘前从未被检查（确定的漏查）。
        # 首建早于收盘但最新检查被收盘后的过期覆盖的，无法区分，不计入（宁可少报，不误报）。
        created_at = getattr(state, "created_at", None)
        end_of_entry_day = datetime.combine(
            date.fromisoformat(entry_day), SESSION_CLOSE, tzinfo=CST,
        ).astimezone(timezone.utc).replace(tzinfo=None)
        if isinstance(created_at, datetime) and created_at.replace(tzinfo=None) < end_of_entry_day:
            continue
        out.append({"code": str(rec.code), "signal_date": str(rec.trade_date), "entry_day": entry_day})
    return out


__all__ = [
    "ALERT_KIND",
    "annotate_unchecked_expiries",
    "entry_day_unchecked",
    "recent_expired_without_check",
    "report_coverage",
    "session_start_utc",
    "was_checked_in_session",
]

"""推荐次日执行确认：用当前全市场快照重跑原推荐主策略。

单票入口（confirm_recommendation_execution）保留 Agent 增强能力；批量入口
（app.services.batch_execution_confirmation）传入 review=None，按高置信规则
兜底决策。两者共享 confirm_with_prepared_context——"已准备上下文下确认单笔"
的纯函数，避免实时判断与单票确认产生两套规则。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.skill_loader import load_skills, StrategySkill
from app.engine.strategy_runner import explain_skill_prefilter, run_strategy
from app.engine.trading_calendar import latest_allowed_quote_date
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome
from app.services.evidence.validation import canonical_live_forward_run_ids
from app.services.outcome_simulation import _is_intraday_recommendation


def _decision(code: str, label: str, reason: str) -> dict[str, str]:
    return {"code": code, "label": label, "reason": reason}


def _json_mapping(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


# 未最终结算的 outcome label：无 outcome 行、pending、provisional 都视为未结算，
# 仍可进入实时确认。success/failed/missed 及未来新增的最终结算标签都视为已结算。
# 复用 outcome_simulation 的口径，不在前后端各维护一份容易分叉的硬编码列表。
_UNSETTLED_OUTCOME_LABELS = frozenset({"pending", "provisional"})


def is_unsettled_formal_rec(rec: Recommendation, outcome: RecommendationOutcome | None) -> bool:
    """推荐是否仍未最终结算且属于 formal run（可进入实时确认）。

    复用 realtime_pnl_service._load_pending_recs 的未结算 SQL 语义：无 outcome
    行、或 outcome_label 为 pending / provisional。任何最终标签（success/failed/
    missed 及未来新增）都返回 False。run_mode 由调用方在 SQL 层保证，此处只判
    outcome 维度，便于单票与批量共用。
    """
    if outcome is None:
        return True
    return str(getattr(outcome, "outcome_label", "") or "").lower() in _UNSETTLED_OUTCOME_LABELS


def _entry_window_closed(data_as_of: str, local_now: datetime) -> bool:
    """入场窗口是否已关闭（不再追认盘中买点）。

    data_as_of 落在今天且已收盘（≥15:00），或快照早于今天（周末 latest_allowed_quote_date
    回退到周五，fresh=True 但 data_as_of < 今天，此前窗口判定恒 False 致周末重新输出 buy）。
    """
    today_iso = local_now.date().isoformat()
    return (data_as_of == today_iso and local_now.hour >= 15) or data_as_of < today_iso


def _adjust_stop_loss_for_ex_right(
    db: Session,
    code: str,
    signal_date: str,
    data_as_of: str,
    stop_loss: float,
) -> float:
    """把推荐日裸价止损基准按除权因子缩放到 data_as_of 口径，规避除权误判。

    stop_loss 存的是推荐日裸价（market_data.py 由 ma20*0.95 算出）。若推荐日到今日发生
    除权（10送10 价格腰斩），裸价比 current_price <= stop_loss 会把翻倍的复权后股价误判
    为跌破止损。Tushare 的 pct_chg 在除权日已调整前收（≈0），无法用它捕捉价格跳变；
    这里直接用「除权日 open / 前日 close」累乘出除权因子，缩放止损基准。无除权时因子=1。
    """
    if stop_loss <= 0 or not signal_date or not data_as_of or data_as_of <= signal_date:
        return stop_loss
    bars = db.scalars(
        select(DailyKbar).where(
            DailyKbar.code == code,
            DailyKbar.trade_date > signal_date,
            DailyKbar.trade_date <= data_as_of,
        ).order_by(DailyKbar.trade_date)
    ).all()
    if not bars:
        return stop_loss
    # 取 signal_date 当日 close 作除权跳变的前收基准；缺失则用首根 bar 的 open 兜底
    signal_close = db.scalar(
        select(DailyKbar.close).where(
            DailyKbar.code == code, DailyKbar.trade_date == signal_date,
        )
    )
    factor = 1.0
    prev_close = float(signal_close or 0) or float(getattr(bars[0], "open", 0) or 0)
    for bar in bars:
        cur_open = float(getattr(bar, "open", 0) or 0)
        cur_pct = float(getattr(bar, "pct_chg", 0) or 0)
        if prev_close > 0 and cur_open > 0:
            price_jump = (cur_open / prev_close - 1) * 100
            # 除权日：价格跳变 > 20% 但 pct_chg 在涨停幅度内（pct_chg 已除权调整）
            if abs(price_jump) > 20 and abs(cur_pct) < 12:
                factor *= cur_open / prev_close
        prev_close = float(getattr(bar, "close", 0) or 0) or prev_close
    if factor <= 0:
        return stop_loss
    return round(stop_loss * factor, 2)


def _derive_decisions(
    *,
    fresh: bool,
    signal_age_trade_days: int,
    entry_window_closed: bool,
    prefilter_ok: bool,
    signal: dict[str, Any],
    review: dict[str, Any] | None,
    current_price: float,
    stop_loss: float,
    is_intraday_recommendation: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """把规则信号与策略审查投影为未入场/已持仓两套动作。"""
    if not fresh:
        reason = "当前行情快照不是最新交易日，等待数据更新后再确认"
        return _decision("not_ready", "数据未就绪", reason), _decision("observe", "等待最新数据", reason)

    stance = str((review or {}).get("stance") or "")
    action = str((review or {}).get("action") or "")
    review_available = bool(review and review.get("status") == "ok")
    hard_veto = bool(signal.get("hard_veto"))
    strategy_vetoed = hard_veto or stance in {"oppose", "veto"} or action == "不做"
    entry_blocked = strategy_vetoed or not prefilter_ok
    stop_broken = stop_loss > 0 and current_price > 0 and current_price <= stop_loss
    rule_supportive = bool(signal.get("setup_match")) and float(signal.get("score") or 0) >= 72
    supportive = stance in {"strong_support", "support"} or (not review_available and rule_supportive)

    if signal_age_trade_days < 1 and not is_intraday_recommendation:
        entry = _decision("not_ready", "盘后候选，下一交易日确认", "盘后生成，当前不可买；请在下一交易日按实时条件确认")
    elif is_intraday_recommendation and signal_age_trade_days > 0:
        entry = _decision("expired", "盘中买点已过期", "盘中买点仅在推荐当日有效，请勿追入")
    elif not is_intraday_recommendation and signal_age_trade_days > 1:
        entry = _decision("expired", "次日确认窗口已过期", "已错过推荐后的首个交易日，请等待新的推荐信号")
    elif entry_window_closed:
        entry = _decision("expired", "盘中买点已过执行窗口", "今日已收盘，本次买点不再有效，请勿追入")
    elif stop_broken:
        entry = _decision("cancel", "取消买入", "当前价格已经跌破原推荐止损位")
    elif entry_blocked:
        reason = str(
            (signal.get("prefilter_reason") if not prefilter_ok else "")
            or signal.get("veto_reason")
            or (review or {}).get("summary")
            or "原策略当前不再支持入场"
        )
        entry = _decision("cancel", "取消买入", reason)
    elif signal.get("setup_match") and float(signal.get("score") or 0) >= 60 and supportive:
        entry = _decision("buy", "允许按策略轻仓执行", str((review or {}).get("summary") or signal.get("entry_rule") or "当前买点仍成立"))
    else:
        entry = _decision("wait", "继续观察", str((review or {}).get("summary") or "没有形成足够明确的当前入场确认"))

    if stop_broken:
        holding = _decision("exit", "退出持仓", "当前价格已经跌破原推荐止损位")
    elif strategy_vetoed:
        reason = str(signal.get("veto_reason") or (review or {}).get("summary") or "原策略逻辑已经失效")
        holding = _decision("exit", "原策略已失效，建议退出", reason)
    elif not prefilter_ok:
        holding = _decision("reduce", "禁止加仓，降低仓位观察", "当前已不满足原策略候选条件")
    elif supportive:
        holding = _decision("hold", "继续持有，严格执行保护位", str((review or {}).get("summary") or "原策略逻辑仍成立"))
    else:
        holding = _decision("reduce", "禁止加仓，收紧保护位观察", str((review or {}).get("summary") or "当前策略未明确支持继续持有"))
    return entry, holding


def _run_agent_review(skill: Any, stock: dict[str, Any], snapshot: dict[str, Any], signal: dict[str, Any]) -> dict[str, Any]:
    from app.agents.agno_skill_agent import drain_api_costs, run_skill_agent_direct

    try:
        output = asyncio.run(
            run_skill_agent_direct(skill=skill, stock=stock, snapshot=snapshot, rule_signal=signal)
        )
        raw = output.model_dump() if hasattr(output, "model_dump") else dict(output)
        raw["status"] = "ok"
        costs = drain_api_costs()
        raw["elapsed_cost_calls"] = len(costs)
        return raw
    except Exception as exc:  # Agent 只增强解释，失败不能阻断规则确认
        return {"status": "fallback", "error": str(exc)}


def _build_signal_for_strategy(
    skill: StrategySkill,
    current: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], bool, str]:
    """对单只已 enriched 的 stock 跑原策略 prefilter + 运行，返回带 prefilter 元数据的 signal。"""
    prefilter_detail = explain_skill_prefilter(skill, current)
    prefilter_ok = bool(prefilter_detail["passed"])
    prefilter_reason = "pass" if prefilter_ok else str(prefilter_detail["label"])
    signal = dict(run_strategy(skill, current, list(snapshot.get("main_industries") or [])))
    signal["prefilter_ok"] = prefilter_ok
    signal["prefilter_reason"] = prefilter_reason
    signal["prefilter_detail"] = prefilter_detail
    return signal, prefilter_ok, prefilter_reason


def confirm_with_prepared_context(
    db: Session,
    rec: Recommendation,
    *,
    snapshot: dict[str, Any],
    current: dict[str, Any],
    skill: StrategySkill,
    signal: dict[str, Any],
    prefilter_ok: bool,
    signal_age_trade_days: int,
    review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在已准备好的市场上下文（snapshot/enriched stock/策略 signal）下确认一笔推荐。

    单票确认（含 Agent）与批量确认（review=None）共用此函数，保证两套入口不会
    产生两套规则。调用方负责准备 snapshot、enriched stock、signal 和交易日年龄，
    本函数只做决策投影与元数据组装，不写库、不拉行情。

    signal_age_trade_days 由调用方按各自的数据加载方式计算（单票用 SQL，批量用
    已加载的历史 K），但语义一致：推荐日之后到 data_as_of（含）之间的真实交易日数。
    """
    code = str(rec.code)
    data_as_of = str(snapshot.get("trade_date") or current.get("latest_trade_date") or "")

    local_now = datetime.now()
    expected_quote_date = latest_allowed_quote_date()
    fresh = data_as_of == expected_quote_date
    entry_window_closed = _entry_window_closed(data_as_of, local_now)

    current_price = float(current.get("price") or current.get("close") or 0)
    original_snapshot = _json_mapping(rec.feature_snapshot_json)
    stop_loss = float(original_snapshot.get("stop_loss") or 0)
    # 止损基准跨除权修正：stop_loss 是推荐日裸价，若推荐日到今日发生除权（10送10 等），
    # 裸价比会误判跌破止损。用 pct_chg 链累乘出复权因子，把止损基准缩放到今日口径。
    adjusted_stop_loss = _adjust_stop_loss_for_ex_right(db, code, str(rec.trade_date), data_as_of, stop_loss)
    entry_decision, holding_decision = _derive_decisions(
        fresh=fresh,
        signal_age_trade_days=signal_age_trade_days,
        entry_window_closed=entry_window_closed,
        prefilter_ok=prefilter_ok,
        signal=signal,
        review=review,
        current_price=current_price,
        stop_loss=adjusted_stop_loss,
        is_intraday_recommendation=_is_intraday_recommendation(rec),
    )
    condition_summary = _condition_summary(signal, prefilter_ok, entry_decision)

    strategy_id = str(rec.bucket or "daily-stock-analysis-skill")
    return {
        "status": "ok" if fresh else "not_ready",
        "recommendation_id": rec.recommendation_id,
        "code": code,
        "name": rec.name,
        "strategy_id": strategy_id,
        "strategy_name": skill.name,
        "checked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "data_as_of": data_as_of,
        "quote_time": current.get("quote_time"),
        "snapshot_generated_at": (snapshot.get("breadth") or {}).get("generated_at"),
        "data_source": current.get("quote_source") or (snapshot.get("breadth") or {}).get("quote_source"),
        "fresh": fresh,
        "signal_age_trade_days": signal_age_trade_days,
        "current_price": current_price,
        "entry_decision": entry_decision,
        "holding_decision": holding_decision,
        "condition_summary": condition_summary,
        "original": {
            "trade_date": str(rec.trade_date),
            "recommend_price": float(rec.recommend_price or 0),
            "stop_loss": stop_loss or None,
            "entry_rule": rec.entry_rule,
            "invalid_rule": rec.invalid_rule,
        },
        "market": {
            "regime": (snapshot.get("breadth") or {}).get("regime"),
            "up_count": snapshot.get("up_count"),
            "down_count": snapshot.get("down_count"),
            "limit_up_count": snapshot.get("limit_up_count"),
            "limit_down_count": snapshot.get("limit_down_count"),
            "main_industries": list(snapshot.get("main_industries") or []),
        },
        "strategy_signal": {
            **signal,
        },
        "agent_review": review,
    }


def _condition_summary(
    signal: dict[str, Any],
    prefilter_ok: bool,
    entry_decision: dict[str, str],
) -> dict[str, Any]:
    if not prefilter_ok:
        detail = signal.get("prefilter_detail")
        if isinstance(detail, dict):
            return {**detail, "status": "failed"}
    setup_match = str(signal.get("setup_match") or "").strip()
    return {
        "passed": entry_decision.get("code") == "buy",
        "status": "passed" if entry_decision.get("code") == "buy" else "failed",
        "scope": "setup" if setup_match else "strategy",
        "code": entry_decision.get("code") or "unknown",
        "label": setup_match or entry_decision.get("reason") or "未形成明确买点",
        "expression": str(signal.get("entry_rule") or ""),
        "observed": {"score": float(signal.get("score") or 0)},
    }


def confirm_recommendation_execution(
    db: Session,
    recommendation_id: str,
    *,
    include_agent: bool = True,
) -> dict[str, Any] | None:
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    canonical_run_ids = canonical_live_forward_run_ids(
        db,
        trade_date=str(rec.trade_date),
    )
    if str(rec.run_id) not in canonical_run_ids:
        return None
    outcome = db.get(RecommendationOutcome, recommendation_id)
    if not is_unsettled_formal_rec(rec, outcome):
        return None

    from app.core.config import get_settings
    from app.services.auxiliary.application import (
        inject_auxiliary_prefilter_flags,
        load_auxiliary_feature_views,
    )
    from app.services.feature_builder import build_features
    from app.services.factors.registry import bootstrap_default_factor_registry
    from app.services.market_data import get_market_universe

    bootstrap_default_factor_registry()
    stocks, snapshot = get_market_universe()
    code = str(rec.code)
    target = next((stock for stock in stocks if str(stock.get("code")) == code), None)
    if target is None:
        return {
            "status": "error",
            "recommendation_id": recommendation_id,
            "message": f"当前可交易股票池中找不到 {code}",
        }

    data_as_of = str(snapshot.get("trade_date") or target.get("latest_trade_date") or "")
    auxiliary = load_auxiliary_feature_views(db, [code], trade_date=data_as_of)
    inject_auxiliary_prefilter_flags([target], auxiliary)
    enriched = build_features(stocks, snapshot)
    current = next(stock for stock in enriched if str(stock.get("code")) == code)

    skills = {skill.skill_id: skill for skill in load_skills(get_settings().skills_dir)}
    strategy_id = str(rec.bucket or "daily-stock-analysis-skill")
    skill = skills.get(strategy_id)
    if skill is None:
        return {
            "status": "error",
            "recommendation_id": recommendation_id,
            "message": f"原推荐策略 {strategy_id} 已不存在",
        }

    signal, prefilter_ok, _prefilter_reason = _build_signal_for_strategy(skill, current, snapshot)

    later_dates = set(
        str(value)
        for value in db.scalars(
            select(DailyKbar.trade_date).where(
                DailyKbar.code == code,
                DailyKbar.trade_date > str(rec.trade_date),
                DailyKbar.trade_date <= data_as_of,
            )
        ).all()
    )
    if data_as_of > str(rec.trade_date):
        later_dates.add(data_as_of)
    signal_age_trade_days = len(later_dates)

    expected_quote_date = latest_allowed_quote_date()
    fresh = data_as_of == expected_quote_date
    review = _run_agent_review(skill, current, snapshot, signal) if include_agent and fresh else None

    result = confirm_with_prepared_context(
        db,
        rec,
        snapshot=snapshot,
        current=current,
        skill=skill,
        signal=signal,
        prefilter_ok=prefilter_ok,
        signal_age_trade_days=signal_age_trade_days,
        review=review,
    )
    from app.services.execution_confirmation_state import persist_execution_assessments
    from app.services.outcome_service import invalidate_outcome_metrics_cache
    from app.services.workbench_service import invalidate_recommendation_history_cache

    if persist_execution_assessments(db, [result]):
        db.commit()
        invalidate_outcome_metrics_cache()
        invalidate_recommendation_history_cache()
    return result


__all__ = [
    "confirm_recommendation_execution",
    "confirm_with_prepared_context",
    "is_unsettled_formal_rec",
]

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
from app.services.evidence.validation import (
    canonical_live_forward_run_ids,
    catchup_formal_run_ids,
)
from app.services.entry_day_checks import (
    ENTRY_WINDOW_TRADE_DAYS,
    EntryDayCheck,
    check_entry_bar,
    entry_window_days_for_rec,
)
from app.services.entry_window_state import (
    MISSING_STATUSES,
    SKIPPED_STATUSES,
    first_day_status,
    signal_age_trade_days as signal_age_trade_days_for,
)
from app.services.intraday_cumulative_policy import (
    hard_veto_intraday_inconclusive,
    intraday_cumulative_inconclusive,
)
from app.services.outcome_simulation import _is_intraday_recommendation, _resolve_stop_loss_price


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
    entry_check: EntryDayCheck | None = None,
    prior_day_blocked: bool = False,
    window_days: int = ENTRY_WINDOW_TRADE_DAYS,
    prior_day_missing: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """把入场日检查与策略信号投影为未入场/已持仓两套动作。

    未入场（买入确认）只看入场日检查（与回测买入规则一一对应，见 entry_day_checks）：
    不再用入场日盘中数据重判信号日的形态/筛选条件（prefilter、require_setup、阶段硬否决），
    也不再因「跌破推荐止损位」取消——回测没有这两类入场规则。
    已持仓提示仍参考策略信号与止损（不影响结算）。
    """
    if not fresh:
        reason = "当前行情快照不是最新交易日，等待数据更新后再确认"
        return _decision("not_ready", "数据未就绪", reason), _decision("observe", "等待最新数据", reason)

    stance = str((review or {}).get("stance") or "")
    action = str((review or {}).get("action") or "")
    review_available = bool(review and review.get("status") == "ok")
    # 硬否决若只因盘中累计量未到（全日仍可能消失），收盘前不据此给退出结论
    hard_veto = bool(signal.get("hard_veto")) and not bool(signal.get("hard_veto_inconclusive"))
    strategy_vetoed = hard_veto or stance in {"oppose", "veto"} or action == "不做"
    # 入场决策发生在交易时段内（收盘后走 expired 分支），成交额/换手率/量比等字段拿到的
    # 是盘中累计值。这类条件在收盘前只能确认达标、不能确认不达标，未达标时判「继续观察」
    # 而不是取消——否则开盘几分钟内每只待确认推荐都会被一次采样永久判死。
    prefilter_inconclusive = not prefilter_ok and intraday_cumulative_inconclusive(
        signal.get("prefilter_detail")
    )
    prefilter_failed = not prefilter_ok and not prefilter_inconclusive
    stop_broken = stop_loss > 0 and current_price > 0 and current_price <= stop_loss
    rule_supportive = bool(signal.get("setup_match")) and float(signal.get("score") or 0) >= 72
    supportive = stance in {"strong_support", "support"} or (not review_available and rule_supportive)

    window_days = max(1, int(window_days))
    last_window_day = signal_age_trade_days >= window_days
    if signal_age_trade_days < 1 and not is_intraday_recommendation:
        entry = _decision("not_ready", "盘后候选，下一交易日确认", "盘后生成，当前不可买；请在下一交易日按实时条件确认")
    elif is_intraday_recommendation and signal_age_trade_days > 0:
        entry = _decision("expired", "盘中买点已过期", "盘中买点仅在推荐当日有效，请勿追入")
    elif not is_intraday_recommendation and signal_age_trade_days > window_days:
        entry = _decision("expired", "确认窗口已过期", f"已超过推荐后 {window_days} 个交易日的入场窗口（与回测一致），请等待新的推荐信号")
    elif not is_intraday_recommendation and signal_age_trade_days >= 2 and prior_day_missing:
        # 第 1 日日 K 没入库（全市场或该股，且不是停牌）：判不了第 1 日能不能买，窗口内既不买也不判过期（过了窗口按日历过期）
        entry = _decision(
            "not_ready", "数据未就绪",
            "推荐后第 1 个交易日的日 K 尚未入库，无法判断第 1 日是否可买；数据补齐前不买也不判过期（已告警）",
        )
    elif not is_intraday_recommendation and signal_age_trade_days >= 2 and not prior_day_blocked:
        entry = _decision("expired", "次日确认窗口已过期", "推荐后首个交易日按回测口径已可开盘买入，后续交易日不再追入")
    elif is_intraday_recommendation:
        # 盘中推荐：回测按推荐价当日成交，无入场日附加检查
        if entry_window_closed:
            entry = _decision("expired", "盘中买点已过执行窗口", "今日已收盘，本次买点不再有效，请勿追入")
        else:
            entry = _decision("buy", "按推荐价执行", "盘中推荐，回测口径按推荐价当日成交")
    elif entry_check is None:
        entry = _decision("wait", "继续观察", "尚未取到入场日开盘行情，暂不确认")
    elif not entry_check.blocked:
        if entry_window_closed:
            entry = _decision("expired", "盘中买点已过执行窗口", "今日已收盘，本次买点不再有效，请勿追入")
        else:
            entry = _decision("buy", "按回测口径开盘买入", entry_check.reason)
    elif entry_check.inconclusive and not entry_window_closed:
        entry = _decision("wait", "继续观察", entry_check.reason)
    elif last_window_day:
        entry = _decision("cancel", "取消买入", f"{entry_check.reason}；入场窗口最后一日，不再买入")
    else:
        entry = _decision("wait", "今日不买，次日再确认", f"{entry_check.reason}；回测口径跳过该日，下一交易日按开盘价再确认")

    if stop_broken:
        holding = _decision("exit", "退出持仓", "当前价格已经跌破原推荐止损位")
    elif strategy_vetoed:
        reason = str(signal.get("veto_reason") or (review or {}).get("summary") or "原策略逻辑已经失效")
        holding = _decision("exit", "原策略已失效，建议退出", reason)
    elif prefilter_failed:
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
    except Exception as exc:  # Agent 只增强解释，失败不能阻断规则确认  # noqa: BLE001
        return {"status": "fallback", "error": str(exc)}


def _build_signal_for_strategy(
    skill: StrategySkill,
    current: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], bool, str]:
    """对单只已 enriched 的 stock 跑原策略 prefilter + 运行，返回带 prefilter 元数据的 signal。

    ``run_strategy`` 自己也查 universe，未通过时短路成 ``hard_veto`` + "不在策略股票池"，
    只给出一个粗粒度否决、丢掉具体是哪条规则不过。确认路径改用 ``skip_universe_check=True``：
    门槛判定统一由 ``explain_skill_prefilter`` 承担（它覆盖的规则是 ``_passes_universe``
    的超集，还多出 ``prefilter.market.include``/``include_any``），结果带着失败规则与观测值
    进入决策状态机，才能区分「真的不合格」和「盘中累计量还没到」。phases 里的 hard_veto
    仍然照常生效。
    """
    prefilter_detail = explain_skill_prefilter(skill, current)
    prefilter_ok = bool(prefilter_detail["passed"])
    prefilter_reason = "pass" if prefilter_ok else str(prefilter_detail["label"])
    signal = dict(run_strategy(
        skill,
        current,
        list(snapshot.get("main_industries") or []),
        skip_universe_check=True,
    ))
    signal["prefilter_ok"] = prefilter_ok
    signal["prefilter_reason"] = prefilter_reason
    signal["prefilter_detail"] = prefilter_detail
    # 硬否决同样认「盘中累计量还不能下结论」（对齐 66bed1be 的 prefilter 做法）
    signal["hard_veto_inconclusive"] = bool(signal.get("hard_veto")) and hard_veto_intraday_inconclusive(
        skill, current, list(snapshot.get("main_industries") or []),
    )
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
    entry_bar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在已准备好的市场上下文（snapshot/enriched stock/策略 signal）下确认一笔推荐。

    entry_bar：入场日（data_as_of）开盘至今的实时 K（open/high/low/close/volume/amount/pre_close）。
    未传时从 current["_bars"] 里取同日 K；都取不到则买入确认判「继续观察」。

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
    # 原始 stop_loss = max(ma20*0.95, 价*(1-5.5%))，股价远低于 MA20（超跌反包）时会高于推荐价，
    # 直接比较会让这类推荐一开盘就被判「跌破止损」取消。按结算口径（outcome_simulation）以推荐价
    # 为入场基准归一化止损，保证实时确认与回测/结算用同一条止损线。
    stop_loss = _resolve_stop_loss_price(float(rec.recommend_price or 0) or None, original_snapshot)
    # 止损基准跨除权修正：stop_loss 是推荐日裸价，若推荐日到今日发生除权（10送10 等），
    # 裸价比会误判跌破止损。用 pct_chg 链累乘出复权因子，把止损基准缩放到今日口径。
    adjusted_stop_loss = _adjust_stop_loss_for_ex_right(db, code, str(rec.trade_date), data_as_of, stop_loss)
    recommend_price = float(rec.recommend_price or 0)
    bar = entry_bar or _same_day_bar(current, data_as_of)
    entry_check = (
        check_entry_bar(code, bar, recommend_price, session_closed=entry_window_closed)
        if bar is not None else None
    )
    window_days = entry_window_days_for_rec(rec)
    day1_status, day1 = day1_status_in_window(
        db, rec, signal_age_trade_days=signal_age_trade_days, window_days=window_days, data_as_of=data_as_of,
    )
    prior_day_blocked = day1_status in SKIPPED_STATUSES
    prior_day_missing = day1_status in MISSING_STATUSES
    sync_day1_missing_alert(rec, day1_status, day1)
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
        entry_check=entry_check,
        prior_day_blocked=prior_day_blocked,
        window_days=window_days,
        prior_day_missing=prior_day_missing,
    )
    condition_summary = _condition_summary(entry_check, entry_decision)
    if day1 is not None:
        condition_summary["observed"] = {
            **condition_summary.get("observed", {}), "day1": day1, "day1_status": day1_status,
        }

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
    entry_check: EntryDayCheck | None,
    entry_decision: dict[str, str],
) -> dict[str, Any]:
    """买入确认的条件证据：只记录入场日检查（与回测买入规则对应），不再记录信号日形态重判。"""
    code = entry_decision.get("code") or "unknown"
    if code == "buy":
        status = "passed"
    elif code in {"wait", "not_ready"}:
        status = "pending"
    else:
        status = "failed"
    return {
        "passed": code == "buy",
        "status": status,
        "scope": "entry_day",
        "code": entry_check.rule_id if entry_check is not None else code,
        "label": entry_decision.get("reason") or "",
        "expression": "回测买入规则：入场窗口 2 日、可成交、非一字涨停、开盘相对推荐价 ≤6%",
        "observed": dict(entry_check.observed or {}) if entry_check is not None else {},
    }


def _same_day_bar(current: dict[str, Any], data_as_of: str) -> dict[str, Any] | None:
    """从 enriched stock 的 _bars 取 data_as_of 当日 K（含前收），取不到返回 None。"""
    bars = sorted(
        (b for b in (current.get("_bars") or []) if isinstance(b, dict)),
        key=lambda b: str(b.get("trade_date")),
    )
    if not bars or str(bars[-1].get("trade_date")) != data_as_of:
        return None
    bar = dict(bars[-1])
    if len(bars) >= 2 and not bar.get("pre_close"):
        bar["pre_close"] = bars[-2].get("close")
    return bar


def _first_trade_day_status(
    db: Session,
    code: str,
    signal_date: str,
    data_as_of: str,
    recommend_price: float,
) -> tuple[str, str | None]:
    """推荐后第 1 个交易日（按交易所日历）的状态：buyable / blocked / suspended / missing_market / missing_code / none。

    决定 2 天窗口的第 2 日能否再确认；缺数据（missing_*）时调用方判「数据未就绪」并告警。
    """
    return first_day_status(db, code, signal_date, data_as_of, recommend_price)


def day1_status_in_window(
    db: Session,
    rec: Recommendation,
    *,
    signal_age_trade_days: int,
    window_days: int,
    data_as_of: str,
) -> tuple[str, str | None]:
    """只有 2 天窗口的第 2 日（及以内）才需要第 1 日状态；过了窗口记 past_window（解除缺数据告警）。"""
    if _is_intraday_recommendation(rec) or window_days < 2 or signal_age_trade_days < 2:
        return "none", None
    if signal_age_trade_days > window_days:
        return "past_window", None
    return _first_trade_day_status(
        db, str(rec.code), str(rec.trade_date), data_as_of, float(rec.recommend_price or 0)
    )


DAY1_MISSING_ALERT_KIND = "entry_day1_bar_missing"
DAY1_MISSING_ALERT_COOLDOWN = 20 * 3600  # 条件还在就每天重发一次


def sync_day1_missing_alert(rec: Recommendation, status: str, day1: str | None) -> None:
    """第 1 日日 K 缺失 → 告警（每天一次）；状态已能判定（有 K / 停牌）→ 解除。"""
    from app.core.alerts import emit_alert, resolve_alert

    key = str(rec.recommendation_id)
    if status in MISSING_STATUSES:
        scope = "全市场" if status == "missing_market" else "该股"
        emit_alert(
            DAY1_MISSING_ALERT_KIND, key,
            f"{rec.code} {rec.name or ''} 推荐日 {rec.trade_date} 的第 1 个交易日 {day1} {scope}日 K 未入库，"
            "买入确认判「数据未就绪」（不买也不过期），请补数",
            cooldown=DAY1_MISSING_ALERT_COOLDOWN,
        )
    elif status != "none":
        resolve_alert(DAY1_MISSING_ALERT_KIND, key, f"{rec.code} 第 1 个交易日{(' ' + day1) if day1 else ''}状态已可判定或已过窗口（{status}）")


def confirm_recommendation_execution(
    db: Session,
    recommendation_id: str,
    *,
    include_agent: bool = True,
) -> dict[str, Any] | None:
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    canonical_run_ids = set(canonical_live_forward_run_ids(
        db,
        trade_date=str(rec.trade_date),
    ))
    # 凌晨补跑的正式运行（unverified 但时点有效）同样需要买入确认/执行状态，
    # 否则复盘页这些票没有实时状态。KPI 口径仍只用 canonical LIVE。
    canonical_run_ids.update(catchup_formal_run_ids(
        db, date_from=str(rec.trade_date), date_to=str(rec.trade_date),
    ))
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

    # 推荐天数按交易所日历数（不按该股自己的 K：停牌/缺数据不能拉长或缩短窗口）
    signal_age_trade_days = signal_age_trade_days_for(db, str(rec.trade_date), data_as_of)

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

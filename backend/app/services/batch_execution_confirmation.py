"""批量次日执行确认：对历史页当前页未结算正式推荐一次性实时确认。

与单票 ``confirm_recommendation_execution`` 共享 ``confirm_with_prepared_context``，
保证两套入口不会产生两套规则。批量入口不调用 LLM/Agent（review=None），按高置信
规则兜底决策；只写独立的执行确认状态，不改结算、不影响胜率与收益统计。

实时行情共享方式（Spec 第 14 条 DoD 记录）：
- 一次 ``get_market_universe()`` 拉全市场快照作为公共市场上下文（广度/主线行业/样本数），
  目标股票的 quote 字段（price/turnover_rate/amount_yi 等）也从该快照复用。
- 一次批量 ``rt_k``（复用 realtime_pnl_service._fetch_rtk_quotes）取目标股票当日实时
  OHLCV，合成临时日 K 追加（或替换同日）到历史 bars 后重算 MA/RSI/MACD/量比/突破等
  当前策略字段——满足 Spec 8.3 严格语义，禁止仅更新 close 而沿用昨日指标。
- 一次 ``build_features`` 对目标股票子集做辅助数据注入与标签。
- 逐记录用各自原策略执行确定性 prefilter + 运行，共享决策状态机。

不变量：N 只未结算股票最多触发一次批量 rt_k、一次公共市场上下文加载、一次公共特征
准备；已结算 ID 不进入 items；过滤后为空时不调行情数据源。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.skill_loader import load_skills
from app.engine.trading_calendar import latest_allowed_quote_date, normalize_trade_date
from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun
from app.services.evidence.validation import (
    canonical_live_forward_run_ids,
    catchup_formal_run_ids,
)
from app.services.execution_confirmation import (
    _build_signal_for_strategy,
    _entry_window_closed,
    confirm_with_prepared_context,
    is_unsettled_formal_rec,
)
from app.services.outcome_simulation import _is_intraday_recommendation

logger = logging.getLogger(__name__)

MAX_BATCH_IDS = 50
_CACHE_TTL_SECONDS = 3 * 60.0
_cache_lock: threading.Lock | None = None
_cache: dict[str, Any] = {"fetched_at": 0.0, "key": None, "value": None}
_inflight: dict[str, dict[str, Any]] = {}


def _get_lock() -> threading.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = threading.Lock()
    return _cache_lock


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _load_eligible_recs(
    db: Session, recommendation_ids: list[str]
) -> tuple[list[tuple[Recommendation, RecommendationOutcome | None]], int]:
    """一次 SQL 查 Recommendation + Run + Outcome，过滤非 canonical LIVE 与已结算。

    返回 (未结算 canonical LIVE 记录列表, 已结算/非 canonical 跳过数)。不存在或
    不符合资格的 ID 一律计入跳过，不向调用方泄露其存在性（Spec 7 安全约定）。
    """
    if not recommendation_ids:
        return [], 0
    # 补跑正式运行也纳入实时执行确认（复盘页展示用，不影响 KPI）。
    canonical_run_ids = tuple(canonical_live_forward_run_ids(db)) + tuple(catchup_formal_run_ids(db))
    if not canonical_run_ids:
        return [], len(set(recommendation_ids))
    rows = list(
        db.execute(
            select(Recommendation, RecommendationOutcome, RecommendationRun)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .outerjoin(
                RecommendationOutcome,
                RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
            )
            .where(
                Recommendation.recommendation_id.in_(recommendation_ids),
                RecommendationRun.run_id.in_(canonical_run_ids),
            )
        ).all()
    )
    eligible: list[tuple[Recommendation, RecommendationOutcome | None]] = []
    skipped = 0
    seen: set[str] = set()
    for rec, outcome, run in rows:
        rid = str(rec.recommendation_id)
        if rid in seen:
            continue
        seen.add(rid)
        # 已最终结算的 canonical LIVE run 也跳过。run 身份已由 exact hash selector
        # 和 SQL run_id 集合保证，outcome 维度复用单票/实时盈亏的未结算口径。
        if not is_unsettled_formal_rec(rec, outcome):
            skipped += 1
            continue
        eligible.append((rec, outcome))
    # 请求中存在但 DB 查不到的 ID 也计入跳过（不泄露存在性）
    skipped += len(set(recommendation_ids) - seen)
    return eligible, skipped


def _signal_age_trade_days(bars: list[dict[str, Any]], signal_date: str, data_as_of: str) -> int:
    """推荐日之后到 data_as_of（含）之间的真实交易日数（基于合成后的完整 K 序列）。

    保留给单测验证合成 K 的年龄口径；生产路径用 _load_signal_ages 一次 SQL 批量算。
    """
    later = {
        str(b.get("trade_date"))
        for b in bars
        if str(b.get("trade_date")) > signal_date and str(b.get("trade_date")) <= data_as_of
    }
    if data_as_of > signal_date:
        later.add(data_as_of)
    return len(later)


def _load_signal_ages(
    db: Session,
    recs: list[tuple[Recommendation, RecommendationOutcome | None]],
    data_as_of: str,
) -> dict[str, int]:
    """一次 SQL 按 recommendation 计算推荐日之后的市场交易日数。

    年龄属于一笔推荐而不是股票代码；同一股票不同推荐日必须得到不同结果。交易日使用
    全市场日 K 的 distinct 日期，而不是依赖个股是否有 K（停牌/数据缺口不应延长信号）。
    data_as_of 尚未落库时仍作为当前交易日补入。
    """
    if not recs:
        return {}
    signal_dates = [str(rec.trade_date) for rec, _ in recs if str(rec.trade_date)]
    if not signal_dates or not data_as_of:
        return {str(rec.recommendation_id): 0 for rec, _ in recs}
    min_signal = min(signal_dates)
    market_dates = {
        str(value)
        for value in db.scalars(
            select(DailyKbar.trade_date)
            .where(DailyKbar.trade_date > min_signal, DailyKbar.trade_date <= data_as_of)
            .distinct()
        ).all()
    }
    ages: dict[str, int] = {}
    for rec, _ in recs:
        signal_date = str(rec.trade_date)
        later = {trade_date for trade_date in market_dates if trade_date > signal_date}
        if data_as_of > signal_date:
            later.add(data_as_of)
        ages[str(rec.recommendation_id)] = len(later)
    return ages


def _build_today_bar(code: str, quote_date: str, rt: dict[str, Any]) -> dict[str, Any]:
    """用 rt_k 当日实时 OHLCV 合成临时日 K（筹码/指标字段留空，由重算兜底）。

    rt_k close 即现价；pre_close 反推 pct_chg（与 realtime_pnl_service 同口径）。筹码
    分布是 T+1 数据，rt_k 不含，留 None 让 _build_stock_from_bars 走中性估算兜底——
    符合 Spec 8.3.6「无法可靠计算的字段标为缺失，不能沿用昨日值伪装实时」。
    """
    pre_close = _f(rt.get("pre_close"))
    close = _f(rt.get("close"))
    pct_chg = round((close - pre_close) / pre_close * 100, 4) if pre_close > 0 else 0.0
    return {
        "code": code,
        "trade_date": quote_date,
        "open": _f(rt.get("open")),
        "high": _f(rt.get("high")),
        "low": _f(rt.get("low")),
        "close": close,
        "volume": _f(rt.get("vol")),
        "amount": _f(rt.get("amount")),
        "pct_chg": pct_chg,
        "turnover_rate": 0.0,  # rt_k 不含换手率，盘中累计换手率由 quote.turnover_rate 顶层字段兜底
        "ma5": None, "ma10": None, "ma20": None, "ma60": None,
        "volume_ratio_5d": None,
        "chip_winner_rate": None, "chip_weight_avg": None,
        "chip_cost_15": None, "chip_cost_50": None, "chip_cost_85": None,
        "holder_pct": None, "profit_efficiency": None,
        "chip_concentration": None,
        "_indicator": {},
    }


def _rebuild_stock_with_rt_k(
    stock: dict[str, Any],
    rt: dict[str, Any],
    quote_date: str,
    *,
    build_stock_from_bars: Any,
) -> dict[str, Any] | None:
    """用 rt_k 合成当日临时日 K，拼接到历史 bars 后重算技术指标。

    Spec 8.3.4：当本地最后一根 K 与实时 K 日期相同时用实时 K 替换，不重复追加。
    返回新的 stock dict（含今日 OHLCV 重算的 MA/RSI/MACD/量比/突破等），失败返回 None。
    """
    bars = list(stock.get("_bars") or [])
    if not bars:
        return None
    bars = sorted(bars, key=lambda b: str(b.get("trade_date")))
    today_bar = _build_today_bar(str(stock.get("code")), quote_date, rt)
    if bars and str(bars[-1].get("trade_date")) == quote_date:
        bars[-1] = today_bar  # 替换同日历史 K，不重复追加
    else:
        bars.append(today_bar)

    # _build_stock_from_bars 需要的 quote 字段：从已 enriched stock 复用（get_market_universe
    # 的快照在盘中已含当日实时 price/turnover_rate/amount_yi 等）。
    quote = {
        "code": stock.get("code"),
        "name": stock.get("name"),
        "exchange": stock.get("exchange"),
        "industry": stock.get("industry"),
        "price": _f(rt.get("close")) or _f(stock.get("price")),
        "pct": _f(rt.get("pct")) if rt.get("pct") else _f(stock.get("pct")),
        "amount_yi": _f(stock.get("amount_yi")),
        "turnover_rate": _f(stock.get("turnover_rate")),
        "free_mv_yi": _f(stock.get("free_mv_yi")),
        "total_mv_yi": _f(stock.get("total_mv_yi")),
        "pe_ttm": _f(stock.get("pe_ttm")),
        "main_net_inflow_yi": _f(stock.get("main_net_inflow_yi")),
        "is_st": bool(stock.get("is_st")),
        "quote_time": rt.get("quote_time") or stock.get("quote_time"),
    }
    rebuilt = build_stock_from_bars(
        quote,
        bars,
        quote_source=str(stock.get("quote_source") or "rt_k"),
        kline_source=str(stock.get("kline_source") or "local_pool+rt_k"),
    )
    # 保留原 stock 的 dsa_profile 等已被 _build_stock_from_bars 重算覆盖；quote_time 用 rt 实际时间
    rebuilt["quote_time"] = rt.get("quote_time") or stock.get("quote_time")
    return rebuilt


def _expired_item(rec: Recommendation, reason: str, *, data_as_of: str) -> dict[str, Any]:
    return {
        "recommendation_id": str(rec.recommendation_id),
        "code": str(rec.code),
        "trade_date": str(rec.trade_date),
        "status": "ok",
        "fresh": True,
        "checked_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "data_as_of": data_as_of,
        "quote_time": None,
        "data_source": None,
        "current_price": 0.0,
        "entry_decision": {"code": "expired", "label": "信号已过期", "reason": reason},
        "strategy_id": str(rec.bucket or "daily-stock-analysis-skill"),
        "condition_summary": {
            "passed": False,
            "status": "failed",
            "scope": "entry_window",
            "code": "expired",
            "label": reason,
            "expression": "",
            "observed": {"signal_age_trade_days": None},
        },
        "strategy_signal": {},
    }


def _persist_fresh_assessments(db: Session, assessments: list[dict[str, Any]]) -> None:
    """一次提交本批有效状态，并让历史页立即读到最新结果。"""
    if not assessments:
        return
    from app.services.execution_confirmation_state import persist_execution_assessments
    from app.services.outcome_service import invalidate_outcome_metrics_cache
    from app.services.workbench_service import invalidate_recommendation_history_cache

    if persist_execution_assessments(db, assessments):
        db.commit()
        invalidate_outcome_metrics_cache()
        invalidate_recommendation_history_cache()


def _not_ready_item(
    rec: Recommendation,
    reason: str,
    *,
    data_as_of: str,
    quote_time: str | None = None,
    current_price: float = 0.0,
) -> dict[str, Any]:
    return {
        "recommendation_id": str(rec.recommendation_id),
        "code": str(rec.code),
        "trade_date": str(rec.trade_date),
        "status": "not_ready",
        "fresh": False,
        "data_as_of": data_as_of,
        "quote_time": quote_time,
        "data_source": None,
        "current_price": current_price,
        "entry_decision": {"code": "not_ready", "label": "行情未就绪", "reason": reason},
        "strategy_signal": {},
    }


def _realtime_bar_issue(
    rt: dict[str, Any],
    *,
    data_as_of: str,
    expected_quote_date: str,
    local_now: datetime,
) -> str | None:
    """返回实时 K 不可用于买入判断的原因；None 表示可用。"""
    quote_date = normalize_trade_date(rt.get("trade_date"))
    if data_as_of != expected_quote_date or quote_date != expected_quote_date:
        return f"行情日期未就绪（快照 {data_as_of or '—'}，实时 K {quote_date or '—'}）"
    if expected_quote_date == local_now.date().isoformat() and (local_now.hour, local_now.minute) < (9, 25):
        return "集合竞价尚未结束，实时 K 尚未形成"
    open_price = _f(rt.get("open"))
    high = _f(rt.get("high"))
    low = _f(rt.get("low"))
    close = _f(rt.get("close"))
    volume = _f(rt.get("vol"))
    amount = _f(rt.get("amount"))
    if min(open_price, high, low, close) <= 0 or volume <= 0 or amount <= 0:
        return "实时 K 的 OHLCV 尚不完整"
    if high < max(open_price, low, close) or low > min(open_price, high, close):
        return "实时 K 的高低价关系无效"
    return None


def _compute_batch(
    db: Session,
    eligible: list[tuple[Recommendation, RecommendationOutcome | None]],
    *,
    requested_count: int,
    settled_skipped: int,
) -> dict[str, Any]:
    """批量实时确认主流程（无缓存）。eligible 为已过滤的未结算 formal 记录。"""
    started = time.time()

    if not eligible:
        # 过滤后无可计算记录：直接返回，不调行情数据源（Spec 4.2）。
        return _empty_response(requested_count, settled_skipped, started)

    local_now = datetime.now()
    expected_quote_date = latest_allowed_quote_date()

    # 3. 预判 expired：用 expected_quote_date 作为 data_as_of 上界，一次 SQL 批量算
    #    真实交易日年龄。年龄 > 1 的直接 expired，不拉该票 rt_k（Spec 8.4.4）。
    #    age == 0（推荐日 >= expected_quote_date）说明推荐日之后尚无新交易日 → not_ready，
    #    仍进入 pending 拉行情确认 fresh。窗口判断统一留给共享决策状态机，避免在
    #    not_ready 之前误判 expired。
    ages = _load_signal_ages(db, eligible, expected_quote_date)

    pending: list[tuple[Recommendation, RecommendationOutcome | None]] = []
    items: list[dict[str, Any]] = []
    expired_assessments: list[dict[str, Any]] = []
    expired_count = 0
    for rec, outcome in eligible:
        age = ages.get(str(rec.recommendation_id), 0)
        intraday = _is_intraday_recommendation(rec)
        reason = ""
        if intraday and age > 0:
            reason = "盘中推荐仅在推荐当日确认买点"
        elif not intraday and age > 1:
            reason = "已超过推荐后的首个交易日，需要等待新的推荐信号"
        elif intraday and age == 0 and _entry_window_closed(expected_quote_date, local_now):
            reason = "当前交易日已收盘，不再追认盘中买点"
        if reason:
            expired = _expired_item(rec, reason, data_as_of=expected_quote_date)
            expired["condition_summary"]["observed"]["signal_age_trade_days"] = age
            items.append(expired)
            expired_assessments.append(expired)
            expired_count += 1
            continue
        pending.append((rec, outcome))

    if not pending:
        _persist_fresh_assessments(db, expired_assessments)
        return _assemble_response(
            requested_count=requested_count,
            eligible_count=len(eligible),
            settled_skipped_count=settled_skipped,
            expired_count=expired_count,
            items=items,
            errors=[],
            data_as_of=expected_quote_date,
            started=started,
            cached=False,
        )

    # 4. 一次公共市场上下文（get_market_universe 拉全市场快照 + 实时 quote）。
    from app.core.config import get_settings
    from app.services.feature_builder import build_features
    from app.services.factors.registry import bootstrap_default_factor_registry
    from app.services.market_data import _build_stock_from_bars, get_market_universe
    from app.services.realtime_pnl_service import _fetch_rtk_quotes, compute_realtime_pnl_items

    bootstrap_default_factor_registry()
    try:
        stocks, snapshot = get_market_universe()
    except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
        # 批次公共行情完全失败 → 请求级错误（Spec 7），不伪装成「未命中」。
        logger.warning("批量执行确认公共行情失败: %s", str(exc)[:200])
        _persist_fresh_assessments(db, expired_assessments)
        return {
            "computed_at": _now_str(),
            "data_as_of": expected_quote_date,
            "cached": False,
            "error": f"公共行情数据源失败: {str(exc)[:120]}",
            "summary": {
                "requested_count": requested_count,
                "eligible_count": len(eligible),
                "settled_skipped_count": settled_skipped,
                "expired_count": expired_count,
                "success_count": 0,
                "error_count": len(pending),
            },
            "items": items,
            "pnl_items": [],
            "errors": [
                {"recommendation_id": str(rec.recommendation_id), "code": str(rec.code), "message": "实时行情不可用"}
                for rec, _ in pending
            ],
        }

    data_as_of = str(snapshot.get("trade_date") or expected_quote_date)
    stocks_by_code = {str(s.get("code")): s for s in stocks}

    # 5. 一次批量 rt_k（仅 pending 涉及的代码）。
    pending_codes = sorted({str(rec.code) for rec, _ in pending})
    rt_quotes = _fetch_rtk_quotes(pending_codes) if pending_codes else {}
    rt_fetch_missing = len(pending_codes) - len(rt_quotes)

    # 6. 重建目标股票特征（合成今日临时日 K + 重算），一次 build_features。
    rebuilt_stocks: list[dict[str, Any]] = []
    rebuilt_by_code: dict[str, dict[str, Any]] = {}
    missing_rt_codes: list[str] = []
    not_ready_by_code: dict[str, str] = {}
    usable_rt_quotes: dict[str, dict[str, Any]] = {}
    for code in pending_codes:
        stock = stocks_by_code.get(code)
        rt = rt_quotes.get(code)
        if not rt or _f(rt.get("close")) <= 0:
            missing_rt_codes.append(code)
            continue
        issue = _realtime_bar_issue(
            rt,
            data_as_of=data_as_of,
            expected_quote_date=expected_quote_date,
            local_now=local_now,
        )
        if issue:
            not_ready_by_code[code] = issue
            continue
        usable_rt_quotes[code] = rt
        if not stock:
            missing_rt_codes.append(code)
            continue
        quote_date = normalize_trade_date(rt.get("trade_date"))
        try:
            rebuilt = _rebuild_stock_with_rt_k(
                stock,
                rt,
                quote_date,
                build_stock_from_bars=_build_stock_from_bars,
            )
        except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
            logger.warning("批量确认实时 K 重建失败 %s: %s", code, str(exc)[:160])
            missing_rt_codes.append(code)
            continue
        if rebuilt is None:
            missing_rt_codes.append(code)
            continue
        rebuilt_stocks.append(rebuilt)
        rebuilt_by_code[code] = rebuilt

    pnl_items = compute_realtime_pnl_items(db, [rec for rec, _ in pending], usable_rt_quotes)

    # 一次辅助数据注入 + 标签（公共特征准备）。
    if rebuilt_stocks:
        enriched_stocks = build_features(rebuilt_stocks, snapshot, db=db)
        rebuilt_by_code = {str(stock.get("code")): stock for stock in enriched_stocks}

    # 7. 一次加载策略目录。
    skills = {skill.skill_id: skill for skill in load_skills(get_settings().skills_dir)}

    # 8. 逐记录执行确定性规则（review=None），共享决策状态机。
    errors: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    for rec, _outcome in pending:
        code = str(rec.code)
        current = rebuilt_by_code.get(code)
        if current is None:
            if code in not_ready_by_code:
                rt = rt_quotes.get(code) or {}
                items.append(
                    _not_ready_item(
                        rec,
                        not_ready_by_code[code],
                        data_as_of=data_as_of,
                        quote_time=rt.get("quote_time"),
                        current_price=_f(rt.get("close")),
                    )
                )
                continue
            errors.append({"recommendation_id": str(rec.recommendation_id), "code": code, "message": "实时行情不可用"})
            continue
        strategy_id = str(rec.bucket or "daily-stock-analysis-skill")
        skill = skills.get(strategy_id)
        if skill is None:
            errors.append({"recommendation_id": str(rec.recommendation_id), "code": code, "message": f"原推荐策略 {strategy_id} 已不存在"})
            continue
        try:
            age = ages.get(str(rec.recommendation_id), 0)
            signal, prefilter_ok, _reason = _build_signal_for_strategy(skill, current, snapshot)
            result = confirm_with_prepared_context(
                db,
                rec,
                snapshot=snapshot,
                current=current,
                skill=skill,
                signal=signal,
                prefilter_ok=prefilter_ok,
                signal_age_trade_days=age,
                review=None,
            )
            assessments.append(result)
            items.append(_to_batch_item(result))
        except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
            logger.warning("批量确认单票失败 %s: %s", code, str(exc)[:160])
            errors.append({"recommendation_id": str(rec.recommendation_id), "code": code, "message": str(exc)[:80]})

    _persist_fresh_assessments(db, [*expired_assessments, *assessments])

    success_count = sum(1 for it in items if it.get("status") == "ok")
    elapsed = time.time() - started
    logger.info(
        "批量执行确认 requested=%d eligible=%d settled_skipped=%d expired=%d success=%d error=%d "
        "rt_codes=%d rt_missing=%d elapsed=%.2fs",
        requested_count, len(eligible), settled_skipped, expired_count, success_count, len(errors),
        len(pending_codes), rt_fetch_missing + len(missing_rt_codes), elapsed,
    )
    return _assemble_response(
        requested_count=requested_count,
        eligible_count=len(eligible),
        settled_skipped_count=settled_skipped,
        expired_count=expired_count,
        items=items,
        errors=errors,
        pnl_items=pnl_items,
        data_as_of=data_as_of,
        started=started,
        cached=False,
    )


def _to_batch_item(result: dict[str, Any]) -> dict[str, Any]:
    """把 confirm_with_prepared_context 的完整结果投影为批量 API 的 item 结构。"""
    return {
        "recommendation_id": result.get("recommendation_id"),
        "code": result.get("code"),
        "trade_date": result.get("original", {}).get("trade_date") or "",
        "status": result.get("status"),
        "fresh": result.get("fresh"),
        "data_as_of": result.get("data_as_of"),
        "quote_time": result.get("quote_time"),
        "data_source": result.get("data_source"),
        "current_price": result.get("current_price"),
        "entry_decision": result.get("entry_decision"),
        "holding_decision": result.get("holding_decision"),
        "condition_summary": result.get("condition_summary") or {},
        "strategy_signal": _slim_strategy_signal(result.get("strategy_signal") or {}),
    }


def _slim_strategy_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """批量 item 只暴露前端展示所需的策略信号摘要，不泄露完整 evidence/evidence 列表。"""
    return {
        "phase": signal.get("phase"),
        "score": signal.get("score"),
        "setup_match": signal.get("setup_match"),
        "hard_veto": signal.get("hard_veto"),
        "risk_flags": list(signal.get("risk_flags") or []),
    }


def _now_str() -> str:
    from app.core.time import bj_now_iso

    return bj_now_iso()


def _empty_response(requested_count: int, settled_skipped: int, started: float) -> dict[str, Any]:
    return {
        "computed_at": _now_str(),
        "data_as_of": latest_allowed_quote_date(),
        "cached": False,
        "summary": {
            "requested_count": requested_count,
            "eligible_count": 0,
            "settled_skipped_count": settled_skipped,
            "expired_count": 0,
            "success_count": 0,
            "error_count": 0,
        },
        "items": [],
        "errors": [],
        "pnl_items": [],
    }


def _assemble_response(
    *,
    requested_count: int,
    eligible_count: int,
    settled_skipped_count: int,
    expired_count: int,
    items: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    pnl_items: list[dict[str, Any]] | None = None,
    data_as_of: str,
    started: float,
    cached: bool,
) -> dict[str, Any]:
    success_count = sum(1 for it in items if it.get("status") == "ok")
    return {
        "computed_at": _now_str(),
        "data_as_of": data_as_of,
        "cached": cached,
        "summary": {
            "requested_count": requested_count,
            "eligible_count": eligible_count,
            "settled_skipped_count": settled_skipped_count,
            "expired_count": expired_count,
            "success_count": success_count,
            "error_count": len(errors),
        },
        "items": items,
        "errors": errors,
        "pnl_items": pnl_items or [],
    }


def confirm_batch_executions(
    db: Session,
    recommendation_ids: list[str],
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """对一批未结算正式推荐做批量实时买入确认。

    Spec 8.5：缓存 key 至少包含排序后的未结算 recommendation IDs；settled ID 不纳入
    有效 key（后端不信任前端的未结算判断，先过滤再用未结算 ID 算 key）。同 key 并发
    请求由锁保护避免击穿上游。refresh=True 绕过结果缓存。
    """
    ids = sorted(set(recommendation_ids))
    requested_count = len(ids)
    # 先按数据库真实状态过滤非 formal 与已结算，用未结算 ID 算缓存 key（Spec 8.5）。
    eligible, settled_skipped = _load_eligible_recs(db, ids)
    eligible_ids = sorted({str(rec.recommendation_id) for rec, _ in eligible})
    cache_key = ",".join(eligible_ids)

    with _get_lock():
        if not refresh:
            cached = _cache["value"]
            if (
                cached is not None
                and _cache["key"] == cache_key
                and (time.time() - _cache["fetched_at"]) < _CACHE_TTL_SECONDS
            ):
                return _response_for_request(
                    cached,
                    requested_count=requested_count,
                    settled_skipped=settled_skipped,
                    cached=True,
                )
        flight = _inflight.get(cache_key)
        if flight is None:
            flight = {"event": threading.Event(), "result": None, "error": None}
            _inflight[cache_key] = flight
            owns_flight = True
        else:
            owns_flight = False

    if not owns_flight:
        flight["event"].wait()
        if flight["error"] is not None:
            raise flight["error"]
        return _response_for_request(
            flight["result"],
            requested_count=requested_count,
            settled_skipped=settled_skipped,
            cached=True,
        )

    try:
        result = _compute_batch(db, eligible, requested_count=requested_count, settled_skipped=settled_skipped)
        with _get_lock():
            _cache["fetched_at"] = time.time()
            _cache["key"] = cache_key
            _cache["value"] = result
            flight["result"] = result
        return result
    except Exception as exc:
        with _get_lock():
            flight["error"] = exc
        raise
    finally:
        with _get_lock():
            _inflight.pop(cache_key, None)
            flight["event"].set()


def _response_for_request(
    result: dict[str, Any],
    *,
    requested_count: int,
    settled_skipped: int,
    cached: bool,
) -> dict[str, Any]:
    """复用 eligible 计算结果，同时保留本次请求自己的 summary 计数。"""
    response = dict(result)
    response["summary"] = {
        **dict(result.get("summary") or {}),
        "requested_count": requested_count,
        "settled_skipped_count": settled_skipped,
    }
    response["cached"] = cached
    return response


__all__ = ["confirm_batch_executions", "MAX_BATCH_IDS"]

"""
历史回测服务：在历史日期上重跑推荐流水线并自动标注结果。

工作流：
1. 接受日期区间
2. 从本地 DailyKbar 池加载历史 K 线数据，构建伪行情
3. 运行推荐流水线（规则引擎，不调用 LLM）
4. 用未来 N 天的实际走势标注结果
5. 汇总统计：胜率、平均收益、最大回撤、按 Skill/行业/Bucket 分组
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.aggregator import apply_pool_rules
from app.engine.skill_loader import load_skills
from app.engine.strategy_runner import run_strategy
from app.models.tables import (
    BacktestRun,
    DailyKbar,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    Stock,
    StockQuote,
)
from app.services.feature_builder import build_features, market_regime_score
from app.services.market_data import (
    _build_quote_snapshot,
    _build_stock_from_bars,
    _candidate_recall_plan,
    _eligible_market_quotes,
    _exchange_from_code,
    _select_recall_candidates,
)
from app.services.outcome_service import (
    is_accurate_recommendation,
    is_trade_executed,
    label_pending_outcomes,
    normalized_outcome_label,
    resolved_trade_return,
)
from app.services.temporal_consistency import check_run_temporal_consistency, check_backtest_temporal_consistency
from app.services.evaluation_metrics import (
    compute_full_metrics,
    _empty_metrics as _backtest_empty_metrics,
    _accuracy_failure_gate,
)
from app.services.prefilter_service import apply_prefilters
from app.services.research_context import build_research_contexts
from app.services.workbench_service import (
    _build_recommendation_card,
    _build_skill_rule_gate,
    _prelim_rule_score,
    _resolve_prefilter_target_count,
    _resolve_skill_review_limit,
)

DECISION_MIN_COMPARABLE_TRADES = 5
DECISION_LOW_CONFIDENCE_TRADES = 30
DECISION_STRONG_ACC_EDGE = 0.05
DECISION_NORMAL_ACC_EDGE = 0.03
DECISION_STRONG_RETURN_EDGE = 0.5
DECISION_NORMAL_RETURN_BUFFER = -0.2
DECISION_LOW_CONFIDENCE_RETURN_BUFFER = -0.05
DECISION_NORMAL_DRAWDOWN_TOLERANCE = 1.0
DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE = 0.5
DECISION_DRAWDOWN_BLOCK = 2.5


def run_backtest(
    db: Session,
    start_date: str,
    end_date: str,
    run_mode: str = "experiment",
    horizon_days: int = 5,
    score_line: float = 72.0,
    skill_id: str | None = None,
) -> dict[str, Any]:
    """在历史日期区间上执行回测，数据完全来自本地 DailyKbar 池。"""
    if skill_id is None:
        skill_id = "_general_"

    settings = get_settings()
    dates = _trading_dates_in_range(db, start_date, end_date)
    if not dates:
        return {"status": "no_data", "message": "区间内 DailyKbar 无可用交易日数据", "dates_scanned": 0}

    results: list[dict[str, Any]] = []
    for trade_date in dates:
        t0 = time.perf_counter()
        try:
            date_result = _backtest_single_date(
                db,
                trade_date,
                run_mode,
                settings,
                score_line=score_line,
            )
            date_result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            results.append(date_result)
        except Exception as exc:
            results.append({
                "trade_date": trade_date,
                "status": "error",
                "error": str(exc),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            })

    labeled = label_pending_outcomes(db, horizon_days=horizon_days)

    # ── 自动时态一致性检查 ──
    temporal_check = _run_backtest_temporal_check(db, results, run_mode)

    success_dates = [r for r in results if r.get("status") == "ok"]
    failed_dates = [r for r in results if r.get("status") != "ok"]

    run_ids = _run_ids_from_results(results)

    # 构建交易明细（用于复盘）
    trade_details = _build_trade_detail_list(db, start_date, end_date, run_mode, run_ids=run_ids)

    summary = {
        "status": "completed",
        "dates_scanned": len(dates),
        "dates_succeeded": len(success_dates),
        "dates_failed": len(failed_dates),
        "total_recommendations": sum(r.get("recommendation_count", 0) for r in success_dates),
        "total_elapsed_ms": round(sum(r.get("elapsed_ms", 0) for r in results), 1),
        "labeled_count": labeled.get("labeled", 0),
        "temporal_consistency": temporal_check,
        "trade_detail_count": len(trade_details),
        "trade_details": trade_details,
        "recommendation_records": _backtest_recommendation_records(trade_details),
        "false_positive_samples": _backtest_false_positive_samples(trade_details),
        "results": results,
    }

    if summary["total_recommendations"] > 0:
        summary["breakdown"] = _backtest_breakdown(db, start_date, end_date, run_mode, run_ids=run_ids)
        summary["aggregate_metrics"] = _backtest_aggregate_metrics(db, start_date, end_date, run_mode, run_ids=run_ids)

    _save_backtest_summary(db, skill_id, start_date, end_date, summary)
    return summary


def _save_backtest_summary(
    db: Session, skill_id: str, start_date: str, end_date: str, result: dict[str, Any]
) -> str:
    """将回测汇总结果写入 backtest_runs 表。"""
    backtest_id = f"bt_{skill_id[:20]}_{uuid.uuid4().hex[:10]}"
    row = BacktestRun(
        backtest_id=backtest_id,
        skill_id=skill_id,
        date_from=start_date,
        date_to=end_date,
        status="completed",
        result_json=json.dumps(result, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    return backtest_id


def _trading_dates_in_range(db: Session, start: str, end: str) -> list[str]:
    """获取区间内 DailyKbar 中存在的交易日（至少 100 只股票有数据）。"""
    rows = db.execute(
        select(
            DailyKbar.trade_date,
            func.count().label("cnt"),
        )
        .where(DailyKbar.trade_date >= start, DailyKbar.trade_date <= end)
        .group_by(DailyKbar.trade_date)
        .having(func.count() >= 100)
        .order_by(DailyKbar.trade_date.asc())
    ).all()
    return [row.trade_date for row in rows if row.cnt >= 100]

def _load_latest_unified_quote_map(db: Session, codes: list[str], trade_date: str) -> dict[str, dict[str, Any]]:
    quote_map: dict[str, dict[str, Any]] = {}
    if not codes:
        return quote_map
    latest_quotes = (
        select(
            StockQuote.code.label("code"),
            StockQuote.trade_date.label("trade_date"),
            StockQuote.name.label("name"),
            StockQuote.industry.label("industry"),
            StockQuote.price.label("price"),
            StockQuote.pct.label("pct"),
            StockQuote.volume.label("volume"),
            StockQuote.amount_yi.label("amount_yi"),
            StockQuote.turnover_rate.label("turnover_rate"),
            StockQuote.pe_ttm.label("pe_ttm"),
            StockQuote.total_mv_yi.label("total_mv_yi"),
            StockQuote.free_mv_yi.label("free_mv_yi"),
            StockQuote.chg60.label("chg60"),
            func.row_number().over(
                partition_by=StockQuote.code,
                order_by=(StockQuote.trade_date.desc(), StockQuote.snapped_at.desc()),
            ).label("rn"),
        )
        .where(StockQuote.code.in_(codes), StockQuote.trade_date <= trade_date)
        .subquery()
    )
    rows = db.execute(select(latest_quotes).where(latest_quotes.c.rn == 1)).all()
    for row in rows:
        quote_map[str(row.code)] = {
            "code": str(row.code),
            "trade_date": str(row.trade_date or ""),
            "name": str(row.name or row.code),
            "industry": row.industry,
            "price": float(row.price or 0),
            "pct": float(row.pct or 0),
            "volume": float(row.volume or 0),
            "amount_yi": float(row.amount_yi or 0),
            "turnover_rate": float(row.turnover_rate or 0),
            "pe_ttm": float(row.pe_ttm or 0),
            "total_mv_yi": float(row.total_mv_yi or 0),
            "free_mv_yi": float(row.free_mv_yi or 0),
            "chg60": float(row.chg60 or 0),
        }
    return quote_map


def _historical_quotes_from_bars(
    all_bars: list[DailyKbar],
    stocks_meta: dict[str, dict[str, Any]],
    trade_date: str,
    quote_map: dict[str, dict[str, Any]],
    chg60_map: dict[str, float],
) -> list[dict[str, Any]]:
    quotes: list[dict[str, Any]] = []
    for bar in all_bars:
        code = str(bar.code)
        meta = stocks_meta.get(code, {})
        quote_meta = quote_map.get(code, {})
        amount_yi = round(float(bar.amount or 0) / 1e8, 3)
        free_mv_yi = float(quote_meta.get("free_mv_yi") or 0)
        turnover_rate = float(bar.turnover_rate or 0)
        if turnover_rate <= 0 and amount_yi > 0 and free_mv_yi > 0:
            turnover_rate = round(amount_yi / free_mv_yi * 100, 3)
        if turnover_rate <= 0 and amount_yi > 0:
            turnover_rate = 0.3
        quotes.append(
            {
                "code": code,
                "name": meta.get("name", code),
                "exchange": _exchange_from_code(code),
                "industry": meta.get("industry") or quote_meta.get("industry") or "未分类",
                "is_st": meta.get("is_st", False),
                "price": float(bar.close or 0),
                "pct": float(bar.pct_chg or 0),
                "volume": float(bar.volume or 0),
                "amount_yi": amount_yi,
                "turnover_rate": turnover_rate,
                "pe_ttm": float(quote_meta.get("pe_ttm") or 0),
                "total_mv_yi": float(quote_meta.get("total_mv_yi") or 0),
                "free_mv_yi": free_mv_yi,
                "chg60": float(chg60_map.get(code) or 0),
                "ytd_chg": 0.0,
                "main_net_inflow_yi": 0.0,
                "trade_date": trade_date,
                "quote_time": f"{trade_date}T15:00:00",
            }
        )
    return quotes

def _backtest_single_date(
    db: Session,
    trade_date: str,
    run_mode: str,
    settings: Any,
    score_line: float,
) -> dict[str, Any]:
    """对单个历史日期运行推荐流水线（复用线上规则门与聚合语义）。"""
    all_bars = db.scalars(select(DailyKbar).where(DailyKbar.trade_date == trade_date)).all()
    if len(all_bars) < 100:
        return {"trade_date": trade_date, "status": "skipped", "reason": f"该日期仅 {len(all_bars)} 条日K"}

    codes = [bar.code for bar in all_bars]
    stock_rows = db.scalars(select(Stock).where(Stock.code.in_(codes))).all()
    stocks_meta: dict[str, dict[str, Any]] = {
        s.code: {"name": s.name, "industry": s.industry, "is_st": s.is_st, "market": s.market}
        for s in stock_rows
    }
    chg60_map = _compute_chg60_for_date(db, codes, trade_date)
    quote_map = _load_latest_unified_quote_map(db, codes, trade_date)
    historical_quotes = _historical_quotes_from_bars(all_bars, stocks_meta, trade_date, quote_map, chg60_map)
    eligible_quotes = _eligible_market_quotes(historical_quotes)
    if not eligible_quotes:
        return {"trade_date": trade_date, "status": "skipped", "reason": "历史快照无可交易股票"}

    recall_plan = _candidate_recall_plan(len(historical_quotes), len(eligible_quotes), settings)
    candidates = _select_recall_candidates(eligible_quotes, recall_plan["broad_recall_count"])
    if not candidates:
        return {"trade_date": trade_date, "status": "skipped", "reason": "宽召回为空"}

    bars_history = _load_bars_history_for_date(db, [c["code"] for c in candidates], trade_date)
    stocks = _build_stocks_from_bars(candidates, bars_history, stocks_meta, trade_date, quote_map)
    if not stocks:
        return {"trade_date": trade_date, "status": "skipped", "reason": "宽召回候选缺少历史K线"}

    quote_snapshot = _build_quote_snapshot(
        eligible_quotes,
        data_source="daily_bar_backtest",
        quote_source="daily_bar_backtest",
        quote_errors=[],
    )
    snapshot = {
        **quote_snapshot,
        "snapshot_id": f"backtest_{trade_date}",
        "breadth": {
            **(quote_snapshot.get("breadth") or {}),
            "data_source": "daily_bar_backtest",
            "full_market_mode": recall_plan["full_market_mode"],
            "unified_quote_total": len(historical_quotes),
            "base_filter_eligible_count": len(eligible_quotes),
            "actual_scan_count": len(stocks),
            "candidate_limit_config": {
                "market_candidate_count": recall_plan["explicit_scan_limit"],
                "lookahead_multiplier": recall_plan["lookahead_multiplier"],
                "lookahead_count": recall_plan["broad_recall_count"],
                "target_scan_count": recall_plan["scan_target_count"],
                "broad_recall_count": recall_plan["broad_recall_count"],
                "broad_recall_limited": recall_plan["broad_recall_limited"],
                "recall_strategy": "multi_bucket_quote_recall",
            },
        },
    }

    all_skills = load_skills(settings.skills_dir)
    skill_registries = {
        r.skill_id: r
        for r in db.scalars(select(__import__("app.models.tables", fromlist=["SkillRegistry"]).SkillRegistry)).all()
    }
    selected_skills: list[dict[str, Any]] = []
    skill_map = {skill.skill_id: skill for skill in all_skills}
    for skill in all_skills:
        reg = skill_registries.get(skill.skill_id)
        if reg and reg.mode == run_mode and reg.enabled:
            selected_skills.append(
                {
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "weight": 1.0,
                    "mode": reg.mode,
                    "skill": skill,
                }
            )

    if not selected_skills:
        return {"trade_date": trade_date, "status": "skipped", "reason": "无可用的 Skill"}
    min_prefilter_count = _resolve_prefilter_target_count(
        {"run_mode": run_mode, "score_line": score_line},
        selected_skill_count=len(selected_skills),
    )
    prefiltered_stocks, _prefilter_audit = apply_prefilters(
        stocks,
        selected_skills,
        min_candidate_count=min_prefilter_count,
    )
    if not prefiltered_stocks:
        return {"trade_date": trade_date, "status": "skipped", "reason": "前置预过滤后为空"}

    enriched = build_features(prefiltered_stocks, snapshot, db=None)
    research_contexts = build_research_contexts(enriched, snapshot)
    market_score = market_regime_score(snapshot)

    prelim_entries: list[dict[str, Any]] = []
    for context in research_contexts:
        stock = context.to_skill_input()
        prefilter_skill_ids = {
            str(skill_id)
            for skill_id in (stock.get("prefilter_skill_ids") or [])
            if skill_id
        }
        prefilter_source = str(stock.get("prefilter_source") or "")
        rule_signals_by_skill: dict[str, Any] = {}
        for selected in selected_skills:
            skill_obj = selected["skill"]
            if prefilter_source == "skill_pass" and prefilter_skill_ids and skill_obj.skill_id not in prefilter_skill_ids:
                signal = None
            else:
                skip_universe = prefilter_source == "skill_pass" and skill_obj.skill_id in prefilter_skill_ids
                signal = run_strategy(skill_obj, stock, snapshot["main_industries"], skip_universe_check=skip_universe) if skill_obj.executable else None
            rule_signals_by_skill[skill_obj.skill_id] = signal
        prelim_entries.append(
            {
                "prelim_score": _prelim_rule_score(stock, rule_signals_by_skill, snapshot),
                "context": context,
                "rule_signals": rule_signals_by_skill,
                "stock": stock,
            }
        )

    prelim_entries.sort(key=lambda item: float(item.get("prelim_score") or 0), reverse=True)
    gate_result = _build_skill_rule_gate(prelim_entries, selected_skills, settings)
    skill_candidates = gate_result["skill_candidates"]
    skill_review_limit = _resolve_skill_review_limit(
        {"run_mode": run_mode, "score_line": score_line},
        settings,
    )
    if skill_review_limit > 0 and len(skill_candidates) > skill_review_limit:
        skill_candidates = skill_candidates[:skill_review_limit]

    recommendations: list[dict[str, Any]] = []
    for entry in skill_candidates:
        card = _build_recommendation_card(
            entry,
            [],
            skill_map=skill_map,
            selected_skills=selected_skills,
            market_score=market_score,
            risk_template="balanced",
            calibrated_weights=None,
            snapshot=snapshot,
            recent_codes=set(),
        )
        card["backtest_mode"] = "rule_only_no_synthetic_agent"
        card["trade_date"] = trade_date
        card["recommended_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        recommendations.append(card)
    recommendations = apply_pool_rules(
        recommendations,
        score_line=score_line,
        market_snapshot=snapshot,
        risk_template="balanced",
    )

    run_id = _persist_backtest_run(
        db,
        trade_date,
        run_mode,
        len(research_contexts),
        len(recommendations),
        [selected["skill"] for selected in selected_skills],
        score_line=score_line,
    )
    _persist_backtest_recommendations(db, run_id, recommendations, snapshot)

    return {
        "trade_date": trade_date,
        "status": "ok",
        "run_id": run_id,
        "candidate_count": len(research_contexts),
        "recommendation_count": len(recommendations),
        "skill_count": len(selected_skills),
    }


def _compute_chg60_for_date(
    db: Session, codes: list[str], trade_date: str
) -> dict[str, float]:
    """批量计算每只股票在 trade_date 的 60 日涨幅。"""
    if not codes:
        return {}

    ranked = (
        select(
            DailyKbar.code.label("code"),
            DailyKbar.close.label("close"),
            func.row_number().over(
                partition_by=DailyKbar.code,
                order_by=DailyKbar.trade_date.desc(),
            ).label("rn"),
        )
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date <= trade_date)
        .subquery()
    )
    rows = db.execute(
        select(ranked.c.code, ranked.c.close, ranked.c.rn).where(ranked.c.rn.in_([1, 60]))
    ).all()

    current_close: dict[str, float] = {}
    base_close: dict[str, float] = {}
    for row in rows:
        code = str(row.code)
        close_val = float(row.close or 0)
        if int(row.rn) == 1:
            current_close[code] = close_val
        elif int(row.rn) == 60:
            base_close[code] = close_val

    result: dict[str, float] = {}
    for code, current in current_close.items():
        base = base_close.get(code)
        if base and base > 0:
            result[code] = round((current - base) / base * 100, 2)
    return result


def _load_bars_history_for_date(
    db: Session, codes: list[str], trade_date: str
) -> dict[str, list[dict[str, Any]]]:
    """加载截至 trade_date 每只股票的完整历史 K 线（最多120天）。"""
    all_bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date <= trade_date)
        .order_by(DailyKbar.code, DailyKbar.trade_date.asc())
    ).all()

    bars_by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bar in all_bars:
        bars_by_code[bar.code].append({
            "trade_date": bar.trade_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume or 0,
            "amount": bar.amount or 0,
            "amount_yi": round((bar.amount or 0) / 1e8, 2),
            "pct_chg": bar.pct_chg or 0,
            "turnover_rate": bar.turnover_rate or 0,
            "ma5": bar.ma5,
            "ma10": bar.ma10,
            "ma20": bar.ma20,
            "ma60": bar.ma60,
            "volume_ratio_5d": bar.vol_ratio_5,
        })

    # 截取最近120条
    for code in bars_by_code:
        bars_by_code[code] = bars_by_code[code][-120:]

    return dict(bars_by_code)


def _build_stocks_from_bars(
    candidates: list[dict[str, Any]],
    bars_history: dict[str, list[dict[str, Any]]],
    stocks_meta: dict[str, dict[str, Any]],
    trade_date: str,
    quote_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """从历史 K 线数据构建股票特征输入，尽量复用正式链路的 stock 构建逻辑。"""
    stocks = []
    for candidate in candidates:
        code = candidate["code"]
        meta = stocks_meta.get(code, {})
        bars = bars_history.get(code, [])
        if not bars:
            continue
        quote_meta = quote_map.get(code, {})
        quote = {
            "code": code,
            "name": meta.get("name", code),
            "industry": meta.get("industry") or quote_meta.get("industry") or "未分类",
            "price": candidate.get("price"),
            "pct": candidate.get("pct"),
            "volume": candidate.get("volume"),
            "amount_yi": candidate.get("amount_yi"),
            "turnover_rate": candidate.get("turnover_rate"),
            "free_mv_yi": float(quote_meta.get("free_mv_yi") or candidate.get("free_mv_yi") or 0),
            "total_mv_yi": float(quote_meta.get("total_mv_yi") or candidate.get("total_mv_yi") or 0),
            "pe_ttm": float(quote_meta.get("pe_ttm") or candidate.get("pe_ttm") or 0),
            "is_st": meta.get("is_st", False),
            "exchange": meta.get("exchange", ""),
            "trade_date": trade_date,
            "quote_time": f"{trade_date}T15:00:00",
            "main_net_inflow_yi": 0.0,
        }
        stock = _build_stock_from_bars(
            quote,
            bars,
            quote_source="daily_bar_backtest",
            kline_source="daily_bar_backtest",
        )
        stock["data_source"] = "daily_bar_backtest"
        stock["quote_source"] = "daily_bar_backtest"
        stock["kline_source"] = "daily_bar_backtest"
        stock["data_quality"] = {
            **(stock.get("data_quality") or {}),
            "strict_real_data": False,
            "source_breakdown": {"OBSERVED": 60, "SYNTHETIC": 40},
        }
        stocks.append(stock)

    return stocks


def _persist_backtest_run(
    db: Session,
    trade_date: str,
    run_mode: str,
    universe_count: int,
    recommended_count: int,
    skills: list[Any],
    score_line: float,
) -> str:
    run_id = f"bt_{trade_date}_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run = RecommendationRun(
        run_id=run_id,
        run_name=f"回测 {trade_date}",
        run_mode=run_mode,
        trade_date=trade_date,
        started_at=now,
        finished_at=now,
        market_scope="全市场",
        risk_template="balanced",
        score_line=score_line,
        output_count=0,
        universe_count=universe_count,
        recommended_count=recommended_count,
        status="completed",
        selected_skills_json=json.dumps([s.skill_id for s in skills]),
        params_json=json.dumps(
            {
                "backtest": True,
                "trade_date": trade_date,
                "run_mode": run_mode,
                "score_line": score_line,
            }
        ),
        market_snapshot_json="{}",
        result_summary_json=json.dumps({"backtest": True, "score_line": score_line}),
    )
    db.add(run)
    db.commit()
    return run_id


def _persist_backtest_recommendations(
    db: Session,
    run_id: str,
    cards: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for card in cards:
        feature_snapshot = {
            **(card.get("feature_snapshot", {}) or {}),
            "recommend_price": card.get("recommend_price"),
            "stop_loss": card.get("stop_loss"),
            "take_profit": card.get("take_profit"),
            "score_line_meta": card.get("score_line_meta", {}),
            "execution_viability": card.get("execution_viability", {}),
        }
        rec = Recommendation(
            recommendation_id=f"btrec_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            trade_date=card.get("trade_date", ""),
            recommended_at=now,
            code=card["code"],
            name=card.get("name", ""),
            industry=card.get("industry"),
            recommend_price=card.get("recommend_price", 0),
            bucket="",
            final_score=card.get("final_score", 0),
            consensus_score=card.get("consensus_score", 0),
            setup_type=card.get("setup_type"),
            entry_rule=card.get("entry_rule"),
            invalid_rule=card.get("invalid_rule"),
            rationale=card.get("rationale"),
            risk_tags_json=json.dumps(card.get("risk_flags", [])),
            feature_snapshot_json=json.dumps(feature_snapshot, ensure_ascii=False),
            market_context_json=json.dumps(snapshot),
            skill_breakdown_json=json.dumps(card.get("strategy_signals", [])),
            agent_summary_json="{}",
            status="pending",
            recent_repeat=False,
        )
        db.add(rec)
    db.commit()


def _backtest_breakdown(
    db: Session, start_date: str, end_date: str, run_mode: str, run_ids: set[str] | None = None
) -> dict[str, Any]:
    """按 Skill / 行业 / 分数段 分组统计。"""
    if run_ids is not None and not run_ids:
        return {
            "by_skill": [],
            "by_setup": [],
            "by_industry": [],
            "by_market_regime": [],
            "by_score_range": [],
            "by_score_line": [],
        }
    stmt = (
        select(Recommendation)
        .join(RecommendationRun, Recommendation.run_id == RecommendationRun.run_id)
        .where(
            RecommendationRun.trade_date >= start_date,
            RecommendationRun.trade_date <= end_date,
            RecommendationRun.run_mode == run_mode,
        )
    )
    if run_ids is not None:
        stmt = stmt.where(Recommendation.run_id.in_(list(run_ids)))
    recs = db.scalars(stmt).all()

    rec_ids = [r.recommendation_id for r in recs]
    outcomes = {
        o.recommendation_id: o
        for o in db.scalars(
            select(RecommendationOutcome).where(
                RecommendationOutcome.recommendation_id.in_(rec_ids)
            )
        ).all()
    } if rec_ids else {}

    skill_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "triggered": 0, "success": 0, "scores": []})
    for rec in recs:
        signals = json.loads(rec.skill_breakdown_json or "[]")
        outcome = outcomes.get(rec.recommendation_id)
        is_success = is_accurate_recommendation(outcome)
        is_triggered = is_trade_executed(outcome)
        for signal in signals:
            sid = signal.get("strategy_id", "unknown")
            skill_stats[sid]["count"] += 1
            skill_stats[sid]["scores"].append(float(signal.get("score", 0)))
            if is_triggered:
                skill_stats[sid]["triggered"] += 1
            if is_success:
                skill_stats[sid]["success"] += 1

    by_skill = []
    for sid, stats in sorted(skill_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        n = stats["count"]
        triggered = stats["triggered"]
        by_skill.append({
            "skill_id": sid,
            "recommendation_count": n,
            "triggered_count": triggered,
            "success_count": stats["success"],
            "trigger_rate": round(triggered / max(1, n), 3),
            "hit_rate": round(stats["success"] / max(1, triggered), 3) if triggered else 0.0,
            "avg_score": round(sum(stats["scores"]) / max(1, len(stats["scores"])), 2),
        })

    setup_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "triggered": 0, "missed": 0, "success": 0, "returns": []})
    regime_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "triggered": 0, "missed": 0, "success": 0, "returns": []})
    industry_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "triggered": 0, "success": 0})
    for rec in recs:
        industry = rec.industry or "未分类"
        outcome = outcomes.get(rec.recommendation_id)
        feature_snapshot = json.loads(rec.feature_snapshot_json or "{}")
        setup = str(rec.setup_type or "未命中买点")
        regime = str(
            feature_snapshot.get("market_regime")
            or (feature_snapshot.get("market_discipline") or {}).get("market_regime")
            or "未知"
        )
        industry_stats[industry]["count"] += 1
        setup_stats[setup]["count"] += 1
        regime_stats[regime]["count"] += 1
        if is_trade_executed(outcome):
            industry_stats[industry]["triggered"] += 1
            setup_stats[setup]["triggered"] += 1
            regime_stats[regime]["triggered"] += 1
            ret = resolved_trade_return(outcome)
            setup_stats[setup]["returns"].append(ret)
            regime_stats[regime]["returns"].append(ret)
        elif outcome is not None:
            setup_stats[setup]["missed"] += 1
            regime_stats[regime]["missed"] += 1
        if is_accurate_recommendation(outcome):
            industry_stats[industry]["success"] += 1
            setup_stats[setup]["success"] += 1
            regime_stats[regime]["success"] += 1

    by_industry = []
    for ind, stats in sorted(industry_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        n = stats["count"]
        triggered = stats["triggered"]
        by_industry.append({
            "industry": ind,
            "recommendation_count": n,
            "triggered_count": triggered,
            "success_count": stats["success"],
            "trigger_rate": round(triggered / max(1, n), 3),
            "hit_rate": round(stats["success"] / max(1, triggered), 3) if triggered else 0.0,
        })

    by_setup = []
    for setup, stats in sorted(setup_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        n = stats["count"]
        triggered = stats["triggered"]
        returns = stats["returns"]
        by_setup.append({
            "setup_type": setup,
            "recommendation_count": n,
            "triggered_count": triggered,
            "missed_count": stats["missed"],
            "success_count": stats["success"],
            "trigger_rate": round(triggered / max(1, n), 3),
            "hit_rate": round(stats["success"] / max(1, triggered), 3) if triggered else 0.0,
            "avg_return": round(sum(returns) / max(1, len(returns)), 2) if returns else 0.0,
        })

    by_market_regime = []
    for regime, stats in sorted(regime_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        n = stats["count"]
        triggered = stats["triggered"]
        returns = stats["returns"]
        by_market_regime.append({
            "market_regime": regime,
            "recommendation_count": n,
            "triggered_count": triggered,
            "missed_count": stats["missed"],
            "success_count": stats["success"],
            "trigger_rate": round(triggered / max(1, n), 3),
            "hit_rate": round(stats["success"] / max(1, triggered), 3) if triggered else 0.0,
            "avg_return": round(sum(returns) / max(1, len(returns)), 2) if returns else 0.0,
        })

    score_range_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "triggered": 0, "success": 0})
    for rec in recs:
        outcome = outcomes.get(rec.recommendation_id)
        if rec.final_score >= 80:
            rng = "80-100"
        elif rec.final_score >= 64:
            rng = "64-79"
        elif rec.final_score >= 50:
            rng = "50-63"
        else:
            rng = "0-49"
        score_range_stats[rng]["count"] += 1
        if is_trade_executed(outcome):
            score_range_stats[rng]["triggered"] += 1
        if is_accurate_recommendation(outcome):
            score_range_stats[rng]["success"] += 1

    by_score_range = []
    for rng in ["80-100", "64-79", "50-63", "0-49"]:
        stats = score_range_stats.get(rng)
        if not stats:
            continue
        n = stats["count"]
        triggered = stats["triggered"]
        by_score_range.append({
            "range": rng,
            "recommendation_count": n,
            "triggered_count": triggered,
            "success_count": stats["success"],
            "trigger_rate": round(triggered / max(1, n), 3),
            "hit_rate": round(stats["success"] / max(1, triggered), 3) if triggered else 0.0,
        })

    by_score_line = []
    total_recs = len(recs)
    for score_line in [55, 60, 65, 70, 75, 80]:
        selected = [rec for rec in recs if float(rec.final_score or 0) >= score_line]
        labeled = [rec for rec in selected if normalized_outcome_label(outcomes.get(rec.recommendation_id)) != "pending"]
        executed = [rec for rec in labeled if is_trade_executed(outcomes.get(rec.recommendation_id))]
        missed = max(0, len(labeled) - len(executed))
        success_count = sum(1 for rec in executed if is_accurate_recommendation(outcomes.get(rec.recommendation_id)))
        by_score_line.append({
            "score_line": float(score_line),
            "sample_count": len(selected),
            "activation_rate": round(len(selected) / max(1, total_recs), 3) if total_recs else 0.0,
            "labeled_count": len(labeled),
            "triggered_count": len(executed),
            "missed_count": missed,
            "success_count": success_count,
            "trigger_rate": round(len(executed) / max(1, len(labeled)), 3) if labeled else 0.0,
            "hit_rate": round(success_count / max(1, len(executed)), 3) if executed else 0.0,
            "avg_return": round(
                sum(resolved_trade_return(outcomes.get(rec.recommendation_id)) for rec in executed) / max(1, len(executed)),
                2,
            ) if executed else 0.0,
            "avg_drawdown": round(
                sum(float(outcomes.get(rec.recommendation_id).max_drawdown or 0) for rec in labeled if outcomes.get(rec.recommendation_id)) / max(1, len(labeled)),
                2,
            ) if labeled else 0.0,
            "avg_close_return": round(
                sum(float(outcomes.get(rec.recommendation_id).close_return or 0) for rec in labeled if outcomes.get(rec.recommendation_id)) / max(1, len(labeled)),
                2,
            ) if labeled else 0.0,
        })

    return {
        "by_skill": by_skill,
        "by_setup": by_setup,
        "by_industry": by_industry,
        "by_market_regime": by_market_regime,
        "by_score_range": by_score_range,
        "by_score_line": by_score_line,
    }


def _build_trade_detail_list(
    db: Session,
    start_date: str,
    end_date: str,
    run_mode: str,
    run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """构建每笔交易的明细记录，用于复盘分析。

    对每笔已执行交易输出：
    - 推荐日期 / 标的代码 / 标的名称
    - 入场日期 / 入场价格 / 出场日期 / 出场价格
    - 净收益 / 毛收益 / 基准收益
    - 触发条件（setup_type / entry_rule / exit_reason）
    - 误判原因（仅亏损交易）
    - 持仓天数

    Returns:
        list[dict]: 每笔交易的明细字典
    """
    if run_ids is not None and not run_ids:
        return []
    stmt = (
        select(Recommendation)
        .join(RecommendationRun, Recommendation.run_id == RecommendationRun.run_id)
        .where(
            RecommendationRun.trade_date >= start_date,
            RecommendationRun.trade_date <= end_date,
            RecommendationRun.run_mode == run_mode,
        )
    )
    if run_ids is not None:
        stmt = stmt.where(Recommendation.run_id.in_(run_ids))
    recs = db.scalars(stmt).all()

    rec_ids = [r.recommendation_id for r in recs]
    if not rec_ids:
        return []

    outcomes_list = db.scalars(
        select(RecommendationOutcome).where(
            RecommendationOutcome.recommendation_id.in_(rec_ids)
        )
    ).all()
    outcome_map = {o.recommendation_id: o for o in outcomes_list}

    # 第一遍：收集日期用于基准查询
    all_dates: set[str] = set()
    trade_records: list[tuple[Recommendation, RecommendationOutcome]] = []

    for rec in recs:
        outcome = outcome_map.get(rec.recommendation_id)
        if outcome is None:
            continue
        if not is_trade_executed(outcome):
            continue
        entry_date = outcome.entry_date or rec.trade_date
        exit_date = outcome.exit_date or rec.trade_date
        all_dates.add(entry_date)
        all_dates.add(exit_date)
        trade_records.append((rec, outcome))

    if not trade_records:
        return []

    # 批量查询基准指数收盘价
    from app.services.benchmark_service import compute_benchmark_return, get_benchmark_close_map
    close_map = get_benchmark_close_map(db, list(all_dates))

    # 第二遍：构建明细记录
    details: list[dict[str, Any]] = []
    for rec, outcome in trade_records:
        executed_return = resolved_trade_return(outcome)
        entry_date = outcome.entry_date or rec.trade_date
        exit_date = outcome.exit_date or rec.trade_date
        benchmark_ret = compute_benchmark_return(close_map, entry_date, exit_date)

        # 从 feature_snapshot 提取信号特征摘要
        feature_snapshot = json.loads(rec.feature_snapshot_json or "{}")
        signal_features = {
            "market_regime": str(feature_snapshot.get("market_regime", "")),
            "trend_status": str(feature_snapshot.get("trend_status", "")),
            "volume_ratio_5d": feature_snapshot.get("volume_ratio_5d"),
            "ma_alignment": str(feature_snapshot.get("ma_alignment", "")),
            "setup_type": str(feature_snapshot.get("setup_type") or rec.setup_type or ""),
            "entry_rule": str(feature_snapshot.get("entry_rule") or rec.entry_rule or ""),
        }
        signal_valid = not _outcome_bool_flag(getattr(outcome, "hit_invalid", False))

        details.append({
            # 推荐标识
            "recommendation_id": rec.recommendation_id,
            "trade_date": str(rec.trade_date),
            "code": str(rec.code),
            "name": str(rec.name),
            "industry": str(rec.industry or "未分类"),
            # 入场信息
            "entry_date": entry_date,
            "entry_price": float(outcome.entry_price or 0),
            # 出场信息
            "exit_date": exit_date,
            "exit_price": float(outcome.exit_price or 0),
            # 收益指标
            "net_return_pct": float(executed_return),
            "gross_return_pct": float(outcome.close_return or 0),
            "achievable_return_pct": float(outcome.achievable_return or 0),
            "benchmark_return_pct": benchmark_ret,
            # 风险指标
            "morning_drawdown_pct": float(outcome.max_drawdown or 0),
            "holding_days": float(outcome.holding_days or 1),
            # 触发条件
            "setup_type": str(rec.setup_type or ""),
            "entry_rule": str(rec.entry_rule or ""),
            "exit_reason": str(outcome.exit_reason or ""),
            "final_score": float(rec.final_score or 0),
            # 结果判定
            "accurate": bool(executed_return > 0),
            "signal_valid_at_recommend_time": signal_valid,
            "executed": True,
            "net_return_positive": bool(executed_return > 0),
            "false_positive_reason": outcome.outcome_reason if executed_return <= 0 else None,
            # 信号特征摘要
            "signal_features": signal_features,
            "resolved": True,
        })

    return details


def _build_backtest_metric_records(
    db: Session,
    start_date: str,
    end_date: str,
    run_mode: str,
    run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if run_ids is not None and not run_ids:
        return []
    stmt = (
        select(Recommendation)
        .join(RecommendationRun, Recommendation.run_id == RecommendationRun.run_id)
        .where(
            RecommendationRun.trade_date >= start_date,
            RecommendationRun.trade_date <= end_date,
            RecommendationRun.run_mode == run_mode,
        )
    )
    if run_ids is not None:
        stmt = stmt.where(Recommendation.run_id.in_(list(run_ids)))
    recs = db.scalars(stmt).all()

    rec_ids = [r.recommendation_id for r in recs]
    if not rec_ids:
        return []

    outcomes_list = db.scalars(
        select(RecommendationOutcome).where(
            RecommendationOutcome.recommendation_id.in_(rec_ids)
        )
    ).all()
    outcome_map = {o.recommendation_id: o for o in outcomes_list}

    all_dates: set[str] = set()
    for rec in recs:
        outcome = outcome_map.get(rec.recommendation_id)
        if outcome is None or normalized_outcome_label(outcome) == "pending":
            continue
        if not is_trade_executed(outcome):
            continue
        entry_date = outcome.entry_date or rec.trade_date
        exit_date = outcome.exit_date or rec.trade_date
        all_dates.add(str(entry_date))
        all_dates.add(str(exit_date))

    from app.services.benchmark_service import compute_benchmark_return, get_benchmark_close_map
    close_map = get_benchmark_close_map(db, list(all_dates)) if all_dates else {}

    records: list[dict[str, Any]] = []
    for rec in recs:
        outcome = outcome_map.get(rec.recommendation_id)
        if outcome is None or normalized_outcome_label(outcome) == "pending":
            continue
        executed = is_trade_executed(outcome)
        executed_return = resolved_trade_return(outcome) if executed else 0.0
        benchmark_ret = 0.0
        if executed:
            entry_date = outcome.entry_date or rec.trade_date
            exit_date = outcome.exit_date or rec.trade_date
            benchmark_ret = compute_benchmark_return(close_map, entry_date, exit_date)
        accurate = bool(executed and executed_return > 0)
        false_positive_reason = outcome.outcome_reason if not accurate else None
        if false_positive_reason is None and not executed:
            false_positive_reason = "未触发买点或不可真实成交"
        signal_valid = not _outcome_bool_flag(getattr(outcome, "hit_invalid", False))
        records.append(
            {
                "recommendation_id": str(rec.recommendation_id),
                "trade_date": str(rec.trade_date),
                "code": str(rec.code),
                "name": str(rec.name or rec.code),
                "net_return_pct": float(executed_return),
                "gross_return_pct": float(outcome.close_return or 0),
                "achievable_return_pct": float(outcome.achievable_return or 0),
                "morning_drawdown_pct": float(outcome.max_drawdown or 0),
                "holding_days": float((outcome.holding_days or 0) if not executed else (outcome.holding_days or 1)),
                "benchmark_return_pct": benchmark_ret,
                "accurate": accurate,
                "signal_valid_at_recommend_time": signal_valid,
                "executed": executed,
                "net_return_positive": bool(executed and executed_return > 0),
                "false_positive_reason": false_positive_reason,
                "resolved": True,
            }
        )

    return records


def _backtest_aggregate_metrics(
    db: Session,
    start_date: str,
    end_date: str,
    run_mode: str,
    run_ids: set[str] | None = None,
) -> dict[str, Any]:
    """回测汇总指标 — 通过共享的 evaluation_metrics 计算完整 9-KPI。"""
    metric_records = _build_backtest_metric_records(db, start_date, end_date, run_mode, run_ids=run_ids)
    if not metric_records:
        return _backtest_empty_metrics()
    return compute_full_metrics(metric_records)


def _backtest_recommendation_records(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        trades,
        key=lambda item: (item.get("trade_date", ""), item.get("entry_date", ""), item.get("code", "")),
    )
    return [
        {
            "trade_date": item.get("trade_date"),
            "code": item.get("code"),
            "name": item.get("name"),
            "recommendation_time": item.get("trade_date"),
            "entry_time": item.get("entry_date"),
            "entry_price": item.get("entry_price"),
            "exit_date": item.get("exit_date"),
            "exit_time": item.get("exit_date"),
            "exit_price": item.get("exit_price"),
            "net_return_pct": item.get("net_return_pct"),
            "gross_return_pct": item.get("gross_return_pct"),
            "benchmark_return_pct": item.get("benchmark_return_pct"),
            "accurate": item.get("accurate"),
            "signal_valid_at_recommend_time": item.get("signal_valid_at_recommend_time", True),
            "executed": item.get("executed", True),
            "net_return_positive": item.get("net_return_positive", False),
            "accuracy_failure_label": _accuracy_failure_label(item),
            "exit_reason": item.get("exit_reason"),
            "false_positive_reason": item.get("false_positive_reason"),
        }
        for item in ordered
    ]


def _backtest_false_positive_samples(trades: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    losers = [item for item in trades if item.get("resolved") and not item.get("accurate")]
    losers.sort(key=lambda item: float(item.get("net_return_pct") or 0.0))
    return [
        {
            "trade_date": item.get("trade_date"),
            "code": item.get("code"),
            "name": item.get("name"),
            "entry_time": item.get("entry_date"),
            "exit_time": item.get("exit_date"),
            "net_return_pct": item.get("net_return_pct"),
            "false_positive_reason": item.get("false_positive_reason"),
            "signal_valid_at_recommend_time": item.get("signal_valid_at_recommend_time", True),
            "executed": item.get("executed", True),
            "net_return_positive": item.get("net_return_positive", False),
            "accuracy_failure_label": _accuracy_failure_label(item),
        }
        for item in losers[:limit]
    ]


def _accuracy_failure_label(item: dict[str, Any]) -> str | None:
    if item.get("accurate"):
        return None
    _gate, label = _accuracy_failure_gate(item)
    return label


def _outcome_bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _run_ids_from_results(results: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("run_id"))
        for item in results
        if item.get("status") == "ok" and item.get("run_id")
    }


def _run_backtest_temporal_check(
    db: Session,
    results: list[dict[str, Any]],
    run_mode: str,
) -> dict[str, Any]:
    """对回测结果做自动时态一致性检查。

    检查维度：
    1. 所有成功日期的推荐记录是否存在对应日K线数据
    2. Outcome 标注的 entry_date >= trade_date
    3. Outcome 标注的 exit_date > entry_date（无穿越）

    Returns:
        dict with status, violation_count, warnings, errors, summary
    """
    from app.models.tables import RecommendationRun

    run_ids = [
        r.get("run_id")
        for r in results
        if r.get("status") == "ok" and r.get("run_id")
    ]
    if not run_ids:
        return {
            "status": "skipped",
            "message": "无可检查的回测运行记录",
            "violation_count": 0,
        }

    violations: list[dict[str, Any]] = []
    for run_id in run_ids:
        check_result = check_run_temporal_consistency(
            db, run_id=run_id, run_mode=run_mode
        )
        if check_result.get("status") == "completed":
            v = check_result.get("violations", [])
            violations.extend(v)

    # 同时跑 backtest 专用检查
    bt_result = check_backtest_temporal_consistency(db)
    if bt_result.get("status") == "completed":
        violations.extend(bt_result.get("violations", []))

    error_count = sum(1 for v in violations if v.get("severity") == "error")
    warning_count = sum(1 for v in violations if v.get("severity") == "warning")

    return {
        "status": "completed",
        "violation_count": len(violations),
        "error_count": error_count,
        "warning_count": warning_count,
        "passed": error_count == 0,
        "summary": {
            "total": len(violations),
            "errors": error_count,
            "warnings": warning_count,
        },
        "violations": violations[:20],  # 最多返回20条
    }


def _build_time_split_analysis(
    trades: list[dict[str, Any]],
    all_dates: list[str],
    train_ratio: float = 0.6,
) -> dict[str, Any]:
    """严格时间切分验证（固定比例 train/test split by date）。

    与 expanding window walk-forward 互补：
    - walk-forward 测试多个时间窗口的稳定性
    - time-split 给出单一训练集/测试集的清晰对比

    Args:
        trades: 已执行的交易记录列表
        all_dates: 回测区间内所有交易日（已排序）
        train_ratio: 训练集占比（默认 0.6）

    Returns:
        dict with train/test date ranges, trade counts, and 9-KPI metrics
    """
    if len(trades) < 3 or len(all_dates) < 5:
        return {
            "eligible": False,
            "message": f"交易笔数或交易日数不足 (trades={len(trades)}, dates={len(all_dates)})",
            "train_metrics": _backtest_empty_metrics(),
            "test_metrics": _backtest_empty_metrics(),
        }

    # 按日期排序切割
    split_index = max(1, int(len(all_dates) * train_ratio))
    if split_index >= len(all_dates):
        split_index = max(1, len(all_dates) - 1)

    train_date_set = set(all_dates[:split_index])
    test_date_set = set(all_dates[split_index:])

    train_trades = [t for t in trades if t["trade_date"] in train_date_set]
    test_trades = [t for t in trades if t["trade_date"] in test_date_set]

    train_metrics = compute_full_metrics(train_trades) if train_trades else _backtest_empty_metrics()
    test_metrics = compute_full_metrics(test_trades) if test_trades else _backtest_empty_metrics()

    return {
        "eligible": True,
        "train_ratio": train_ratio,
        "train_date_range": {
            "start": all_dates[0],
            "end": all_dates[split_index - 1],
        },
        "test_date_range": {
            "start": all_dates[split_index],
            "end": all_dates[-1],
        },
        "train_trade_count": len(train_trades),
        "test_trade_count": len(test_trades),
        "train_metrics": _validation_metric_view(train_metrics),
        "test_metrics": _validation_metric_view(test_metrics),
        "train_vs_test": {
            "accuracy_diff": round(
                train_metrics.get("recommendation_accuracy", 0)
                - test_metrics.get("recommendation_accuracy", 0), 3
            ),
            "return_diff": round(
                train_metrics.get("average_net_return", 0)
                - test_metrics.get("average_net_return", 0), 3
            ),
        },
    }


def run_backtest_comparison(
    db: Session,
    start_date: str,
    end_date: str,
    run_mode: str = "experiment",
    horizon_days: int = 5,
    baseline_score_line: float = 72.0,
    candidate_score_line: float = 65.0,
    skill_id: str | None = None,
) -> dict[str, Any]:
    """执行 baseline vs candidate 双版本回测比较。

    工作流：
    1. 用 baseline_score_line 跑一次回测（基线版）
    2. 用 candidate_score_line 跑一次回测（候选版，更宽松的评分线）
    3. 对两个版本分别计算 9-KPI 指标
    4. 执行滚动样本外验证（walk-forward）
    5. 自动选择更优版本

    Args:
        db: 数据库会话
        start_date: 起始日期
        end_date: 结束日期
        run_mode: formal | experiment
        horizon_days: 持仓天数
        baseline_score_line: 基线版评分线（默认 72，更严格）
        candidate_score_line: 候选版评分线（默认 65，更宽松）
        skill_id: 指定 Skill ID

    Returns:
        dict 包含 baseline/candidate 的完整对比结果
    """
    if skill_id is None:
        skill_id = "_general_"

    started_at = time.perf_counter()

    # ── 1. 跑两个版本 ──
    baseline_label = f"基线版 (score_line={baseline_score_line})"
    candidate_label = f"候选版 (score_line={candidate_score_line})"

    logger.info("回测比较开始: baseline=%.1f candidate=%.1f", baseline_score_line, candidate_score_line)

    baseline_result = run_backtest(
        db, start_date, end_date, run_mode, horizon_days,
        score_line=baseline_score_line, skill_id=skill_id,
    )
    candidate_result = run_backtest(
        db, start_date, end_date, run_mode, horizon_days,
        score_line=candidate_score_line, skill_id=skill_id,
    )

    # ── 2. 提取 trade 数据用于比较 ──
    def _extract_trades(result: dict[str, Any]) -> list[dict[str, Any]]:
        """从回测结果中提取已执行的交易记录。"""
        if result.get("status") != "completed":
            return []
        agg = result.get("aggregate_metrics")
        if not agg:
            return []
        # aggregate_metrics 已经是 compute_full_metrics 的输出
        # 我们需要更详细的 trade 数据，从 results 中的 run_ids 获取
        return _load_backtest_trades(db, result, start_date, end_date, run_mode)

    baseline_trades = _extract_trades(baseline_result)
    candidate_trades = _extract_trades(candidate_result)

    # ── 3. 构建 walk_forward 分析 ──
    dates = _trading_dates_in_range(db, start_date, end_date)
    walk_baseline = _build_backtest_walk_forward(dates, baseline_trades)
    walk_candidate = _build_backtest_walk_forward(dates, candidate_trades)

    # ── 3.5 严格时间切分验证（与 walk-forward 互补） ──
    time_split_baseline = _build_time_split_analysis(baseline_trades, dates, train_ratio=0.6)
    time_split_candidate = _build_time_split_analysis(candidate_trades, dates, train_ratio=0.6)

    # ── 4. 决策：选择更优版本 ──
    selected_variant, adoption_reason, decision_context = _choose_backtest_variant(
        baseline_result, candidate_result,
        walk_baseline, walk_candidate,
        time_split_baseline, time_split_candidate,
    )

    selected_result = baseline_result if selected_variant == "baseline" else candidate_result
    baseline_metrics = baseline_result.get("aggregate_metrics") or {}
    candidate_metrics = candidate_result.get("aggregate_metrics") or {}

    summary = {
        "status": "completed",
        "strategy_id": skill_id,
        "strategy_name": f"通用回测比较 ({skill_id})",
        "strategy_window": {"start_date": start_date, "end_date": end_date},
        "dates_scanned": len(dates),
        "dates_succeeded": selected_result.get("dates_succeeded", 0),
        "dates_failed": selected_result.get("dates_failed", 0),
        "total_recommendations": selected_result.get("total_recommendations", 0),
        "total_elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        "labeled_count": selected_result.get("labeled_count", 0),
        "baseline": {
            "label": baseline_label,
            "score_line": baseline_score_line,
            "status": baseline_result.get("status"),
            "total_recommendations": baseline_result.get("total_recommendations", 0),
            "dates_succeeded": baseline_result.get("dates_succeeded", 0),
            "aggregate_metrics": baseline_result.get("aggregate_metrics"),
            "walk_forward": walk_baseline,
            "time_split": time_split_baseline,
        },
        "candidate": {
            "label": candidate_label,
            "score_line": candidate_score_line,
            "status": candidate_result.get("status"),
            "total_recommendations": candidate_result.get("total_recommendations", 0),
            "dates_succeeded": candidate_result.get("dates_succeeded", 0),
            "aggregate_metrics": candidate_result.get("aggregate_metrics"),
            "walk_forward": walk_candidate,
            "time_split": time_split_candidate,
        },
        "comparison": {
            "baseline_label": baseline_label,
            "candidate_label": candidate_label,
            "selected_variant": selected_variant,
            "selected_variant_label": candidate_label if selected_variant == "candidate" else baseline_label,
            "adoption_reason": adoption_reason,
            "decision_scope": decision_context["scope"],
            "decision_scope_label": decision_context["scope_label"],
            "guardrail_notes": decision_context["guardrail_notes"],
            "decision_metrics": {
                "baseline": decision_context["baseline_metrics"],
                "candidate": decision_context["candidate_metrics"],
            },
            "walk_forward": {
                "baseline": walk_baseline,
                "candidate": walk_candidate,
            },
            "time_split": {
                "baseline": time_split_baseline,
                "candidate": time_split_candidate,
            },
            "validation_profile": _build_backtest_validation_profile(
                decision_context=decision_context,
                walk_baseline=walk_baseline,
                walk_candidate=walk_candidate,
                time_split_baseline=time_split_baseline,
                time_split_candidate=time_split_candidate,
                baseline_metrics=baseline_metrics,
                candidate_metrics=candidate_metrics,
            ),
            "hypothesis": "降低评分线可以增加候选数量，但可能降低命中率。通过 walk-forward 验证评估 trade-off。",
        },
        "results": selected_result.get("results", []),
        "temporal_consistency": selected_result.get("temporal_consistency"),
    }

    # 保存
    _save_backtest_summary(db, f"{skill_id}_comparison", start_date, end_date, summary)
    logger.info(
        "回测比较完成: selected=%s baseline_recs=%d candidate_recs=%d elapsed=%dms",
        selected_variant,
        baseline_result.get("total_recommendations", 0),
        candidate_result.get("total_recommendations", 0),
        summary["total_elapsed_ms"],
    )
    return summary


def _load_backtest_trades(
    db: Session,
    result: dict[str, Any],
    start_date: str,
    end_date: str,
    run_mode: str,
) -> list[dict[str, Any]]:
    """从本次回测结果中加载推荐级记录（用于 walk_forward/time-split 分析）。"""
    run_ids = _run_ids_from_results(list(result.get("results") or []))
    if not run_ids:
        return []
    return _build_backtest_metric_records(
        db,
        start_date,
        end_date,
        run_mode,
        run_ids=run_ids,
    )


def _build_backtest_walk_forward(
    signal_dates: list[str],
    trades: list[dict[str, Any]],
    min_train_dates: int = 2,
) -> dict[str, Any]:
    """为回测交易构建 expanding window walk-forward 验证。

    与 overnight_holding_service._build_walk_forward_analysis 逻辑一致。
    """
    if len(trades) < min_train_dates:
        return {
            "eligible": False,
            "message": f"交易笔数不足 ({len(trades)} < {min_train_dates})",
            "window_count": 0,
            "test_aggregate": {},
        }

    # 按日期排序
    date_order = sorted(set(t["trade_date"] for t in trades))
    if len(date_order) < min_train_dates + 1:
        return {
            "eligible": False,
            "message": f"交易日期数不足 ({len(date_order)} < {min_train_dates + 1})",
            "window_count": 0,
            "test_aggregate": {},
        }

    windows = []
    for i in range(min_train_dates, len(date_order)):
        train_end = date_order[i - 1]
        test_start = date_order[i]
        test_end = date_order[-1]

        train_trades = [t for t in trades if t["trade_date"] <= train_end]
        test_trades = [t for t in trades if t["trade_date"] >= test_start]

        if len(test_trades) < 1:
            continue

        train_metrics = compute_full_metrics(train_trades) if train_trades else {}
        test_metrics = compute_full_metrics(test_trades) if test_trades else {}

        windows.append({
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "train_count": len(train_trades),
            "test_count": len(test_trades),
            "train_accuracy": train_metrics.get("recommendation_accuracy", 0),
            "test_accuracy": test_metrics.get("recommendation_accuracy", 0),
            "train_avg_return": train_metrics.get("average_net_return", 0),
            "test_avg_return": test_metrics.get("average_net_return", 0),
            "train_drawdown": train_metrics.get("max_drawdown", 0),
            "test_drawdown": test_metrics.get("max_drawdown", 0),
        })

    if not windows:
        return {
            "eligible": False,
            "message": "无法构建任何验证窗口",
            "window_count": 0,
            "test_aggregate": {},
        }

    # 聚合所有测试集
    all_test_trades = [t for t in trades if t["trade_date"] >= date_order[min_train_dates - 1]]
    test_aggregate = compute_full_metrics(all_test_trades) if all_test_trades else {}

    return {
        "eligible": True,
        "min_train_dates": min_train_dates,
        "date_count": len(date_order),
        "window_count": len(windows),
        "windows": windows,
        "test_aggregate": _validation_metric_view(test_aggregate),
        "notes": [f"滚动验证按最少 {min_train_dates} 个训练信号日构建 expanding windows。"],
    }


def _validation_metric_view(metrics: dict[str, Any]) -> dict[str, Any]:
    """保留验证摘要需要的严格推荐口径字段。"""
    return {
        "trade_count": int(metrics.get("trade_count", 0) or 0),
        "recommendation_accuracy": float(metrics.get("recommendation_accuracy", 0) or 0),
        "accuracy_numerator": int(metrics.get("accuracy_numerator", 0) or 0),
        "accuracy_denominator": int(metrics.get("accuracy_denominator", metrics.get("trade_count", 0)) or 0),
        "trade_accuracy": float(metrics.get("trade_accuracy", metrics.get("hit_rate", 0)) or 0),
        "executed_trade_count": int(metrics.get("executed_trade_count", metrics.get("triggered_count", 0)) or 0),
        "unexecuted_count": int(metrics.get("unexecuted_count", metrics.get("missed_count", 0)) or 0),
        "trigger_rate": float(metrics.get("trigger_rate", 0) or 0),
        "average_net_return": float(metrics.get("average_net_return", 0) or 0),
        "average_executed_return": float(
            metrics.get("average_executed_return", metrics.get("avg_executed_return", 0)) or 0
        ),
        "max_drawdown": float(metrics.get("max_drawdown", 0) or 0),
        "profit_loss_ratio": float(metrics.get("profit_loss_ratio", 0) or 0),
        "sample_confidence": str(metrics.get("sample_confidence", "low") or "low"),
        "accuracy_basis": str(metrics.get("accuracy_basis", "labeled_recommendations") or "labeled_recommendations"),
    }


def _choose_backtest_variant(
    baseline_result: dict[str, Any],
    candidate_result: dict[str, Any],
    walk_baseline: dict[str, Any],
    walk_candidate: dict[str, Any],
    time_split_baseline: dict[str, Any],
    time_split_candidate: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """选择更优的回测版本。

    决策层次（与 overnight_holding_service._choose_variant 一致）：
    1. 优先 walk_forward 测试集结果（样本外）
    2. 样本外不足时回退到 time-split 测试集
    3. 最后才看全量指标
    4. trade_count < 30 视为低置信度，需要更强优势
    5. 若回撤显著恶化，则不能仅凭更高收益/准确率默认采用
    """
    # 检查状态
    baseline_ok = baseline_result.get("status") == "completed"
    candidate_ok = candidate_result.get("status") == "completed"
    if not baseline_ok and not candidate_ok:
        return "baseline", "两个版本均未完成，默认选 baseline", _decision_context(
            scope="aggregate",
            scope_label="全量指标",
            baseline_metrics={},
            candidate_metrics={},
            guardrail_notes=["两个版本均未完成，无法形成有效对比。"],
        )
    if not candidate_ok:
        return "baseline", "候选版未完成，选基线版", _decision_context(
            scope="aggregate",
            scope_label="全量指标",
            baseline_metrics=_decision_metric_view(baseline_result.get("aggregate_metrics") or {}),
            candidate_metrics={},
            guardrail_notes=["候选版未完成，仅保留基线结果。"],
        )
    if not baseline_ok:
        return "candidate", "基线版未完成，选候选版", _decision_context(
            scope="aggregate",
            scope_label="全量指标",
            baseline_metrics={},
            candidate_metrics=_decision_metric_view(candidate_result.get("aggregate_metrics") or {}),
            guardrail_notes=["基线版未完成，仅保留候选结果。"],
        )

    baseline_metrics = baseline_result.get("aggregate_metrics") or {}
    candidate_metrics = candidate_result.get("aggregate_metrics") or {}
    scope = _pick_decision_scope(
        walk_baseline,
        walk_candidate,
        time_split_baseline,
        time_split_candidate,
        baseline_metrics,
        candidate_metrics,
    )
    base_view = scope["baseline_metrics"]
    cand_view = scope["candidate_metrics"]
    guardrail_notes = list(scope["guardrail_notes"])

    low_confidence = _is_low_confidence(base_view) or _is_low_confidence(cand_view)
    if low_confidence:
        guardrail_notes.append("当前对比样本低于 30 笔，按低置信度标准处理。")

    drawdown_worse_by = _drawdown_worse_by(base_view, cand_view)
    acc_edge = cand_view["recommendation_accuracy"] - base_view["recommendation_accuracy"]
    return_edge = cand_view["average_net_return"] - base_view["average_net_return"]
    trade_edge = cand_view["trade_count"] - base_view["trade_count"]

    if drawdown_worse_by >= DECISION_DRAWDOWN_BLOCK and (
        acc_edge < DECISION_STRONG_ACC_EDGE or return_edge < DECISION_STRONG_RETURN_EDGE
    ):
        reason = (
            f"{scope['scope_label']}: 候选版回撤较基线恶化 {drawdown_worse_by:.2f}pct，"
            f"准确率/收益优势不足以覆盖风险，保留基线版。"
        )
        guardrail_notes.append("命中回撤护栏，禁止仅凭轻微优势切换到候选版。")
        return "baseline", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    acc_threshold = DECISION_STRONG_ACC_EDGE if low_confidence else DECISION_NORMAL_ACC_EDGE
    return_buffer = (
        DECISION_LOW_CONFIDENCE_RETURN_BUFFER
        if low_confidence
        else DECISION_NORMAL_RETURN_BUFFER
    )
    drawdown_tolerance = (
        DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE
        if low_confidence
        else DECISION_NORMAL_DRAWDOWN_TOLERANCE
    )

    if (
        acc_edge >= acc_threshold
        and return_edge >= return_buffer
        and drawdown_worse_by <= drawdown_tolerance
    ):
        reason = (
            f"{scope['scope_label']}: 候选版准确率 {cand_view['recommendation_accuracy']:.1%} "
            f"> 基线版 {base_view['recommendation_accuracy']:.1%}，"
            f"且收益/回撤未明显恶化。"
        )
        return "candidate", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    if (
        return_edge >= DECISION_STRONG_RETURN_EDGE
        and acc_edge >= -0.01
        and drawdown_worse_by <= DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE
    ):
        reason = (
            f"{scope['scope_label']}: 候选版平均净收益 {cand_view['average_net_return']:.2f}% "
            f"> 基线版 {base_view['average_net_return']:.2f}%，"
            f"准确率未明显变差。"
        )
        return "candidate", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    if base_view["recommendation_accuracy"] >= cand_view["recommendation_accuracy"] + 0.02:
        reason = (
            f"{scope['scope_label']}: 基线版准确率 {base_view['recommendation_accuracy']:.1%} "
            f"> 候选版 {cand_view['recommendation_accuracy']:.1%}，保留基线版。"
        )
        return "baseline", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    if (
        scope["scope"] == "aggregate"
        and trade_edge > 0
        and cand_view["trade_count"] >= max(1, int(base_view["trade_count"] * 1.5))
        and acc_edge >= -0.02
        and drawdown_worse_by <= DECISION_LOW_CONFIDENCE_DRAWDOWN_TOLERANCE
    ):
        reason = (
            f"全量指标: 候选版样本 {cand_view['trade_count']} > 基线版 {base_view['trade_count']}，"
            "准确率基本持平，扩大覆盖后仍可接受。"
        )
        guardrail_notes.append("由于样本外可比交易不足，本次仅按全量指标做保守放宽。")
        return "candidate", reason, _decision_context(
            scope=scope["scope"],
            scope_label=scope["scope_label"],
            baseline_metrics=base_view,
            candidate_metrics=cand_view,
            guardrail_notes=guardrail_notes,
        )

    reason = f"{scope['scope_label']}: 候选版优势不足或风险代价偏大，保留基线版。"
    return "baseline", reason, _decision_context(
        scope=scope["scope"],
        scope_label=scope["scope_label"],
        baseline_metrics=base_view,
        candidate_metrics=cand_view,
        guardrail_notes=guardrail_notes,
    )


def _pick_decision_scope(
    walk_baseline: dict[str, Any],
    walk_candidate: dict[str, Any],
    time_split_baseline: dict[str, Any],
    time_split_candidate: dict[str, Any],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    wf_base = _decision_metric_view(walk_baseline.get("test_aggregate") or {})
    wf_cand = _decision_metric_view(walk_candidate.get("test_aggregate") or {})
    if (
        walk_baseline.get("eligible")
        and walk_candidate.get("eligible")
        and wf_base["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
        and wf_cand["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
    ):
        return {
            "scope": "walk_forward",
            "scope_label": "滚动样本外验证",
            "baseline_metrics": wf_base,
            "candidate_metrics": wf_cand,
            "guardrail_notes": [],
        }

    ts_base = _decision_metric_view((time_split_baseline or {}).get("test_metrics") or {})
    ts_cand = _decision_metric_view((time_split_candidate or {}).get("test_metrics") or {})
    if (
        time_split_baseline.get("eligible")
        and time_split_candidate.get("eligible")
        and ts_base["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
        and ts_cand["trade_count"] >= DECISION_MIN_COMPARABLE_TRADES
    ):
        return {
            "scope": "time_split",
            "scope_label": "时间切分测试集",
            "baseline_metrics": ts_base,
            "candidate_metrics": ts_cand,
            "guardrail_notes": ["滚动样本外可比交易不足，已回退到 time-split 测试集。"],
        }

    return {
        "scope": "aggregate",
        "scope_label": "全量指标",
        "baseline_metrics": _decision_metric_view(baseline_metrics),
        "candidate_metrics": _decision_metric_view(candidate_metrics),
        "guardrail_notes": ["样本外与时间切分测试样本不足，最终按全量指标保守判断。"],
    }


def _decision_metric_view(metrics: dict[str, Any]) -> dict[str, Any]:
    return _validation_metric_view(metrics)


def _is_low_confidence(metrics: dict[str, Any]) -> bool:
    return (
        int(metrics.get("trade_count", 0) or 0) < DECISION_LOW_CONFIDENCE_TRADES
        or str(metrics.get("sample_confidence", "low") or "low") == "low"
    )


def _drawdown_worse_by(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> float:
    base_drawdown = abs(float(baseline_metrics.get("max_drawdown", 0) or 0))
    cand_drawdown = abs(float(candidate_metrics.get("max_drawdown", 0) or 0))
    return round(max(0.0, cand_drawdown - base_drawdown), 3)


def _decision_context(
    *,
    scope: str,
    scope_label: str,
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    guardrail_notes: list[str],
) -> dict[str, Any]:
    return {
        "scope": scope,
        "scope_label": scope_label,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "guardrail_notes": guardrail_notes,
    }


def _build_backtest_validation_profile(
    *,
    decision_context: dict[str, Any],
    walk_baseline: dict[str, Any],
    walk_candidate: dict[str, Any],
    time_split_baseline: dict[str, Any],
    time_split_candidate: dict[str, Any],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "primary_scope": decision_context["scope"],
        "primary_scope_label": decision_context["scope_label"],
        "fallback_reason": _validation_fallback_reason(decision_context["scope"]),
        "primary_samples": {
            "baseline_trade_count": int(decision_context["baseline_metrics"].get("trade_count", 0) or 0),
            "candidate_trade_count": int(decision_context["candidate_metrics"].get("trade_count", 0) or 0),
            "baseline_confidence": str(decision_context["baseline_metrics"].get("sample_confidence", "low") or "low"),
            "candidate_confidence": str(decision_context["candidate_metrics"].get("sample_confidence", "low") or "low"),
        },
        "walk_forward": {
            "baseline": _compact_walk_forward_validation(walk_baseline),
            "candidate": _compact_walk_forward_validation(walk_candidate),
        },
        "time_split": {
            "baseline": _compact_time_split_validation(time_split_baseline),
            "candidate": _compact_time_split_validation(time_split_candidate),
        },
        "aggregate": {
            "baseline": _decision_metric_view(baseline_metrics),
            "candidate": _decision_metric_view(candidate_metrics),
        },
    }


def _compact_walk_forward_validation(result: dict[str, Any]) -> dict[str, Any]:
    aggregate = _validation_metric_view((result or {}).get("test_aggregate") or {})
    notes = list((result or {}).get("notes") or [])
    message = (result or {}).get("message")
    if message:
        notes.append(str(message))
    return {
        "eligible": bool((result or {}).get("eligible")),
        "window_count": int((result or {}).get("window_count", 0) or 0),
        "date_count": int((result or {}).get("date_count", 0) or 0),
        **aggregate,
        "notes": notes,
    }


def _compact_time_split_validation(result: dict[str, Any]) -> dict[str, Any]:
    result = result or {}
    train_metrics = _decision_metric_view(result.get("train_metrics") or {})
    test_metrics = _decision_metric_view(result.get("test_metrics") or {})
    notes: list[str] = []
    message = result.get("message")
    if message:
        notes.append(str(message))
    return {
        "eligible": bool(result.get("eligible")),
        "train_trade_count": int(result.get("train_trade_count", 0) or 0),
        "test_trade_count": int(result.get("test_trade_count", 0) or 0),
        "train_date_range": result.get("train_date_range") or {},
        "test_date_range": result.get("test_date_range") or {},
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
        "notes": notes,
    }


def _validation_fallback_reason(scope: str) -> str | None:
    if scope == "time_split":
        return "滚动样本外可比交易不足，已回退到 time-split 测试集。"
    if scope == "aggregate":
        return "样本外与时间切分测试样本不足，最终按全量指标保守判断。"
    return None

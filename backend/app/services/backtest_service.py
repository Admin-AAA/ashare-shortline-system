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
import uuid as _uuid
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.aggregator import apply_pool_rules
from app.engine.skill_loader import load_skills
from app.engine.strategy_runner import run_strategy
from app.models.tables import (
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
    _select_recall_candidates,
)
from app.services.outcome_service import is_trade_executed, label_pending_outcomes, normalized_outcome_label, resolved_trade_return
from app.services.prefilter_service import apply_prefilters
from app.services.research_context import build_research_contexts
from app.services.workbench_service import (
    _build_recommendation_card,
    _build_skill_rule_gate,
    _prelim_rule_score,
    _resolve_prefilter_target_count,
    _resolve_skill_review_limit,
)


def run_backtest(
    db: Session,
    start_date: str,
    end_date: str,
    run_mode: str = "experiment",
    horizon_days: int = 5,
    score_line: float = 72.0,
) -> dict[str, Any]:
    """在历史日期区间上执行回测，数据完全来自本地 DailyKbar 池。"""
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

    success_dates = [r for r in results if r.get("status") == "ok"]
    failed_dates = [r for r in results if r.get("status") != "ok"]

    summary = {
        "status": "completed",
        "dates_scanned": len(dates),
        "dates_succeeded": len(success_dates),
        "dates_failed": len(failed_dates),
        "total_recommendations": sum(r.get("recommendation_count", 0) for r in success_dates),
        "total_elapsed_ms": round(sum(r.get("elapsed_ms", 0) for r in results), 1),
        "labeled_count": labeled.get("labeled", 0),
        "results": results,
    }

    if summary["total_recommendations"] > 0:
        summary["breakdown"] = _backtest_breakdown(db, start_date, end_date, run_mode)
        summary["aggregate_metrics"] = _backtest_aggregate_metrics(db, start_date, end_date, run_mode)

    return summary


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
                "exchange": meta.get("exchange", ""),
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
        s.code: {"name": s.name, "industry": s.industry, "is_st": s.is_st, "exchange": s.exchange}
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
            "volume_ratio_5d": bar.volume_ratio_5d,
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
    run_id = f"bt_{trade_date}_{_uuid.uuid4().hex[:8]}"
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
        rec = Recommendation(
            recommendation_id=f"btrec_{_uuid.uuid4().hex[:12]}",
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
            feature_snapshot_json=json.dumps(card.get("feature_snapshot", {})),
            market_context_json=json.dumps(snapshot),
            skill_breakdown_json=json.dumps(card.get("strategy_signals", [])),
            agent_summary_json="{}",
            status="pending",
            recent_repeat=False,
        )
        db.add(rec)
    db.commit()


def _backtest_breakdown(
    db: Session, start_date: str, end_date: str, run_mode: str
) -> dict[str, Any]:
    """按 Skill / 行业 / 分数段 分组统计。"""
    recs = db.scalars(
        select(Recommendation).join(RecommendationRun).where(
            RecommendationRun.trade_date >= start_date,
            RecommendationRun.trade_date <= end_date,
            RecommendationRun.run_mode == run_mode,
        )
    ).all()

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
        is_success = outcome is not None and normalized_outcome_label(outcome) == "success"
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
        if outcome is not None and normalized_outcome_label(outcome) == "success":
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
        if outcome is not None and normalized_outcome_label(outcome) == "success":
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
        success_count = sum(
            1 for rec in executed if normalized_outcome_label(outcomes.get(rec.recommendation_id)) == "success"
        )
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


def _backtest_aggregate_metrics(
    db: Session, start_date: str, end_date: str, run_mode: str
) -> dict[str, Any]:
    """回测汇总指标。"""
    recs = db.scalars(
        select(Recommendation).join(RecommendationRun).where(
            RecommendationRun.trade_date >= start_date,
            RecommendationRun.trade_date <= end_date,
            RecommendationRun.run_mode == run_mode,
        )
    ).all()

    rec_ids = [r.recommendation_id for r in recs]
    if not rec_ids:
        return {"total_recommendations": 0, "labeled_count": 0, "triggered_count": 0, "trigger_rate": 0.0, "hit_rate": 0.0}

    outcomes = db.scalars(
        select(RecommendationOutcome).where(
            RecommendationOutcome.recommendation_id.in_(rec_ids)
        )
    ).all()

    if not outcomes:
        return {"total_recommendations": len(recs), "labeled_count": 0, "triggered_count": 0, "trigger_rate": 0.0, "hit_rate": 0.0}

    n = len(outcomes)
    triggered = [o for o in outcomes if is_trade_executed(o)]
    executed_n = len(triggered)
    success_count = sum(1 for o in triggered if normalized_outcome_label(o) == "success")
    returns = [resolved_trade_return(o) for o in triggered]
    weighted_returns = [float(o.weighted_return or 0) for o in triggered]
    max_returns = [o.max_return or 0 for o in outcomes]
    drawdowns = [o.max_drawdown or 0 for o in outcomes]
    achievable = [o.achievable_return or 0 for o in outcomes]
    holding_days = [float(o.holding_days or 0) for o in triggered]

    return {
        "total_recommendations": len(recs),
        "labeled_count": n,
        "triggered_count": executed_n,
        "trigger_rate": round(executed_n / max(1, n), 3),
        "success_count": success_count,
        "hit_rate": round(success_count / max(1, executed_n), 3) if executed_n else 0.0,
        "avg_return": round(sum(returns) / max(1, executed_n), 2) if executed_n else 0.0,
        "avg_executed_return": round(sum(returns) / max(1, executed_n), 2) if executed_n else 0.0,
        "avg_weighted_return": round(sum(weighted_returns) / max(1, executed_n), 2) if executed_n else 0.0,
        "avg_close_return": round(sum(float(o.close_return or 0) for o in outcomes) / max(1, n), 2),
        "avg_max_return": round(sum(max_returns) / max(1, n), 2),
        "avg_max_drawdown": round(sum(drawdowns) / max(1, n), 2),
        "avg_achievable_return": round(sum(achievable) / max(1, n), 2),
        "avg_holding_days": round(sum(holding_days) / max(1, executed_n), 2) if executed_n else 0.0,
        "profit_factor": _profit_factor(outcomes),
        "win_loss_ratio": _win_loss_ratio(outcomes),
    }


def _profit_factor(outcomes: list[RecommendationOutcome]) -> float:
    gross_profit = sum(resolved_trade_return(o) for o in outcomes if is_trade_executed(o) and resolved_trade_return(o) > 0)
    gross_loss = abs(sum(resolved_trade_return(o) for o in outcomes if is_trade_executed(o) and resolved_trade_return(o) < 0))
    return round(gross_profit / max(gross_loss, 0.01), 2)


def _win_loss_ratio(outcomes: list[RecommendationOutcome]) -> float:
    wins = sum(1 for o in outcomes if is_trade_executed(o) and normalized_outcome_label(o) == "success")
    losses = sum(1 for o in outcomes if is_trade_executed(o) and normalized_outcome_label(o) == "failed")
    return round(wins / max(losses, 1), 2)

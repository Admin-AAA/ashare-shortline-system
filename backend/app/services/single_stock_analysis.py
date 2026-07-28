"""单股票分析服务：加载K线 → 构建特征 → 计算基础短线策略评分 → 可选触发 LLM agent。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Preset, PresetSkill, RecommendationRun, Stock, StockQuote
from app.services.market_data import _build_stock_from_bars
from app.engine.trading_calendar import normalize_trade_date, latest_allowed_bar_date

logger = logging.getLogger(__name__)


def _resolve_analysis_market_snapshot(
    db: Session,
    trade_date: str,
    quote: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """为 analyze-stock 解析市场快照。

    优先：同 trade_date 最近 completed formal run 的 market_snapshot_json
    （全市场 sample_count/limit_up/regime/main_industries，与推荐管线同源）。
    回退：最小单股 snapshot（source=minimal_fallback；LLM 可能因 regime=未知 误判）。

    返回 (snapshot, source_tag)。
    """
    from app.services.market_data import _build_quote_snapshot

    try:
        raw = db.execute(
            select(RecommendationRun.market_snapshot_json)
            .where(
                RecommendationRun.run_mode == "formal",
                RecommendationRun.status == "completed",
                RecommendationRun.trade_date == trade_date,
                RecommendationRun.market_snapshot_json.is_not(None),
                RecommendationRun.market_snapshot_json != "",
                RecommendationRun.market_snapshot_json != "{}",
            )
            .order_by(RecommendationRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    except Exception as exc:  # pragma: no cover - 防御
        logger.warning("load formal market snapshot failed for %s: %s", trade_date, exc)
        raw = None

    if raw:
        try:
            snap = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            snap = None
        if isinstance(snap, dict) and int(snap.get("sample_count") or 0) >= 100:
            # 确保 trade_date 对齐分析日
            snap.setdefault("trade_date", trade_date)
            breadth = snap.get("breadth") if isinstance(snap.get("breadth"), dict) else {}
            snap["breadth"] = breadth
            return snap, "formal_run_snapshot"

    # fallback：最小单股 snapshot（保留旧行为，但打标）
    snapshot = _build_quote_snapshot([quote], "single", "single", [])
    snapshot["main_industries"] = []
    snapshot["breadth"] = snapshot.get("breadth") or {}
    snapshot["breadth"]["regime"] = "未知"
    snapshot["trade_date"] = trade_date
    snapshot["sample_count"] = int(snapshot.get("sample_count") or 1)
    return snapshot, "minimal_fallback"


def analyze_stock(
    db: Session,
    code: str,
    *,
    trade_date: str | None = None,
    include_llm: bool = False,
    skill_id: str = "daily-stock-analysis-skill",
    preset_id: str | None = None,
) -> dict[str, Any]:
    """对单只股票运行完整的特征构建 + 基础短线策略评分流程。

    Args:
        code: 股票代码（如 "000001"）
        trade_date: 分析日期（默认最近交易日）
        include_llm: 是否触发 LLM skill agent（消耗 API 额度）
        skill_id: 使用的 skill ID（单 skill 模式，preset_id 优先）
        preset_id: 预设 ID（多 skill 聚合评分模式，走完整聚合评分管线）

    Returns:
        包含股票信息、特征快照、基础短线策略评分、可选 LLM 分析结果
    """
    t0 = time.perf_counter()

    # 1. 确定目标日期
    if not trade_date:
        trade_date = latest_allowed_bar_date()
    trade_date = normalize_trade_date(trade_date)

    # 2. 加载股票基础信息
    stock = db.scalar(select(Stock).where(Stock.code == code))
    if not stock:
        return {"status": "error", "message": f"股票 {code} 不存在"}

    # 3. 加载行情快照
    quote_row = db.scalar(
        select(StockQuote)
        .where(StockQuote.code == code, StockQuote.freshness_status != "inactive")
        .order_by(StockQuote.snapped_at.desc())
        .limit(1)
    )

    # 4. 加载历史K线（最近120天）
    bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code == code, DailyKbar.trade_date <= trade_date)
        .order_by(DailyKbar.trade_date.asc())
    ).all()

    if len(bars) < 5:
        return {"status": "error", "message": f"股票 {code} 历史数据不足（仅 {len(bars)} 条）"}

    # 取最近120条
    bars = bars[-120:]

    # 5. 构建 quote dict
    if quote_row:
        quote = {
            "code": code,
            "name": quote_row.name or stock.name,
            "industry": stock.industry or "",
            "trade_date": str(quote_row.trade_date or trade_date),
            "price": float(quote_row.price or bars[-1].close or 0),
            "pct": float(quote_row.pct or 0),
            "volume": float(quote_row.volume or 0),
            "amount_yi": float(quote_row.amount_yi or 0),
            "turnover_rate": float(quote_row.turnover_rate or 0),
            "pe_ttm": float(quote_row.pe_ttm or 0) if quote_row.pe_ttm else None,
            "total_mv_yi": float(quote_row.total_mv_yi or 0) if quote_row.total_mv_yi else None,
            "free_mv_yi": float(quote_row.free_mv_yi or 0) if quote_row.free_mv_yi else None,
            "is_st": bool(stock.is_st),
            "exchange": "SH" if code.startswith(("6", "9")) else "SZ",
            "trade_date": trade_date,
            "main_net_inflow_yi": 0.0,
        }
    else:
        last_bar = bars[-1]
        quote = {
            "code": code,
            "name": stock.name,
            "industry": stock.industry or "",
            "trade_date": trade_date,
            "price": float(last_bar.close or 0),
            "pct": float(last_bar.pct_chg or 0),
            "volume": float(last_bar.volume or 0),
            "amount_yi": round(float(last_bar.amount or 0) / 1e8, 2),
            "turnover_rate": float(last_bar.turnover_rate or 0),
            "is_st": bool(stock.is_st),
            "exchange": "SH" if code.startswith(("6", "9")) else "SZ",
            "main_net_inflow_yi": 0.0,
        }

    # 6. 构建 bars list（与 backtest 一致）
    bar_dicts = []
    for bar in bars:
        bar_dicts.append({
            "trade_date": str(bar.trade_date),
            "open": float(bar.open or 0),
            "high": float(bar.high or 0),
            "low": float(bar.low or 0),
            "close": float(bar.close or 0),
            "volume": float(bar.volume or 0),
            "amount": float(bar.amount or 0),
            "pct_chg": float(bar.pct_chg or 0),
            "turnover_rate": float(bar.turnover_rate or 0),
            "ma5": bar.ma5,
            "ma10": bar.ma10,
            "ma20": bar.ma20,
            "ma60": bar.ma60,
            "volume_ratio_5d": bar.vol_ratio_5,
            "_indicator": {
                "rsi_6": bar.rsi_6,
                "rsi_12": bar.rsi_12,
                "rsi_24": bar.rsi_24,
                "macd_dif": bar.macd_dif,
                "macd_dea": bar.macd_dea,
                "macd_bar": bar.macd_bar,
                "bias_ma5": bar.bias_5,
                "bias_ma10": bar.bias_10,
                "bias_ma20": bar.bias_20,
                "breakout20": bar.breakout20,
                "breakout60": bar.breakout60,
                "upper_shadow_count20": bar.upper_shadow_count20,
                "max_dd20": bar.max_dd20,
                "max_dd60": bar.max_dd60,
            },
            "chip_winner_rate": bar.chip_winner_rate,
            "chip_weight_avg": bar.chip_weight_avg,
            "chip_cost_15": bar.chip_cost_15,
            "chip_cost_50": bar.chip_cost_50,
            "chip_cost_85": bar.chip_cost_85,
            "holder_pct": bar.holder_pct,
            "profit_efficiency": bar.profit_efficiency,
        })

    # 历史 ma 列可能全 NULL；读时现算，与生产 _build_stock_from_bars 一致
    from app.services.indicator_computer import fill_missing_local_indicators
    bar_dicts = fill_missing_local_indicators(bar_dicts)

    # 7. 构建特征 + 评分
    stock_result = _build_stock_from_bars(
        quote, bar_dicts, quote_source="single_analysis", kline_source="single_analysis"
    )

    # 8. 基础短线策略特征丰富（与推荐管线一致）
    from app.services.feature_builder import build_features
    from app.services.factors.registry import bootstrap_default_factor_registry
    from app.services.auxiliary.application import (
        inject_auxiliary_prefilter_flags,
        load_auxiliary_feature_views,
    )

    # 离线/脚本直接调 analyze_stock 时不会走 FastAPI lifespan → factor registry
    # 未 bootstrap 会 FactorError。幂等：main.py 已 bootstrap 时此处 no-op。
    bootstrap_default_factor_registry()

    # 提取 enrich 前的 profile（build_features 会 pop 掉 dsa_profile）
    profile_pre = dict(stock_result.get("dsa_profile") or {})

    # 市场快照：优先复用同 trade_date 最近 formal run 的真实全市场 snapshot
    # （sample_count/limit_up/regime/main_industries），避免 analyze-stock 的
    # 「最小 snapshot」让 LLM 看到 sample_count=1/regime=未知 而系统性误判
    # 情绪冰点并对主线票 hard veto（T8 2026-07-21 实证：3/3 票因 snapshot 残缺被 veto）。
    snapshot, snapshot_source = _resolve_analysis_market_snapshot(db, trade_date, quote)
    prefilter_auxiliary = load_auxiliary_feature_views(db, [code], trade_date=trade_date)
    inject_auxiliary_prefilter_flags([stock_result], prefilter_auxiliary)
    from app.services.workbench_service import _load_calibrated_dsa_weights

    enriched_list = build_features(
        [stock_result],
        snapshot,
        _load_calibrated_dsa_weights(db),
        db=db,
    )
    if enriched_list:
        stock_result = enriched_list[0]

    # 合并 profile（enrich 前的原始值 + enrich 后的计算值）
    profile = {**profile_pre, **{k: stock_result.get(k) for k in [
        "trend_status", "volume_status", "macd_status", "macd_signal",
        "rsi_status", "rsi_signal", "support_ma5", "support_ma10",
    ] if stock_result.get(k) is not None}}

    # 9. 提取关键特征
    feature_snapshot = stock_result.get("feature_snapshot", {})

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    result: dict[str, Any] = {
        "status": "ok",
        "code": code,
        "name": stock.name,
        "trade_date": trade_date,
        "bar_count": len(bar_dicts),
        "elapsed_ms": elapsed_ms,
        "price": quote.get("price"),
        "market_snapshot_source": snapshot_source,
        "market_snapshot_summary": {
            "sample_count": snapshot.get("sample_count"),
            "limit_up_count": snapshot.get("limit_up_count"),
            "limit_down_count": snapshot.get("limit_down_count"),
            "regime": (snapshot.get("breadth") or {}).get("regime"),
            "up_ratio": (snapshot.get("breadth") or {}).get("up_ratio"),
            "hot_theme": (snapshot.get("breadth") or {}).get("hot_theme"),
            "main_industries_n": len(snapshot.get("main_industries") or []),
        },
        "dsa_profile": {
            "macd_status": profile.get("macd_status"),
            "macd_dif": profile.get("macd_dif"),
            "macd_dea": profile.get("macd_dea"),
            "rsi_12": profile.get("rsi_12"),
            "bias_ma5": profile.get("bias_ma5"),
            "bias_ma10": profile.get("bias_ma10"),
            "trend_status": profile.get("trend_status"),
            "volume_status": profile.get("volume_status"),
        },
        "scoring": {
            "dsa_signal_score": stock_result.get("dsa_signal_score"),
            "dsa_trend_score": stock_result.get("dsa_trend_score"),
            "dsa_bias_score": stock_result.get("dsa_bias_score"),
            "dsa_volume_score": stock_result.get("dsa_volume_score"),
            "dsa_support_score": stock_result.get("dsa_support_score"),
            "dsa_macd_score": stock_result.get("dsa_macd_score"),
            "dsa_rsi_score": stock_result.get("dsa_rsi_score"),
            "risk_score": stock_result.get("risk_score"),
        },
        "entry_rules": {
            "ideal_buy": stock_result.get("ideal_buy") or feature_snapshot.get("ideal_buy"),
            "secondary_buy": stock_result.get("secondary_buy") or feature_snapshot.get("secondary_buy"),
            "stop_loss": feature_snapshot.get("stop_loss"),
            "take_profit": feature_snapshot.get("take_profit"),
        },
    }

    # 9. LLM agent 分析（可选，消耗 API 额度）
    if include_llm:
        if preset_id:
            # 预设多 skill 聚合评分模式：走完整聚合评分管线
            llm_result = _run_preset_skill_analysis(db, code, stock_result, preset_id, snapshot)
        else:
            # 单 skill 模式（传真实 snapshot，含 trade_date/breadth，避免 LLM 拿 mock regime=未知 + cache trade_date=NULL）
            llm_result = _run_llm_analysis(db, code, stock_result, skill_id, snapshot)
        result["llm_analysis"] = llm_result

    result["final_score"] = _resolve_single_stock_final_score(result)

    _persist_single_stock_history(db, code, trade_date, result)

    return result


def _persist_single_stock_history(db: Session, code: str, trade_date: str, result: dict[str, Any]) -> None:
    """落库单股票评分历史（复用 stock_matrix_history 表，analysis_type="single_stock"）。

    失败仅记日志、不阻塞主流程（与 stock_matrix_service 同款约定）。
    """
    try:
        from app.models.tables import StockMatrixHistory

        final_score = _resolve_single_stock_final_score(result)

        # skill_count：preset 模式取有效 skill 数，单 skill 模式=1
        llm = result.get("llm_analysis")
        skill_count = 1
        if llm and isinstance(llm.get("skill_reviews"), list):
            valid = [r for r in llm["skill_reviews"] if r.get("status") == "ok"]
            skill_count = len(valid) if valid else len(llm["skill_reviews"])

        db.add(StockMatrixHistory(
            code=code,
            trade_date=trade_date,
            analysis_type="single_stock",
            final_score=final_score,
            skill_count=skill_count,
            result_json=json.dumps(result, ensure_ascii=False, default=str),
            elapsed_ms=result.get("elapsed_ms"),
        ))
        db.commit()
    except Exception as hist_exc:
        logger.warning("写入单股票评分历史失败 (code=%s): %s", code, hist_exc)
        db.rollback()


def _resolve_single_stock_final_score(result: dict[str, Any]) -> float | None:
    """返回单股记录唯一总分，口径与页面实际评分一致且限定为 0–100。

    preset 模式优先使用推荐管线聚合分；否则使用已经归一化的
    ``dsa_signal_score``。后者本身就是六个技术子分的总分，不能再次求和。
    """
    explicit = result.get("final_score")
    llm = result.get("llm_analysis") or {}
    value = explicit if explicit is not None else llm.get("final_score")
    if value is None:
        value = (result.get("scoring") or {}).get("dsa_signal_score")
    if value is None:
        return None
    try:
        return round(max(0.0, min(100.0, float(value))), 2)
    except (TypeError, ValueError):
        return None


def _evaluate_single_stock_rule_signals(
    stock: dict[str, Any],
    selected_skills: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> dict[str, dict[str, Any] | None]:
    """使用正式推荐管线的 StrategyRegistry 逐 Skill 计算规则信号。"""
    from app.engine.strategy import StrategyContext, build_registry_from_skills

    registry = build_registry_from_skills([item["skill"] for item in selected_skills])
    context = StrategyContext(main_industries=list(snapshot.get("main_industries") or []))
    signals: dict[str, dict[str, Any] | None] = {}
    for selected in selected_skills:
        skill = selected["skill"]
        signal = registry.get(skill.skill_id).evaluate(stock, context) if skill.executable else None
        if signal is not None:
            signal["weight"] = selected["weight"]
        signals[skill.skill_id] = signal
    return signals


def _run_preset_skill_analysis(
    db: Session,
    code: str,
    stock_result: dict[str, Any],
    preset_id: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """预设多 skill 聚合评分：加载预设 skills → 并发 LLM 审查 → 聚合评分。

    复用推荐管线的规则评估、run_skill_reviews 与卡片构建入口。
    """
    import asyncio

    try:
        from app.core.config import get_settings
        from app.engine.skill_loader import load_skills
        from app.agents.review_runner import run_skill_reviews
        from app.services.feature_builder import market_regime_score
        from app.services.research_context import build_research_context
        from app.services.workbench_service import (
            _build_recommendation_card,
            _load_calibrated_agg_weights,
            _prelim_rule_score,
            _select_skills_for_run,
        )

        settings = get_settings()
        all_skills = load_skills(settings.skills_dir)
        skill_map = {s.skill_id: s for s in all_skills}

        # 加载预设
        preset = db.get(Preset, preset_id)
        if not preset:
            return {"status": "error", "error": f"预设 {preset_id} 不存在"}

        skill_rows = db.scalars(
            select(PresetSkill)
            .where(PresetSkill.preset_id == preset_id)
            .order_by(PresetSkill.sort_order.asc())
        ).all()
        requested_skills = [{"skill_id": row.skill_id, "weight": row.weight} for row in skill_rows]

        selected_skills = _select_skills_for_run(db, skill_map, requested_skills)
        if not selected_skills:
            return {"status": "error", "error": f"预设 {preset.name} 没有可运行的 Skill"}

        # 与正式推荐管线一致：ResearchContext → StrategyRegistry → strategy.yaml
        # 逐 Skill 规则信号。旧实现把同一个 dsa 字典冒充所有策略信号，导致规则层失真。
        research_context = build_research_context(stock_result, snapshot)
        pipeline_stock = research_context.to_skill_input()
        rule_signals_by_skill = _evaluate_single_stock_rule_signals(
            pipeline_stock, selected_skills, snapshot,
        )

        # 并发调用所有 Skill Agent
        skills_list = [s["skill"] for s in selected_skills]
        session_id = f"single-stock-{code}-{preset_id}"

        t0 = time.perf_counter()
        skill_reviews = asyncio.run(run_skill_reviews(
            stock=pipeline_stock,
            skills=skills_list,
            snapshot=snapshot,
            rule_signals_by_skill=rule_signals_by_skill,
            session_id=session_id,
        ))
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        # 计算 agent_review_score（置信度加权平均）
        valid_reviews = [r for r in skill_reviews if r.get("review") and not r.get("error")]
        if valid_reviews:
            scores = [float(r["review"].get("score", 50)) for r in valid_reviews]
            confidences = [float(r["review"].get("confidence", 0.5)) for r in valid_reviews]
            agent_review_score = sum(s * c for s, c in zip(scores, confidences)) / max(sum(confidences), 0.01)
        else:
            agent_review_score = 50.0

        # 复用正式推荐管线的卡片构建入口：真实市场分、校准权重、规则信号
        # 与 Agent 审查的融合方式均保持一致。
        entry = {
            "prelim_score": _prelim_rule_score(pipeline_stock, rule_signals_by_skill, snapshot),
            "context": research_context,
            "rule_signals": rule_signals_by_skill,
            "review_skill_ids": [item["skill_id"] for item in selected_skills],
            "stock": pipeline_stock,
        }
        card = _build_recommendation_card(
            entry,
            skill_reviews,
            skill_map=skill_map,
            selected_skills=selected_skills,
            market_score=market_regime_score(snapshot) or 50.0,
            risk_template=preset.risk_template,
            calibrated_weights=_load_calibrated_agg_weights(db),
            snapshot=snapshot,
        )

        # 收集 API 消耗
        try:
            from app.agents.agno_skill_agent import drain_api_costs
            api_costs = drain_api_costs()
        except Exception:
            api_costs = []

        return {
            "status": "ok",
            "elapsed_ms": elapsed_ms,
            "preset_id": preset_id,
            "preset_name": preset.name,
            "risk_template": preset.risk_template,
            "final_score": round(float(card.get("final_score", 0)), 2),
            "component_scores": card.get("component_scores", {}),
            "agent_review_score": round(agent_review_score, 2),
            "skill_reviews": [
                {
                    "skill_id": r.get("skill_id"),
                    "skill_name": r.get("skill_name"),
                    "status": "error" if r.get("error") else "ok",
                    "error": r.get("error"),
                    "score": r.get("review", {}).get("score") if r.get("review") else None,
                    "stance": r.get("review", {}).get("stance") if r.get("review") else None,
                    "confidence": r.get("review", {}).get("confidence") if r.get("review") else None,
                    "summary": r.get("review", {}).get("summary") if r.get("review") else None,
                    "action": r.get("review", {}).get("action") if r.get("review") else None,
                    "supports": r.get("review", {}).get("supports") or [] if r.get("review") else [],
                    "risks": r.get("review", {}).get("risks") or [] if r.get("review") else [],
                }
                for r in skill_reviews
            ],
            "api_costs": api_costs,
            "total_tokens_est": sum((c.get("prompt_tokens_est") or 0) + (c.get("completion_tokens_est") or 0) for c in api_costs),
            "total_tokens_real": sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in api_costs),
            "total_cache_hit_tokens": sum((c.get("cache_hit_tokens") or 0) for c in api_costs),
            "prompt_cache_active": any((c.get("cache_hit_tokens") or 0) > 0 for c in api_costs if c.get("status") == "success"),
        }
    except Exception as exc:
        logger.warning("Preset skill analysis failed for %s (preset=%s): %s", code, preset_id, exc)
        return {"status": "error", "error": str(exc)}


def _run_llm_analysis(
    db: Session, code: str, stock_result: dict[str, Any], skill_id: str, snapshot: dict[str, Any] | None = None
) -> dict[str, Any]:
    """触发单 Skill 深审，并用正式推荐管线聚合最终分。"""
    import asyncio

    try:
        from app.agents.review_runner import run_skill_reviews
        from app.engine.skill_loader import load_skills
        from app.core.config import get_settings
        from app.services.feature_builder import market_regime_score
        from app.services.research_context import build_research_context
        from app.services.workbench_service import (
            _build_recommendation_card,
            _load_calibrated_agg_weights,
            _prelim_rule_score,
        )

        settings = get_settings()
        all_skills = load_skills(settings.skills_dir)
        skill = next((s for s in all_skills if s.skill_id == skill_id), None)
        if not skill:
            return {"status": "error", "error": f"Skill {skill_id} not found"}

        # 用 analyze_stock 构造的真实 snapshot（含 trade_date/breadth/main_industries），
        # 避免 LLM 拿 mock regime=未知 影响判断 + cache trade_date=NULL 无法追溯。
        if snapshot is None:
            snapshot = {"sample_count": 1, "breadth": {"regime": "未知"}, "main_industries": []}
        selected_skills = [{
            "skill_id": skill.skill_id,
            "name": skill.name,
            "weight": 1.0,
            "skill": skill,
        }]
        research_context = build_research_context(stock_result, snapshot)
        pipeline_stock = research_context.to_skill_input()
        rule_signals_by_skill = _evaluate_single_stock_rule_signals(
            pipeline_stock, selected_skills, snapshot,
        )
        rule_signal = rule_signals_by_skill.get(skill_id)

        t0 = time.perf_counter()
        skill_reviews = asyncio.run(run_skill_reviews(
            stock=pipeline_stock,
            skills=[skill],
            snapshot=snapshot,
            rule_signals_by_skill=rule_signals_by_skill,
            session_id=f"single-stock-{code}-{skill_id}",
        ))
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        review_result = skill_reviews[0] if skill_reviews else {}
        raw = review_result.get("review") or {}
        entry = {
            "prelim_score": _prelim_rule_score(pipeline_stock, rule_signals_by_skill, snapshot),
            "context": research_context,
            "rule_signals": rule_signals_by_skill,
            "review_skill_ids": [skill_id],
            "stock": pipeline_stock,
        }
        card = _build_recommendation_card(
            entry,
            skill_reviews,
            skill_map={skill_id: skill},
            selected_skills=selected_skills,
            market_score=market_regime_score(snapshot) or 50.0,
            risk_template="balanced",
            calibrated_weights=_load_calibrated_agg_weights(db),
            snapshot=snapshot,
        )
        rule_score = float((rule_signal or {}).get("score") or 0.0)
        final_score = round(float(card.get("final_score") or 0.0), 2)
        # 收集 API 消耗
        try:
            from app.agents.agno_skill_agent import drain_api_costs
            api_costs = drain_api_costs()
        except Exception:
            api_costs = []
        return {
            "status": "fallback" if review_result.get("error") or not raw else "ok",
            "error": review_result.get("error"),
            "elapsed_ms": elapsed_ms,
            "skill_id": skill_id,
            "score": raw.get("score"),
            "score_delta": round(float(raw.get("score", 50)) - 50.0, 2),
            "rule_score": round(rule_score, 2),
            "final_score": final_score,
            "component_scores": card.get("component_scores", {}),
            "llm_adjustment": round(final_score - rule_score, 2),
            "stance": raw.get("stance") or ("support" if rule_score >= 72 else "neutral_support" if rule_score >= 60 else "neutral"),
            "confidence": raw.get("confidence") if raw else 0.25,
            "summary": raw.get("summary") or review_result.get("error") or "Skill Agent 暂不可用，已按正式规则信号聚合",
            "action": raw.get("action") or "观察",
            "market_regime_judgment": raw.get("market_regime_judgment"),
            "supports": raw.get("evidence") or raw.get("supports") or [],
            "risks": raw.get("risks") or card.get("risk_flags") or [],
            "api_costs": api_costs,
            # 估算用量（chars/1.8 回退，用于 provider 未返回 usage 时的展示）
            "total_tokens_est": sum((c.get("prompt_tokens_est") or 0) + (c.get("completion_tokens_est") or 0) for c in api_costs),
            # 真实用量（来自 provider usage 字段）；0 表示未取得真实值
            "total_tokens_real": sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in api_costs),
            # prompt cache 命中量（DeepSeek prompt_tokens_details.cached_tokens）；>0 即证明 prompt_cache_key 机制生效
            "total_cache_hit_tokens": sum((c.get("cache_hit_tokens") or 0) for c in api_costs),
            "prompt_cache_active": any((c.get("cache_hit_tokens") or 0) > 0 for c in api_costs if c.get("status") == "success"),
        }
    except Exception as exc:
        logger.warning("LLM analysis failed for %s: %s", code, exc)
        # LLM 失败时返回降级 stance（而非纯 error），避免 analyze-stock 用户看不到任何判断
        # 同 formal run 的 fallback_prelim 逻辑（workbench_service._build_recommendation_card）
        prelim_score = float(stock_result.get("dsa_signal_score") or 50.0)
        return {
            "status": "fallback",
            "error": str(exc),
            "stance": "support" if prelim_score >= 72 else "neutral_support" if prelim_score >= 60 else "neutral",
            "score_delta": round(max(-18.0, min(16.0, prelim_score - 50.0)), 2),
            "score": round(prelim_score, 2),
            "confidence": 0.35 if prelim_score >= 70 else 0.25,
            "summary": f"Skill Agent 暂不可用（{str(exc)[:60]}），已降级为规则预审",
            "runtime": "agent_skill_fallback_prelim",
            "rule_score": prelim_score,
        }

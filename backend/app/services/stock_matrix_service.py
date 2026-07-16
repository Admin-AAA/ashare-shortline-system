"""单股票全 Skill 矩阵分析：对一只股票运行全部 Skill 的规则评分 + 可选 LLM Agent 审查。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill, load_skills
from app.engine.strategy_runner import run_strategy
from app.engine.aggregator import aggregate
from app.models.tables import DailyKbar, Stock, StockQuote
from app.services.market_data import _build_stock_from_bars, _build_quote_snapshot
from app.engine.trading_calendar import normalize_trade_date, latest_allowed_bar_date
from app.services.feature_builder import build_features
from sqlalchemy import select

logger = logging.getLogger(__name__)


def analyze_stock_matrix(
    db: Session,
    code: str,
    *,
    trade_date: str | None = None,
    include_llm: bool = False,
    skill_ids: list[str] | None = None,
) -> dict[str, Any]:
    """对单只股票运行全部 Skill 的规则评分 + 可选 LLM Agent 审查。

    Args:
        code: 股票代码
        trade_date: 分析日期（默认最近交易日）
        include_llm: 是否触发 LLM agent（消耗 API 额度，8 个 skill 约 30s）
        skill_ids: 限定 skill 列表（默认全部已启用 skill）

    Returns:
        {status, code, name, trade_date, price, dsa_profile, skill_results, aggregated, elapsed_ms}
    """
    t0 = time.perf_counter()
    settings = get_settings()

    # ── 1. 加载 Skill ──
    all_skills = load_skills(settings.skills_dir)
    active_skills = [s for s in all_skills if s.enabled]
    if skill_ids:
        id_set = set(skill_ids)
        active_skills = [s for s in active_skills if s.skill_id in id_set]
    if not active_skills:
        return {"status": "error", "message": "无可用的 Skill"}

    # ── 2. 加载股票数据 ──
    if not trade_date:
        trade_date = latest_allowed_bar_date()
    trade_date = normalize_trade_date(trade_date)

    stock = db.scalar(select(Stock).where(Stock.code == code))
    if not stock:
        return {"status": "error", "message": f"股票 {code} 不存在"}

    quote_row = db.scalar(
        select(StockQuote)
        .where(StockQuote.code == code, StockQuote.freshness_status != "inactive")
        .order_by(StockQuote.snapped_at.desc())
        .limit(1)
    )

    bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code == code, DailyKbar.trade_date <= trade_date)
        .order_by(DailyKbar.trade_date.asc())
    ).all()
    if len(bars) < 5:
        return {"status": "error", "message": f"股票 {code} 历史数据不足（仅 {len(bars)} 条）"}
    bars = bars[-120:]

    # ── 3. 构建 quote + bars ──
    quote = _build_quote_dict(code, stock, quote_row, bars[-1], trade_date)
    bar_dicts = _build_bar_dicts(bars)

    # ── 4. 构建特征 ──
    stock_result = _build_stock_from_bars(quote, bar_dicts, quote_source="stock_matrix", kline_source="stock_matrix")
    profile_pre = dict(stock_result.get("dsa_profile") or {})

    snapshot = _build_quote_snapshot([quote], "stock_matrix", "stock_matrix", [])
    snapshot["main_industries"] = []
    snapshot["breadth"] = snapshot.get("breadth", {})
    snapshot["breadth"]["regime"] = "未知"

    enriched_list = build_features([stock_result], snapshot)
    if enriched_list:
        stock_result = enriched_list[0]

    # ── 5. 规则评分（全部 Skill）──
    main_industries: list[str] = list(snapshot.get("main_industries") or [])
    rule_signals_by_skill: dict[str, dict[str, Any]] = {}
    skill_results: list[dict[str, Any]] = []

    for skill in active_skills:
        try:
            signal = run_strategy(skill, stock_result, main_industries, skip_universe_check=True)
        except Exception as exc:
            logger.warning("Skill %s rule scoring failed for %s: %s", skill.skill_id, code, exc)
            signal = _empty_signal(skill)

        rule_signals_by_skill[skill.skill_id] = signal
        skill_results.append({
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "rule_score": signal.get("score", 0),
            "rule_phase": signal.get("phase", ""),
            "rule_evidence": signal.get("evidence", []),
            "rule_risk_flags": signal.get("risk_flags", []),
            "hard_veto": signal.get("hard_veto", False),
            "veto_reason": signal.get("veto_reason", ""),
            "setup_match": signal.get("setup_match", ""),
            "entry_rule": signal.get("entry_rule", ""),
            "invalid_rule": signal.get("invalid_rule", ""),
            "llm_review": None,
        })

    # ── 6. LLM Agent 审查（可选）──
    agent_review_score = 50.0
    if include_llm:
        import uuid
        session_id = f"stock_matrix_{code}_{uuid.uuid4().hex[:8]}"
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有 event loop 中（FastAPI），用 nest_asyncio 或直接 run
                reviews = asyncio.run(_run_reviews(stock_result, active_skills, snapshot, rule_signals_by_skill, session_id))
            else:
                reviews = asyncio.run(_run_reviews(stock_result, active_skills, snapshot, rule_signals_by_skill, session_id))
        except RuntimeError:
            reviews = asyncio.run(_run_reviews(stock_result, active_skills, snapshot, rule_signals_by_skill, session_id))

        review_map = {r["skill_id"]: r for r in reviews if r.get("skill_id")}
        score_sum = 0.0
        score_count = 0
        for sr in skill_results:
            review = review_map.get(sr["skill_id"])
            if not review:
                continue
            raw_review = review.get("review")
            if raw_review:
                sr["llm_review"] = {
                    "stance": raw_review.get("stance", "neutral"),
                    "score": raw_review.get("score", 50),
                    "score_delta": raw_review.get("score_delta", 0),
                    "confidence": raw_review.get("confidence", 0.5),
                    "supports": raw_review.get("supports") or raw_review.get("evidence") or [],
                    "risks": raw_review.get("risks") or [],
                    "summary": raw_review.get("summary", ""),
                    "action": raw_review.get("action", "观察"),
                    "position_range": raw_review.get("position_range", "0-5%"),
                    "hallucination_check": review.get("hallucination_check"),
                    "error": review.get("error"),
                }
                score_sum += float(raw_review.get("score", 50))
                score_count += 1
            elif review.get("error"):
                sr["llm_review"] = {"status": "error", "error": review["error"]}

        if score_count > 0:
            agent_review_score = score_sum / score_count

    # ── 7. 聚合评分 ──
    aggregated = aggregate(
        stock=stock_result,
        signals=[rule_signals_by_skill[s.skill_id] for s in active_skills if s.skill_id in rule_signals_by_skill],
        market_score=50.0,
        agent_review_score=agent_review_score,
        market_snapshot=snapshot,
    )

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

    result = {
        "status": "ok",
        "code": code,
        "name": stock.name,
        "trade_date": trade_date,
        "price": quote.get("price"),
        "dsa_profile": {
            "macd_status": profile_pre.get("macd_status") or stock_result.get("macd_status"),
            "macd_dif": profile_pre.get("macd_dif") or stock_result.get("macd_dif"),
            "rsi_12": profile_pre.get("rsi_12") or stock_result.get("rsi_12"),
            "bias_ma5": profile_pre.get("bias_ma5") or stock_result.get("bias_ma5"),
            "trend_status": stock_result.get("trend_status"),
            "volume_status": stock_result.get("volume_status"),
        },
        "skill_results": skill_results,
        "aggregated": {
            "final_score": aggregated.get("final_score", 0),
            "consensus_score": aggregated.get("consensus_score", 0),
            "component_scores": aggregated.get("component_scores", {}),
            "risk_flags": aggregated.get("risk_flags", []),
            "consensus_tags": aggregated.get("consensus_tags", []),
            "skill_disagreement": aggregated.get("skill_disagreement", {}),
            "stance_summary": aggregated.get("stance_summary", []),
        },
        "elapsed_ms": elapsed_ms,
    }

    # 写入历史记录（不阻塞主流程，失败仅记日志）
    try:
        from app.models.tables import StockMatrixHistory
        final_score = float(aggregated.get("final_score", 0) or 0)
        db.add(StockMatrixHistory(
            code=code,
            trade_date=trade_date,
            analysis_type="analysis",
            final_score=final_score,
            skill_count=len(skill_results),
            result_json=json.dumps(result, ensure_ascii=False, default=str),
            elapsed_ms=elapsed_ms,
        ))
        db.commit()
    except Exception as hist_exc:
        logger.warning("写入 stock_matrix_history 失败: %s", hist_exc)
        db.rollback()

    return result


async def _run_reviews(
    stock: dict[str, Any],
    skills: list[StrategySkill],
    snapshot: dict[str, Any],
    rule_signals_by_skill: dict[str, dict[str, Any]],
    session_id: str,
) -> list[dict[str, Any]]:
    from app.agents.review_runner import run_skill_reviews
    return await run_skill_reviews(stock, skills, snapshot, rule_signals_by_skill, session_id)


def _build_quote_dict(
    code: str, stock: Any, quote_row: Any, last_bar: Any, trade_date: str
) -> dict[str, Any]:
    if quote_row:
        return {
            "code": code,
            "name": quote_row.name or stock.name,
            "industry": stock.industry or "",
            "trade_date": str(quote_row.trade_date or trade_date),
            "price": float(quote_row.price or last_bar.close or 0),
            "pct": float(quote_row.pct or 0),
            "volume": float(quote_row.volume or 0),
            "amount_yi": float(quote_row.amount_yi or 0),
            "turnover_rate": float(quote_row.turnover_rate or 0),
            "pe_ttm": float(quote_row.pe_ttm or 0) if quote_row.pe_ttm else None,
            "total_mv_yi": float(quote_row.total_mv_yi or 0) if quote_row.total_mv_yi else None,
            "free_mv_yi": float(quote_row.free_mv_yi or 0) if quote_row.free_mv_yi else None,
            "is_st": bool(stock.is_st),
            "exchange": "SH" if code.startswith(("6", "9")) else "SZ",
            "main_net_inflow_yi": 0.0,
        }
    return {
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


def _build_bar_dicts(bars: list[Any]) -> list[dict[str, Any]]:
    result = []
    for bar in bars:
        result.append({
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
    return result


def _empty_signal(skill: StrategySkill) -> dict[str, Any]:
    return {
        "strategy_id": skill.skill_id,
        "strategy_name": skill.name,
        "version_id": "",
        "phase": "评分失败",
        "score": 0,
        "confidence": 0,
        "setup_match": "",
        "entry_rule": "",
        "invalid_rule": "",
        "hard_veto": False,
        "veto_reason": "",
        "risk_flags": [],
        "evidence": [f"Skill {skill.name} 规则评分异常"],
    }

"""单股票分析服务：加载K线 → 构建特征 → 计算 DSA 评分 → 可选触发 LLM agent。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Stock, StockQuote
from app.services.market_data import _build_stock_from_bars
from app.services.market_pool import _normalize_trade_date, _latest_allowed_bar_date

logger = logging.getLogger(__name__)


def analyze_stock(
    db: Session,
    code: str,
    *,
    trade_date: str | None = None,
    include_llm: bool = False,
    skill_id: str = "daily-stock-analysis-skill",
) -> dict[str, Any]:
    """对单只股票运行完整的特征构建 + DSA 评分流程。

    Args:
        code: 股票代码（如 "000001"）
        trade_date: 分析日期（默认最近交易日）
        include_llm: 是否触发 LLM skill agent（消耗 API 额度）
        skill_id: 使用的 skill ID

    Returns:
        包含股票信息、特征快照、DSA 评分、可选 LLM 分析结果
    """
    t0 = time.perf_counter()

    # 1. 确定目标日期
    if not trade_date:
        trade_date = _latest_allowed_bar_date()
    trade_date = _normalize_trade_date(trade_date)

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

    # 7. 构建特征 + 评分
    stock_result = _build_stock_from_bars(
        quote, bar_dicts, quote_source="single_analysis", kline_source="single_analysis"
    )

    # 8. DSA 特征丰富（与推荐管线一致）
    from app.services.feature_builder import build_features
    from app.services.market_data import _build_quote_snapshot, _candidate_filter_summary

    # 提取 enrich 前的 profile（build_features 会 pop 掉 dsa_profile）
    profile_pre = dict(stock_result.get("dsa_profile") or {})

    # 构建最小 snapshot
    quote_list = [quote]
    snapshot = _build_quote_snapshot(quote_list, "single", "single", [])
    snapshot["main_industries"] = []
    snapshot["breadth"] = snapshot.get("breadth", {})
    snapshot["breadth"]["regime"] = "未知"
    enriched_list = build_features([stock_result], snapshot)
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
        llm_result = _run_llm_analysis(db, code, stock_result, skill_id)
        result["llm_analysis"] = llm_result

    return result


def _run_llm_analysis(
    db: Session, code: str, stock_result: dict[str, Any], skill_id: str
) -> dict[str, Any]:
    """触发 LLM skill agent 分析单只股票。消耗 API 额度。"""
    import asyncio

    try:
        from app.agents.agno_skill_agent import run_skill_agent_direct
        from app.engine.skill_loader import load_skills
        from app.core.config import get_settings

        settings = get_settings()
        all_skills = load_skills(settings.skills_dir)
        skill = next((s for s in all_skills if s.skill_id == skill_id), None)
        if not skill:
            return {"status": "error", "error": f"Skill {skill_id} not found"}

        snapshot = {"sample_count": 1, "breadth": {"regime": "未知"}, "main_industries": []}
        rule_signal = {
            "dsa_signal_score": stock_result.get("dsa_signal_score"),
            "trend_status": stock_result.get("trend_status"),
            "macd_status": stock_result.get("macd_status"),
        }

        t0 = time.perf_counter()
        review = asyncio.run(run_skill_agent_direct(
            skill=skill,
            stock=stock_result,
            snapshot=snapshot,
            rule_signal=rule_signal,
        ))
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        raw = review.model_dump() if hasattr(review, "model_dump") else (review if isinstance(review, dict) else {})
        # 收集 API 消耗
        try:
            from app.agents.agno_skill_agent import drain_api_costs
            api_costs = drain_api_costs()
        except Exception:
            api_costs = []
        return {
            "status": "ok",
            "elapsed_ms": elapsed_ms,
            "skill_id": skill_id,
            "score": raw.get("score"),
            "score_delta": round(float(raw.get("score", 50)) - 50.0, 2),
            # 规则层 DSA 评分作为基线，让用户能看到 LLM 减分/加分幅度
            # T8 第70轮验证：LLM 净价值 +84.24%（过滤 27 笔 WR=14.8% 的垃圾推荐）
            "rule_score": stock_result.get("scoring", {}).get("dsa_signal_score") if isinstance(stock_result.get("scoring"), dict) else stock_result.get("dsa_signal_score"),
            "llm_adjustment": round(float(raw.get("score", 50)) - float(stock_result.get("scoring", {}).get("dsa_signal_score", 50) if isinstance(stock_result.get("scoring"), dict) else stock_result.get("dsa_signal_score", 50)), 2),
            "stance": raw.get("stance"),
            "confidence": raw.get("confidence"),
            "summary": raw.get("summary"),
            "action": raw.get("action"),
            "market_regime_judgment": raw.get("market_regime_judgment"),
            "supports": raw.get("evidence") or raw.get("supports") or [],
            "risks": raw.get("risks") or [],
            "api_costs": api_costs,
            "total_tokens_est": sum(c.get("prompt_tokens_est", 0) + c.get("completion_tokens_est", 0) for c in api_costs),
        }
    except Exception as exc:
        logger.warning("LLM analysis failed for %s: %s", code, exc)
        return {"status": "error", "error": str(exc)}

"""
历史回测服务：在历史日期上重跑推荐流水线并自动标注结果。

工作流：
1. 接受日期区间
2. 从本地 DailyBar 池加载历史 K 线数据，构建伪行情
3. 运行推荐流水线（规则引擎，不调用 LLM）
4. 用未来 N 天的实际走势标注结果
5. 汇总统计：胜率、平均收益、最大回撤、按 Skill/行业/Bucket 分组
"""

from __future__ import annotations

import json
import math
import uuid as _uuid
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.skill_loader import load_skills
from app.models.tables import (
    DailyBar,
    DailyIndicator,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    Stock,
)
from app.services.feature_builder import build_features, market_regime_score
from app.services.outcome_service import label_pending_outcomes


def run_backtest(
    db: Session,
    start_date: str,
    end_date: str,
    run_mode: str = "experiment",
    horizon_days: int = 5,
) -> dict[str, Any]:
    """在历史日期区间上执行回测，数据完全来自本地 DailyBar 池。"""
    settings = get_settings()
    dates = _trading_dates_in_range(db, start_date, end_date)
    if not dates:
        return {"status": "no_data", "message": "区间内 DailyBar 无可用交易日数据", "dates_scanned": 0}

    results: list[dict[str, Any]] = []
    for trade_date in dates:
        t0 = time.perf_counter()
        try:
            date_result = _backtest_single_date(db, trade_date, run_mode, settings)
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
    """获取区间内 DailyBar 中存在的交易日（至少 100 只股票有数据）。"""
    rows = db.execute(
        select(
            DailyBar.trade_date,
            func.count().label("cnt"),
        )
        .where(DailyBar.trade_date >= start, DailyBar.trade_date <= end)
        .group_by(DailyBar.trade_date)
        .having(func.count() >= 100)
        .order_by(DailyBar.trade_date.asc())
    ).all()
    return [row.trade_date for row in rows if row.cnt >= 100]


def _is_supported_a_share(code: str) -> bool:
    return len(code) == 6 and code[0] in {"0", "2", "3", "6", "8", "4"}


def _select_candidates_from_bars(
    bars_for_date: list[dict[str, Any]],
    stocks_meta: dict[str, dict[str, Any]],
    chg60_map: dict[str, float],
) -> list[dict[str, Any]]:
    """从 DailyBar 数据中筛选候选股票（模拟 _select_candidates 逻辑）。"""
    candidates = []
    for bar in bars_for_date:
        code = bar["code"]
        meta = stocks_meta.get(code, {})
        if not _is_supported_a_share(code):
            continue
        if meta.get("is_st"):
            continue
        price = float(bar.get("close") or 0)
        if price <= 1:
            continue
        amount = float(bar.get("amount") or 0)
        amount_yi = amount / 1e8
        if amount_yi < 0.5:
            continue
        turnover = float(bar.get("turnover_rate") or 0)
        # 回测中 turnover_rate 数据可能缺失（多数源不提供），仅做非零过滤
        if turnover < 0:
            continue

        pct = float(bar.get("pct_chg") or 0)
        chg60 = chg60_map.get(code, 0.0)

        score = (
            math.log1p(max(0.0, amount_yi)) * 12
            + max(-8.0, min(10.0, pct)) * 2.5
            + max(-20.0, min(80.0, chg60)) * 0.25
            + min(12.0, turnover) * 1.2
        )
        bar["_candidate_score"] = score
        bar["_amount_yi"] = amount_yi
        bar["_chg60"] = chg60
        candidates.append(bar)

    candidates.sort(key=lambda b: b["_candidate_score"], reverse=True)
    return candidates


def _backtest_single_date(
    db: Session,
    trade_date: str,
    run_mode: str,
    settings: Any,
) -> dict[str, Any]:
    """对单个历史日期运行推荐流水线（纯 DailyBar 驱动，不依赖 UnifiedQuote）。"""
    # 1. 加载当日所有 DailyBar
    all_bars = db.scalars(
        select(DailyBar).where(DailyBar.trade_date == trade_date)
    ).all()
    if len(all_bars) < 100:
        return {"trade_date": trade_date, "status": "skipped", "reason": f"该日期仅 {len(all_bars)} 条日K"}

    # 2. 加载 Stock 元数据
    codes = [bar.code for bar in all_bars]
    stock_rows = db.scalars(select(Stock).where(Stock.code.in_(codes))).all()
    stocks_meta: dict[str, dict[str, Any]] = {
        s.code: {"name": s.name, "industry": s.industry, "is_st": s.is_st, "exchange": s.exchange}
        for s in stock_rows
    }

    # 3. 计算 chg60（需要前60个交易日数据）
    chg60_map = _compute_chg60_for_date(db, codes, trade_date)

    # 4. 构建 bars 列表并筛选候选
    bars_for_date: list[dict[str, Any]] = []
    for bar in all_bars:
        bars_for_date.append({
            "code": bar.code,
            "trade_date": bar.trade_date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume or 0,
            "amount": bar.amount or 0,
            "pct_chg": bar.pct_chg or 0,
            "turnover_rate": bar.turnover_rate or 0,
            "ma5": bar.ma5,
            "ma10": bar.ma10,
            "ma20": bar.ma20,
            "ma60": bar.ma60,
            "volume_ratio_5d": bar.volume_ratio_5d,
        })

    candidates = _select_candidates_from_bars(bars_for_date, stocks_meta, chg60_map)
    max_scan = max(100, min(int(settings.market_scan_size), 500))
    candidates = candidates[:max_scan]

    # 5. 加载每只股票截至 trade_date 的完整历史 K 线（用于特征计算）
    bars_history = _load_bars_history_for_date(db, [c["code"] for c in candidates], trade_date)

    # 6. 构建 stocks 列表
    stocks = _build_stocks_from_bars(candidates, bars_history, stocks_meta, trade_date, db)

    # 7. 构建市场快照（从 stocks 元数据统计主线行业）
    snapshot = _build_historical_snapshot_from_bars(all_bars, stocks_meta, trade_date)

    # 8. 运行特征工程（不使用辅助数据，因为历史日期辅助数据不全）
    enriched = build_features(stocks, snapshot, db=None)
    market_score = market_regime_score(snapshot)

    # 9. 加载 Skills，运行规则引擎
    from app.engine.strategy_runner import run_strategy
    from app.engine.aggregator import aggregate

    all_skills = load_skills(settings.skills_dir)
    skill_registries = {
        r.skill_id: r
        for r in db.scalars(select(__import__("app.models.tables", fromlist=["SkillRegistry"]).SkillRegistry)).all()
    }
    run_skills = []
    for skill in all_skills:
        reg = skill_registries.get(skill.skill_id)
        if reg and reg.mode == run_mode and reg.enabled:
            run_skills.append(skill)

    if not run_skills:
        return {"trade_date": trade_date, "status": "skipped", "reason": "无可用的 Skill"}

    recommendations = []
    for stock in enriched:
        signals = []
        for skill in run_skills:
            signal = run_strategy(skill, stock, snapshot.get("main_industries", []))
            if signal and signal.get("score", 0) > 0:
                signals.append(signal)

        if not signals:
            continue

        card = aggregate(
            stock=stock,
            signals=signals,
            market_score=market_score,
            agent_review_score=50.0,
            risk_template="balanced",
            market_snapshot=snapshot,
        )

        if card.get("final_score", 0) >= 45:
            card["trade_date"] = trade_date
            card["recommended_at"] = datetime.utcnow().isoformat()
            recommendations.append(card)

    # 10. 持久化
    run_id = _persist_backtest_run(db, trade_date, run_mode, len(candidates), len(recommendations), run_skills)
    _persist_backtest_recommendations(db, run_id, recommendations, snapshot)

    return {
        "trade_date": trade_date,
        "status": "ok",
        "run_id": run_id,
        "candidate_count": len(candidates),
        "recommendation_count": len(recommendations),
        "skill_count": len(run_skills),
    }


def _compute_chg60_for_date(
    db: Session, codes: list[str], trade_date: str
) -> dict[str, float]:
    """计算每只股票在 trade_date 的 60 日涨幅。"""
    result: dict[str, float] = {}
    for code in codes:
        base_bar = db.scalar(
            select(DailyBar.close)
            .where(DailyBar.code == code, DailyBar.trade_date <= trade_date)
            .order_by(DailyBar.trade_date.desc())
            .offset(59)
            .limit(1)
        )
        current_bar = db.scalar(
            select(DailyBar.close)
            .where(DailyBar.code == code, DailyBar.trade_date == trade_date)
        )
        if base_bar and current_bar and base_bar > 0:
            result[code] = round((current_bar - base_bar) / base_bar * 100, 2)
    return result


def _load_bars_history_for_date(
    db: Session, codes: list[str], trade_date: str
) -> dict[str, list[dict[str, Any]]]:
    """加载截至 trade_date 每只股票的完整历史 K 线（最多120天）。"""
    all_bars = db.scalars(
        select(DailyBar)
        .where(DailyBar.code.in_(codes), DailyBar.trade_date <= trade_date)
        .order_by(DailyBar.code, DailyBar.trade_date.asc())
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
    db: Session,
) -> list[dict[str, Any]]:
    """从历史 K 线数据构建股票特征输入。"""
    from app.services.market_data import _apply_relative_features
    from app.models.tables import UnifiedQuote

    # 从最新 UnifiedQuote 加载市值数据（市值日间变化小，可跨日期使用）
    _codes = [c["code"] for c in candidates]
    quote_data: dict[str, dict[str, Any]] = {}
    if _codes:
        latest_quotes = db.scalars(
            select(UnifiedQuote).where(UnifiedQuote.code.in_(_codes))
        ).all()
        for uq in latest_quotes:
            try:
                q = json.loads(uq.quote_json or "{}")
                quote_data[uq.code] = q
            except (json.JSONDecodeError, TypeError):
                pass

    stocks = []
    for candidate in candidates:
        code = candidate["code"]
        meta = stocks_meta.get(code, {})
        bars = bars_history.get(code, [])
        if not bars:
            continue

        # 找 trade_date 对应的 bar
        target_bar = None
        for bar in reversed(bars):
            if bar["trade_date"] == trade_date:
                target_bar = bar
                break
        if not target_bar:
            target_bar = bars[-1]

        close = float(target_bar["close"])
        # 计算当日 pct: (close - prev_close) / prev_close * 100
        prev_close = None
        for i in range(len(bars) - 1, -1, -1):
            if bars[i]["trade_date"] == trade_date:
                if i > 0:
                    prev_close = float(bars[i - 1]["close"])
                break
        if prev_close and prev_close > 0:
            pct = round((close - prev_close) / prev_close * 100, 2)
        else:
            pct = float(target_bar.get("pct_chg") or 0)

        stock = {
            "code": code,
            "name": meta.get("name", code),
            "industry": meta.get("industry", "未分类"),
            "price": close,
            "close": close,
            "open": float(target_bar["open"]),
            "high": float(target_bar["high"]),
            "low": float(target_bar["low"]),
            "volume": float(target_bar.get("volume") or 0),
            "amount_yi": round((float(target_bar.get("amount") or 0)) / 1e8, 2),
            "pct": pct,
            "turnover_rate": float(target_bar.get("turnover_rate") or 0),
            "free_mv_yi": float(quote_data.get(code, {}).get("free_mv_yi") or 0),
            "total_mv_yi": float(quote_data.get(code, {}).get("total_mv_yi") or 0),
            "pe_ttm": float(quote_data.get(code, {}).get("pe_ttm") or 0),
            "is_st": meta.get("is_st", False),
            "exchange": meta.get("exchange", ""),
            "announcement_negative_count": 0,
            "data_source": "local_pool_backtest",
            "quote_source": "local_pool_backtest",
            "kline_source": "local_pool_backtest",
            "latest_trade_date": trade_date,
            "data_quality": {"source_breakdown": {"OBSERVED": 50, "SYNTHETIC": 50}},
        }

        # 附加 DailyBar 指标字段
        for field in ("ma5", "ma10", "ma20", "ma60", "volume_ratio_5d"):
            val = target_bar.get(field)
            if val is not None:
                stock[field] = float(val)

        stocks.append(stock)

    if stocks:
        _apply_relative_features(stocks)

    return stocks


def _build_historical_snapshot_from_bars(
    all_bars: list[Any], stocks_meta: dict[str, dict[str, Any]], trade_date: str
) -> dict[str, Any]:
    """从 DailyBar 数据（含行业元数据）计算市场快照。"""
    up_count = 0
    down_count = 0
    limit_up_count = 0
    limit_down_count = 0

    # 按行业统计成交额，识别主线行业
    industry_amount: dict[str, float] = {}
    industry_up_count: dict[str, int] = {}
    industry_total: dict[str, int] = {}

    for bar in all_bars:
        pct = float(getattr(bar, "pct_chg", None) or 0)
        if pct > 0:
            up_count += 1
        elif pct < 0:
            down_count += 1
        if pct >= 9.8:
            limit_up_count += 1
        elif pct <= -9.8:
            limit_down_count += 1

        code = bar.code
        meta = stocks_meta.get(code, {})
        industry = str(meta.get("industry") or "未分类")
        amount = float(getattr(bar, "amount", None) or 0)
        industry_amount[industry] = industry_amount.get(industry, 0) + amount
        industry_total[industry] = industry_total.get(industry, 0) + 1
        if pct > 0:
            industry_up_count[industry] = industry_up_count.get(industry, 0) + 1

    # 按 成交额 * (1 + up_ratio) 排序，选出 Top 3 主线行业
    industry_scores = {}
    for ind in industry_amount:
        up_ratio_ind = industry_up_count.get(ind, 0) / max(1, industry_total.get(ind, 1))
        industry_scores[ind] = industry_amount[ind] * (1 + max(0, up_ratio_ind))
    main_industries = [
        ind for ind, _ in sorted(industry_scores.items(), key=lambda x: x[1], reverse=True)[:3]
        if ind != "未分类"
    ]

    n = len(all_bars)
    up_ratio = up_count / max(1, n)

    if up_ratio >= 0.65:
        regime = "强牛"
    elif up_ratio >= 0.5:
        regime = "偏多"
    elif up_ratio >= 0.35:
        regime = "震荡"
    elif up_ratio >= 0.2:
        regime = "偏空"
    else:
        regime = "弱势"

    from app.services.market_data import _breadth_from_counts
    breadth = _breadth_from_counts(n, up_count, down_count, limit_up_count, limit_down_count)
    breadth["data_source"] = "daily_bar_backtest"
    breadth["regime"] = regime

    return {
        "snapshot_id": f"backtest_{trade_date}",
        "trade_date": trade_date,
        "sample_count": n,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "main_industries": main_industries,
        "breadth": breadth,
    }


def _persist_backtest_run(
    db: Session,
    trade_date: str,
    run_mode: str,
    universe_count: int,
    recommended_count: int,
    skills: list[Any],
) -> str:
    run_id = f"bt_{trade_date}_{_uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()
    run = RecommendationRun(
        run_id=run_id,
        run_name=f"回测 {trade_date}",
        run_mode=run_mode,
        trade_date=trade_date,
        started_at=now,
        finished_at=now,
        market_scope="全市场",
        risk_template="balanced",
        output_count=20,
        universe_count=universe_count,
        recommended_count=recommended_count,
        status="completed",
        selected_skills_json=json.dumps([s.skill_id for s in skills]),
        params_json=json.dumps({"backtest": True, "trade_date": trade_date, "run_mode": run_mode}),
        market_snapshot_json="{}",
        result_summary_json=json.dumps({"backtest": True}),
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
    now = datetime.utcnow()
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
            bucket=card.get("bucket", "证据不足"),
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
    """按 Skill / 行业 / Bucket 分组统计。"""
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

    skill_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "success": 0, "scores": []})
    for rec in recs:
        signals = json.loads(rec.skill_breakdown_json or "[]")
        outcome = outcomes.get(rec.recommendation_id)
        is_success = outcome is not None and outcome.outcome_label == "success"
        for signal in signals:
            sid = signal.get("strategy_id", "unknown")
            skill_stats[sid]["count"] += 1
            skill_stats[sid]["scores"].append(float(signal.get("score", 0)))
            if is_success:
                skill_stats[sid]["success"] += 1

    by_skill = []
    for sid, stats in sorted(skill_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        n = stats["count"]
        by_skill.append({
            "skill_id": sid,
            "recommendation_count": n,
            "success_count": stats["success"],
            "hit_rate": round(stats["success"] / max(1, n), 3),
            "avg_score": round(sum(stats["scores"]) / max(1, len(stats["scores"])), 2),
        })

    industry_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "success": 0})
    for rec in recs:
        industry = rec.industry or "未分类"
        outcome = outcomes.get(rec.recommendation_id)
        industry_stats[industry]["count"] += 1
        if outcome is not None and outcome.outcome_label == "success":
            industry_stats[industry]["success"] += 1

    by_industry = []
    for ind, stats in sorted(industry_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        n = stats["count"]
        by_industry.append({
            "industry": ind,
            "recommendation_count": n,
            "success_count": stats["success"],
            "hit_rate": round(stats["success"] / max(1, n), 3),
        })

    bucket_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "success": 0})
    for rec in recs:
        outcome = outcomes.get(rec.recommendation_id)
        bucket_stats[rec.bucket]["count"] += 1
        if outcome is not None and outcome.outcome_label == "success":
            bucket_stats[rec.bucket]["success"] += 1

    by_bucket = []
    for bucket, stats in sorted(bucket_stats.items(), key=lambda x: x[1]["count"], reverse=True):
        n = stats["count"]
        by_bucket.append({
            "bucket": bucket,
            "recommendation_count": n,
            "success_count": stats["success"],
            "hit_rate": round(stats["success"] / max(1, n), 3),
        })

    return {
        "by_skill": by_skill,
        "by_industry": by_industry,
        "by_bucket": by_bucket,
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
        return {"total_recommendations": 0, "labeled": 0, "hit_rate": 0}

    outcomes = db.scalars(
        select(RecommendationOutcome).where(
            RecommendationOutcome.recommendation_id.in_(rec_ids)
        )
    ).all()

    if not outcomes:
        return {"total_recommendations": len(recs), "labeled": 0, "hit_rate": 0}

    n = len(outcomes)
    success_count = sum(1 for o in outcomes if o.outcome_label == "success")
    returns = [o.close_return or 0 for o in outcomes]
    max_returns = [o.max_return or 0 for o in outcomes]
    drawdowns = [o.max_drawdown or 0 for o in outcomes]
    achievable = [o.achievable_return or 0 for o in outcomes]

    return {
        "total_recommendations": len(recs),
        "labeled_count": n,
        "success_count": success_count,
        "hit_rate": round(success_count / max(1, n), 3),
        "avg_close_return": round(sum(returns) / max(1, n), 2),
        "avg_max_return": round(sum(max_returns) / max(1, n), 2),
        "avg_max_drawdown": round(sum(drawdowns) / max(1, n), 2),
        "avg_achievable_return": round(sum(achievable) / max(1, n), 2),
        "profit_factor": _profit_factor(outcomes),
        "win_loss_ratio": _win_loss_ratio(outcomes),
    }


def _profit_factor(outcomes: list[RecommendationOutcome]) -> float:
    gross_profit = sum(o.close_return for o in outcomes if o.close_return and o.close_return > 0)
    gross_loss = abs(sum(o.close_return for o in outcomes if o.close_return and o.close_return < 0))
    return round(gross_profit / max(gross_loss, 0.01), 2)


def _win_loss_ratio(outcomes: list[RecommendationOutcome]) -> float:
    wins = sum(1 for o in outcomes if o.outcome_label == "success")
    losses = sum(1 for o in outcomes if o.outcome_label == "failed")
    return round(wins / max(losses, 1), 2)

import json
import logging
import threading
import time
from bisect import bisect_left
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun, SkillRegistry
from app.services.evaluation_metrics import compute_full_metrics as _compute_full_metrics
from app.services.execution_constraints import (
    entry_execution_block_reason_from_bar,
    exit_execution_block_reason_from_bar,
    is_one_word_limit_down_bar,
    is_one_word_limit_up_bar,
    no_liquidity_reason_from_bar,
)

ENTRY_LOOKAHEAD_DAYS = 2
BREAKDOWN_HORIZON_DAYS = (3, 5, 10)
ENTRY_SLIPPAGE_BPS = 10.0
EXIT_SLIPPAGE_BPS = 10.0
SUCCESS_RETURN_PCT = 3.0
DEFAULT_STOP_LOSS_PCT = -6.5   # 基于历史回测最优（-7% 接近 breakeven，保守取 -6.5%）
DEFAULT_TAKE_PROFIT_PCT = 5.0  # 回测：horizon_close 65/92 峰值不达 6.5%，TP=5.0% 模拟 WR 57.2%→71.7%（+14.5pp, 22 saved）
                                 # avg peak 4.27%，33/65 峰值在 3-5%，TP 下修捕获更多止盈窗口
MAX_STOP_LOSS_PCT = -9.0       # 止损最宽 -9%，高波动股允许更宽止损
MAX_TAKE_PROFIT_PCT = 15.0     # 止盈上限（+15%），防止 feature_builder 设置过高的止盈
STOP_LOSS_GAP_CAP_PCT = -15.0  # 跳空止损最大亏损上限：open 远低于止损价时，模拟"隔夜止损单"的极限（科创板 20cm 跌停场景）
MAX_ENTRY_GAP_PCT = 10.0       # 跳空高开 > 10% 则放弃入场（历史数据：gap>10% 胜率 0%）

OUTCOME_PENDING = "pending"
OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_MISSED = "missed"
EXECUTED_OUTCOME_LABELS = {OUTCOME_SUCCESS, OUTCOME_FAILED}

# 短时缓存
_metrics_cache: dict[str, tuple[float, Any]] = {}
_metrics_cache_lock = threading.Lock()
_METRICS_CACHE_TTL = 30.0  # 指标缓存 30 秒（原 5 秒过短，冷启动 350ms 频繁触发）


def _cached(key: str, factory):
    now = time.time()
    with _metrics_cache_lock:
        entry = _metrics_cache.get(key)
        if entry is not None and (now - entry[0]) < _METRICS_CACHE_TTL:
            return entry[1]
    result = factory()
    with _metrics_cache_lock:
        _metrics_cache[key] = (time.time(), result)
    return result


def invalidate_outcome_metrics_cache() -> None:
    with _metrics_cache_lock:
        _metrics_cache.clear()


def normalized_outcome_label(outcome: RecommendationOutcome | Any | None) -> str:
    if outcome is None:
        return OUTCOME_PENDING
    raw_label = str(getattr(outcome, "outcome_label", OUTCOME_PENDING) or OUTCOME_PENDING).lower()
    if raw_label == OUTCOME_PENDING:
        return OUTCOME_PENDING
    if raw_label in {OUTCOME_MISSED, "not_triggered"}:
        return OUTCOME_MISSED
    if raw_label == "risk_hit":
        return OUTCOME_FAILED
    if str(getattr(outcome, "label_source", "") or "").lower() == "manual" and raw_label in {
        OUTCOME_SUCCESS,
        OUTCOME_FAILED,
    }:
        return raw_label
    if not bool(getattr(outcome, "entry_triggered", False)):
        return OUTCOME_MISSED
    return OUTCOME_SUCCESS if raw_label == OUTCOME_SUCCESS else OUTCOME_FAILED


def is_outcome_labeled(outcome: RecommendationOutcome | Any | None) -> bool:
    return normalized_outcome_label(outcome) != OUTCOME_PENDING


def is_trade_executed(outcome: RecommendationOutcome | Any | None) -> bool:
    return bool(outcome is not None and getattr(outcome, "entry_triggered", False))


def resolved_trade_return(outcome: RecommendationOutcome | Any | None) -> float:
    if outcome is None:
        return 0.0
    executed = getattr(outcome, "executed_return", None)
    if executed is not None:
        return float(executed or 0.0)
    return float(getattr(outcome, "close_return", 0.0) or 0.0)


def is_accurate_recommendation(outcome: RecommendationOutcome | Any | None) -> bool:
    """严格准确推荐口径：真实成交，且扣费后净收益为正。

    executed_return 已含滑点但未扣佣金+印花税（_ADDITIONAL_COST_PCT=0.10%），
    阈值必须覆盖这部分成本，与 outcome_label 判定保持一致。
    """
    return bool(is_trade_executed(outcome) and resolved_trade_return(outcome) > _ADDITIONAL_COST_PCT)


def label_pending_outcomes(
    db: Session,
    horizon_days: int = 5,
    run_mode: str | None = None,
) -> dict[str, Any]:
    """对 pending 推荐做执行口径标注。

    保留原有静态窗口指标（close/max/min/achievable），同时新增：
    - 是否真实触发买点
    - 进出场价格/日期
    - 执行收益（含双边滑点）
    - 持仓天数 / 仓位权重 / 退出原因
    """
    stmt = (
        select(Recommendation)
        .outerjoin(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(
            or_(
                RecommendationOutcome.recommendation_id.is_(None),
                RecommendationOutcome.outcome_label == OUTCOME_PENDING,
                Recommendation.status == OUTCOME_PENDING,
            )
        )
    )
    if run_mode:
        stmt = stmt.join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id).where(
            RecommendationRun.run_mode == run_mode
        )
    recs = db.scalars(stmt.order_by(Recommendation.trade_date.asc())).unique().all()
    if not recs:
        return {"labeled": 0}

    codes = sorted({str(rec.code) for rec in recs})
    min_trade_date = min(str(rec.trade_date) for rec in recs)
    bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date >= min_trade_date)
        .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
    ).all()

    bars_by_code: dict[str, list[DailyKbar]] = {}
    dates_by_code: dict[str, list[str]] = {}
    for bar in bars:
        code = str(bar.code)
        bars_by_code.setdefault(code, []).append(bar)
        dates_by_code.setdefault(code, []).append(str(bar.trade_date))

    labeled = 0
    errors = 0
    for rec in recs:
        simulation = _simulate_outcome(
            rec,
            bars_by_code.get(str(rec.code), []),
            dates_by_code.get(str(rec.code), []),
            horizon_days=horizon_days,
        )
        if simulation is None:
            continue

        savepoint = db.begin_nested()
        try:
            existing = db.get(RecommendationOutcome, rec.recommendation_id)
            if existing is not None:
                outcome = existing
            else:
                outcome = RecommendationOutcome(recommendation_id=rec.recommendation_id)
                db.add(outcome)

            outcome.horizon_days = horizon_days
            outcome.entry_triggered = bool(simulation["entry_triggered"])
            outcome.entry_date = simulation["entry_date"]
            outcome.entry_price = simulation["entry_price"]
            outcome.exit_date = simulation["exit_date"]
            outcome.exit_price = simulation["exit_price"]
            outcome.executed_return = simulation["executed_return"]
            outcome.weighted_return = simulation["weighted_return"]
            outcome.position_size = simulation["position_size"]
            outcome.holding_days = simulation["holding_days"]
            outcome.entry_slippage_bps = simulation["entry_slippage_bps"]
            outcome.exit_slippage_bps = simulation["exit_slippage_bps"]
            outcome.stop_loss_price = simulation["stop_loss_price"]
            outcome.take_profit_price = simulation["take_profit_price"]
            outcome.exit_reason = simulation["exit_reason"]
            outcome.max_return = simulation["max_return"]
            outcome.min_return = simulation["min_return"]
            outcome.close_return = simulation["close_return"]
            outcome.achievable_return = simulation["achievable_return"]
            outcome.max_drawdown = simulation["max_drawdown"]
            outcome.hit_target = bool(simulation["hit_target"])
            outcome.hit_invalid = bool(simulation["hit_invalid"])
            outcome.outcome_label = str(simulation["outcome_label"])
            outcome.outcome_label_legacy = str(simulation["outcome_label_legacy"])
            outcome.outcome_reason = str(simulation["outcome_reason"])
            outcome.label_source = "auto"
            outcome.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
            rec.status = normalized_outcome_label(outcome)
            savepoint.commit()
            labeled += 1
        except Exception:
            savepoint.rollback()
            errors += 1
            logger.warning("outcome labeling failed for %s", rec.recommendation_id, exc_info=True)

    db.commit()
    if labeled:
        invalidate_outcome_metrics_cache()
    return {"labeled": labeled, "errors": errors}


def overview_metrics(db: Session, run_mode: str = "formal") -> dict[str, Any]:
    return _cached(f"overview:{run_mode}", lambda: _overview_metrics(db, run_mode))


def _overview_metrics(db: Session, run_mode: str) -> dict[str, Any]:
    rows = _scoped_recommendation_rows(db, run_mode)
    total_recs = len(rows)

    labeled = [row for row in rows if normalized_outcome_label(row) != OUTCOME_PENDING]
    triggered = [row for row in labeled if is_trade_executed(row)]
    success_count = sum(1 for row in labeled if is_accurate_recommendation(row))
    failed_count = sum(1 for row in triggered if not is_accurate_recommendation(row))
    missed_count = max(0, len(labeled) - len(triggered))
    n = len(labeled)
    executed_n = len(triggered)

    if not labeled:
        empty_metrics = _compute_full_metrics([])
        return {
            **empty_metrics,
            "mode": run_mode,
            "total_recommendations": total_recs,
            "labeled_count": 0,
            "triggered_count": 0,
            "executed_trade_count": 0,
            "unexecuted_count": 0,
            "missed_count": missed_count,
            "pending_count": max(0, total_recs - n),
            "success_count": 0,
            "failed_count": 0,
        }

    # ── 收集交易日期 → 批量查询基准指数 ──
    all_dates: set[str] = set()
    for row in triggered:
        entry_date = str(getattr(row, "entry_date", "") or getattr(row, "trade_date", ""))
        exit_date = str(getattr(row, "exit_date", "") or getattr(row, "trade_date", ""))
        all_dates.add(entry_date)
        all_dates.add(exit_date)

    from app.services.benchmark_service import compute_benchmark_return as _bench_return
    from app.services.benchmark_service import get_benchmark_close_map as _get_close_map
    close_map = _get_close_map(db, list(all_dates))

    # ── 构建推荐级记录 → 统一指标引擎 ──
    metric_records: list[dict[str, Any]] = []
    for row in labeled:
        executed = is_trade_executed(row)
        executed_return = resolved_trade_return(row) if executed else 0.0
        benchmark_ret = 0.0
        if executed:
            entry_date = str(getattr(row, "entry_date", "") or getattr(row, "trade_date", ""))
            exit_date = str(getattr(row, "exit_date", "") or getattr(row, "trade_date", ""))
            benchmark_ret = _bench_return(close_map, entry_date, exit_date)
        accurate = bool(executed and executed_return > 0)
        false_positive_reason = (
            getattr(row, "outcome_reason", None)
            if not accurate
            else None
        )
        if false_positive_reason is None and not executed:
            false_positive_reason = "未触发买点或不可真实成交"
        metric_records.append({
            "net_return_pct": float(executed_return),
            "gross_return_pct": float(getattr(row, "close_return", 0) or 0),
            "achievable_return_pct": float(getattr(row, "achievable_return", 0) or 0),
            "morning_drawdown_pct": float(getattr(row, "max_drawdown", 0) or 0),
            "holding_days": float(getattr(row, "holding_days", 0 if not executed else 1) or 0),
            "benchmark_return_pct": benchmark_ret,
            "accurate": accurate,
            "executed": executed,
            "trade_date": str(getattr(row, "trade_date", "")),
            "code": getattr(row, "code", ""),
            "name": getattr(row, "name", ""),
            "false_positive_reason": false_positive_reason,
            "resolved": True,
        })

    full_metrics = _compute_full_metrics(metric_records)

    return {
        "mode": run_mode,
        "total_recommendations": total_recs,
        "labeled_count": n,
        "triggered_count": executed_n,
        "executed_trade_count": executed_n,
        "unexecuted_count": max(0, n - executed_n),
        "trigger_rate": round(executed_n / n, 3) if n else 0.0,
        "missed_count": missed_count,
        "pending_count": max(0, total_recs - n),
        "success_count": success_count,
        "failed_count": failed_count,
        # 9-KPI 来自统一指标引擎
        "hit_rate": full_metrics.get("hit_rate", 0.0),
        "avg_return": full_metrics.get("avg_return", 0.0),
        "avg_executed_return": full_metrics.get("avg_executed_return", 0.0),
        "avg_weighted_return": full_metrics.get("avg_weighted_return", 0.0),
        "avg_close_return": full_metrics.get("avg_close_return", 0.0),
        "avg_mfe": full_metrics.get("avg_max_return", 0.0),
        "avg_mae": full_metrics.get("avg_max_drawdown", 0.0),
        "avg_holding_days": full_metrics.get("avg_holding_days", 0.0),
        "recommendation_accuracy": full_metrics.get("recommendation_accuracy", 0.0),
        "average_net_return": full_metrics.get("average_net_return", 0.0),
        "median_net_return": full_metrics.get("median_net_return", 0.0),
        "profit_loss_ratio": full_metrics.get("profit_loss_ratio", 0.0),
        "max_drawdown": full_metrics.get("max_drawdown", 0.0),
        "positive_month_ratio": full_metrics.get("positive_month_ratio", 0.0),
        "benchmark_excess_return": full_metrics.get("benchmark_excess_return", 0.0),
        "sample_confidence": full_metrics.get("sample_confidence", "low"),
        "false_positive_notes": full_metrics.get("false_positive_notes", []),
        "false_positive_category_counts": full_metrics.get("false_positive_category_counts", []),
        "accuracy_policy": full_metrics.get("accuracy_policy", {}),
        "accuracy_gate_summary": full_metrics.get("accuracy_gate_summary", {}),
        "strategy_compound_return": full_metrics.get("strategy_compound_return", 0.0),
        "benchmark_compound_return": full_metrics.get("benchmark_compound_return", 0.0),
        "trade_accuracy": full_metrics.get("trade_accuracy", 0.0),
        "accuracy_numerator": full_metrics.get("accuracy_numerator", 0),
        "accuracy_denominator": full_metrics.get("accuracy_denominator", n),
        "accuracy_basis": full_metrics.get("accuracy_basis", "labeled_recommendations"),
    }


def metrics_by_strategy(db: Session, run_mode: str = "formal") -> list[dict[str, Any]]:
    return _cached(f"by_strategy:{run_mode}", lambda: _metrics_by_strategy(db, run_mode))


def _metrics_by_strategy(db: Session, run_mode: str) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, run_mode)
    benchmark_by_rec = _benchmark_returns_by_recommendation(db, rows)
    horizon_records = _horizon_metric_records_by_recommendation(db, rows)
    registry = {row.skill_id: row for row in db.scalars(select(SkillRegistry)).all()}

    buckets: dict[str, dict[str, Any]] = {}
    for rec in rows:
        for signal in _loads(rec.skill_breakdown_json, []):
            sid = str(signal.get("strategy_id") or "unknown")
            skill = registry.get(sid)
            item = buckets.setdefault(
                sid,
                {
                    "strategy_id": sid,
                    "strategy_name": skill.name if skill else sid,
                    "current_mode": skill.mode if skill else "unknown",
                    "sample_count": 0,
                    "avg_score": 0.0,
                    "rows": [],
                },
            )
            item["sample_count"] += 1
            item["avg_score"] += float(signal.get("score", 0))
            item["rows"].append(rec)

    result = []
    for item in buckets.values():
        count = max(1, item["sample_count"])
        metric_rows = _metric_records_from_rows(item["rows"], benchmark_by_rec)
        metrics = _compute_full_metrics(metric_rows)
        result.append({
            "strategy_id": item["strategy_id"],
            "strategy_name": item["strategy_name"],
            "current_mode": item["current_mode"],
            "sample_count": item["sample_count"],
            "labeled_count": metrics["trade_count"],
            "triggered_count": metrics["executed_trade_count"],
            "missed_count": metrics["unexecuted_count"],
            "success_count": metrics["success_count"],
            "trigger_rate": metrics["trigger_rate"],
            "hit_rate": metrics["trade_accuracy"],
            "avg_score": round(item["avg_score"] / count, 2),
            "avg_return": metrics["average_executed_return"],
            "horizon_metrics": _horizon_metrics_for_rows(item["rows"], horizon_records),
            **_group_metric_fields(metrics),
        })
    return sorted(result, key=lambda x: (x["sample_count"], x["hit_rate"], x["avg_score"]), reverse=True)


def metrics_by_setup(db: Session, run_mode: str = "formal") -> list[dict[str, Any]]:
    return _cached(f"by_setup:{run_mode}", lambda: _metrics_by_setup(db, run_mode))


def _metrics_by_setup(db: Session, run_mode: str) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, run_mode)
    benchmark_by_rec = _benchmark_returns_by_recommendation(db, rows)
    horizon_records = _horizon_metric_records_by_recommendation(db, rows)
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        setup = str(row.setup_type or "未命中买点")
        item = buckets.setdefault(
            setup,
            {
                "setup_type": setup,
                "sample_count": 0,
                "avg_score": 0.0,
                "rows": [],
            },
        )
        item["sample_count"] += 1
        item["avg_score"] += float(row.final_score or 0)
        item["rows"].append(row)

    result = []
    for item in buckets.values():
        sample_count = max(1, int(item["sample_count"]))
        metric_rows = _metric_records_from_rows(item["rows"], benchmark_by_rec)
        metrics = _compute_full_metrics(metric_rows)
        result.append(
            {
                "setup_type": item["setup_type"],
                "sample_count": item["sample_count"],
                "labeled_count": metrics["trade_count"],
                "triggered_count": metrics["executed_trade_count"],
                "missed_count": metrics["unexecuted_count"],
                "success_count": metrics["success_count"],
                "trigger_rate": metrics["trigger_rate"],
                "hit_rate": metrics["trade_accuracy"],
                "avg_score": round(item["avg_score"] / sample_count, 2),
                "avg_return": metrics["average_executed_return"],
                "avg_drawdown": metrics["avg_max_drawdown"],
                "horizon_metrics": _horizon_metrics_for_rows(item["rows"], horizon_records),
                **_group_metric_fields(metrics),
            }
        )
    return sorted(result, key=lambda item: (item["sample_count"], item["hit_rate"], item["avg_score"]), reverse=True)


def failure_attribution(db: Session, run_mode: str = "formal") -> dict[str, Any]:
    """失败归因分析：按退出原因分组统计失败模式。

    返回：
    - failure_modes: 每种退出原因的胜/败统计
    - gap_up_impact: 跳空高开过滤的理论影响
    - stop_loss_quality: 止损执行质量（止损后是否真的避免了更大亏损）
    """
    stmt = (
        select(RecommendationOutcome, Recommendation)
        .join(Recommendation, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .where(RecommendationOutcome.entry_triggered == True)  # noqa: E712
    )
    if run_mode:
        stmt = stmt.join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id).where(
            RecommendationRun.run_mode == run_mode
        )
    rows = db.execute(stmt).all()

    # Failure modes by exit_reason
    modes: dict[str, dict[str, Any]] = {}
    for outcome, rec in rows:
        reason = str(outcome.exit_reason or "unknown")
        bucket = modes.setdefault(reason, {"exit_reason": reason, "success": 0, "failed": 0, "total_return": 0.0, "total_mdd": 0.0, "count": 0})
        bucket["count"] += 1
        bucket["total_return"] += float(outcome.executed_return or 0)
        bucket["total_mdd"] += float(outcome.max_drawdown or 0)
        if str(outcome.outcome_label) == "success":
            bucket["success"] += 1
        else:
            bucket["failed"] += 1

    failure_modes = []
    for bucket in sorted(modes.values(), key=lambda b: b["count"], reverse=True):
        n = bucket["count"]
        failure_modes.append({
            "exit_reason": bucket["exit_reason"],
            "count": n,
            "success_count": bucket["success"],
            "failed_count": bucket["failed"],
            "win_rate": round(bucket["success"] / n * 100, 1) if n else 0,
            "avg_return": round(bucket["total_return"] / n, 2) if n else 0,
            "avg_max_drawdown": round(bucket["total_mdd"] / n, 2) if n else 0,
        })

    # Gap-up filter impact
    gap_threshold = 10.0
    gap_total = 0
    gap_filtered = 0
    gap_filtered_failures = 0
    for outcome, rec in rows:
        rec_price = _as_float(rec.recommend_price)
        entry_price = _as_float(outcome.entry_price)
        if rec_price > 0 and entry_price > 0:
            gap = (entry_price - rec_price) / rec_price * 100
            if gap > gap_threshold:
                gap_total += 1
                gap_filtered_failures += 1 if str(outcome.outcome_label) == "failed" else 0
        gap_filtered += 1 if gap > gap_threshold else 0

    # Stop-loss quality: did stop-loss prevent bigger losses?
    sl_trades = [(o, r) for o, r in rows if str(o.exit_reason or "").startswith("stop_loss")]
    sl_would_have_won = sum(1 for o, _ in sl_trades if _as_float(o.close_return) > 0)
    sl_total = len(sl_trades)

    # Industry risk: which industries have systematically poor performance?
    industry_data: dict[str, dict[str, Any]] = {}
    for outcome, rec in rows:
        snapshot = _loads(getattr(rec, "feature_snapshot_json", None), {})
        industry = str(snapshot.get("industry") or "unknown")
        bucket = industry_data.setdefault(industry, {"n": 0, "wins": 0, "total_return": 0.0})
        bucket["n"] += 1
        bucket["total_return"] += float(outcome.executed_return or 0)
        if str(outcome.outcome_label) == "success":
            bucket["wins"] += 1

    industry_risk = []
    for ind, d in sorted(industry_data.items(), key=lambda x: x[1]["n"], reverse=True):
        if d["n"] < 5:
            continue
        win_rate = round(d["wins"] / d["n"] * 100, 1)
        avg_ret = round(d["total_return"] / d["n"], 2)
        industry_risk.append({
            "industry": ind,
            "count": d["n"],
            "win_rate": win_rate,
            "avg_return": avg_ret,
            "risk_level": "high" if win_rate < 50 else ("medium" if win_rate < 58 else "low"),
        })

    return {
        "total_executed": len(rows),
        "failure_modes": failure_modes,
        "gap_up_filter": {
            "threshold_pct": gap_threshold,
            "would_filter": gap_filtered,
            "would_filter_failures": gap_filtered_failures,
            "would_filter_successes": gap_filtered - gap_filtered_failures,
        },
        "stop_loss_quality": {
            "total_stop_loss": sl_total,
            "would_have_won_if_held": sl_would_have_won,
            "stop_loss_saved_ratio": round((sl_total - sl_would_have_won) / sl_total * 100, 1) if sl_total else 0,
        },
        "industry_risk": industry_risk,
    }


_ALPHA_DECAY_FACTORS = [
    "is_main_theme",
    "breakout60",
    "capital_resonance_signal",
    "volume_breakout_signal",
    "breakout20",
    "support_ma5",
    "support_ma10",
    "is_shortline_leader_candidate",
    "is_20cm_stock",
    # 非技术因子（负预测力：均值回归信号）
    "chip_health_score_high",  # chip_health_score >= 72
    "capital_flow_score_high",  # capital_flow_score >= 72
]


def alpha_decay_detection(db: Session, run_mode: str = "formal") -> dict[str, Any]:
    """Alpha 衰减检测：对比近期 vs 早期各因子的 point-biserial 相关系数。

    如果因子的预测力在下降（衰减 > 30%），标记为"衰减中"。
    第一性原理：市场会学习并套利掉公开因子，alpha 必然衰减。
    """
    import math

    stmt = (
        select(Recommendation, RecommendationOutcome)
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .where(
            RecommendationOutcome.entry_triggered == True,  # noqa: E712
            RecommendationOutcome.outcome_label.in_([OUTCOME_SUCCESS, OUTCOME_FAILED]),
        )
        .order_by(Recommendation.trade_date.asc())
    )
    if run_mode:
        stmt = stmt.join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id).where(
            RecommendationRun.run_mode == run_mode
        )
    rows = db.execute(stmt).all()
    if len(rows) < 20:
        return {"factors": [], "error": "insufficient samples", "total": len(rows)}

    mid = len(rows) // 2
    early_rows = rows[:mid]
    recent_rows = rows[mid:]

    def _pbr(binary_vals: list[int], ret_vals: list[float]) -> float:
        g0 = [r for b, r in zip(binary_vals, ret_vals) if b == 0]
        g1 = [r for b, r in zip(binary_vals, ret_vals) if b == 1]
        if len(g0) < 5 or len(g1) < 5:
            return 0.0
        n = len(binary_vals)
        n0, n1 = len(g0), len(g1)
        m0 = sum(g0) / n0
        m1 = sum(g1) / n1
        all_r = ret_vals
        mean = sum(all_r) / n
        ss = sum((v - mean) ** 2 for v in all_r)
        sd = math.sqrt(ss / n) if ss > 0 else 0
        if sd == 0:
            return 0.0
        return (m1 - m0) / sd * math.sqrt(n0 * n1 / (n * n))

    def _extract_factor(row_pairs: list, factor: str) -> tuple[list[int], list[float]]:
        binary, returns = [], []
        for rec, outcome in row_pairs:
            snapshot = _loads(getattr(rec, "feature_snapshot_json", None), {})
            val = snapshot.get(factor)
            if val is not None:
                binary.append(1 if val else 0)
                returns.append(float(getattr(outcome, "executed_return", 0) or 0))
        return binary, returns

    factors_result = []
    for factor in _ALPHA_DECAY_FACTORS:
        eb, er = _extract_factor(early_rows, factor)
        rb, rr = _extract_factor(recent_rows, factor)
        r_early = _pbr(eb, er)
        r_recent = _pbr(rb, rr)
        delta = r_recent - r_early
        decay_pct = (delta / abs(r_early) * 100) if abs(r_early) > 0.001 else 0.0

        # 胜率优势（更稳健的指标，不受极端值影响）
        def _win_edge(binary: list[int], returns: list[float]) -> tuple[float, int, int, float, float]:
            true_wins = sum(1 for b, r in zip(binary, returns) if b == 1 and r > 0)
            true_total = sum(1 for b in binary if b == 1)
            false_wins = sum(1 for b, r in zip(binary, returns) if b == 0 and r > 0)
            false_total = sum(1 for b in binary if b == 0)
            wr_true = true_wins / true_total * 100 if true_total else 0
            wr_false = false_wins / false_total * 100 if false_total else 0
            return wr_true - wr_false, true_total, false_total, wr_true, wr_false

        early_edge, early_bt, early_nobt, early_wr_true, early_wr_false = _win_edge(eb, er)
        recent_edge, recent_bt, recent_nobt, recent_wr_true, recent_wr_false = _win_edge(rb, rr)

        # 综合判断：point-biserial 和胜率优势双重验证
        if abs(r_early) < 0.02 and abs(r_recent) < 0.02:
            status = "noise"
        elif decay_pct < -30 and recent_edge < early_edge - 5:
            # 组合保护：即使 win_edge 衰减，若因子=True 的近期绝对胜率仍 >= 55%
            # （接近整体胜率 ~56%），说明因子单独仍有用，不应直接移除。
            # 参考 breakout20 第 10 轮教训：单独衰减 ≠ 因子无用，组合条件下可能仍有效。
            if recent_wr_true >= 55.0:
                status = "decaying_but_usable"
            else:
                status = "decaying"
        elif decay_pct > 30 and recent_edge > early_edge + 5:
            status = "improving"
        else:
            status = "stable"

        factors_result.append({
            "factor": factor,
            "early_r": round(r_early, 4),
            "recent_r": round(r_recent, 4),
            "delta": round(delta, 4),
            "decay_pct": round(decay_pct, 1),
            "status": status,
            "early_n": len(eb),
            "recent_n": len(rb),
            "early_win_edge": round(early_edge, 1),
            "recent_win_edge": round(recent_edge, 1),
            "early_true_wr": round(early_wr_true, 1),
            "recent_true_wr": round(recent_wr_true, 1),
            "early_false_wr": round(early_wr_false, 1),
            "recent_false_wr": round(recent_wr_false, 1),
            "early_true_n": early_bt,
            "recent_true_n": recent_bt,
        })

    factors_result.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return {
        "total": len(rows),
        "early_period": f"{early_rows[0][0].trade_date} to {early_rows[-1][0].trade_date}",
        "recent_period": f"{recent_rows[0][0].trade_date} to {recent_rows[-1][0].trade_date}",
        "factors": factors_result,
    }


# A 股短线真实交易成本（在 slippage 之外的额外成本）
_COMMISSION_RATE_PCT = 0.05   # 佣金万 2.5 双边
_STAMP_TAX_RATE_PCT = 0.05    # 印花税卖出
_ADDITIONAL_COST_PCT = _COMMISSION_RATE_PCT + _STAMP_TAX_RATE_PCT


def cost_adjusted_metrics(db: Session, run_mode: str = "formal") -> dict[str, Any]:
    """扣除佣金+印花税后的真实表现指标。

    注意：executed_return 已包含 entry/exit slippage（各 10bps），
    此函数额外扣除佣金（万 2.5 双边）和印花税（0.05% 卖出）。
    """
    stmt = select(RecommendationOutcome).where(
        RecommendationOutcome.entry_triggered == True,  # noqa: E712
        RecommendationOutcome.outcome_label.in_([OUTCOME_SUCCESS, OUTCOME_FAILED]),
    )
    if run_mode:
        stmt = stmt.join(Recommendation, Recommendation.recommendation_id == RecommendationOutcome.recommendation_id)
        stmt = stmt.join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id).where(
            RecommendationRun.run_mode == run_mode
        )
    outcomes = db.scalars(stmt).all()
    if not outcomes:
        return {"error": "no executed trades", "count": 0}

    import math

    gross_returns = [float(o.executed_return or 0) for o in outcomes]
    net_returns = [r - _ADDITIONAL_COST_PCT for r in gross_returns]
    n = len(gross_returns)

    gross_wins = sum(1 for r in gross_returns if r > 0)
    net_wins = sum(1 for r in net_returns if r > 0)

    gross_avg = sum(gross_returns) / n
    net_avg = sum(net_returns) / n

    gross_profit = sum(r for r in gross_returns if r > 0)
    gross_loss = abs(sum(r for r in gross_returns if r < 0))
    gross_pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0

    net_profit = sum(r for r in net_returns if r > 0)
    net_loss = abs(sum(r for r in net_returns if r < 0))
    net_pf = round(net_profit / net_loss, 2) if net_loss > 0 else 999.0

    gross_sd = math.sqrt(sum((r - gross_avg) ** 2 for r in gross_returns) / n)
    net_sd = math.sqrt(sum((r - net_avg) ** 2 for r in net_returns) / n)

    return {
        "count": n,
        "additional_cost_pct": _ADDITIONAL_COST_PCT,
        "gross": {
            "win_rate": round(gross_wins / n * 100, 1),
            "avg_return": round(gross_avg, 2),
            "profit_factor": gross_pf,
            "sharpe_per_trade": round(gross_avg / gross_sd, 3) if gross_sd > 0 else 0,
        },
        "net": {
            "win_rate": round(net_wins / n * 100, 1),
            "avg_return": round(net_avg, 2),
            "profit_factor": net_pf,
            "sharpe_per_trade": round(net_avg / net_sd, 3) if net_sd > 0 else 0,
        },
        "cost_drag_pct": round(gross_avg - net_avg, 2),
    }


def filter_effectiveness(
    db: Session,
    run_mode: str = "formal",
) -> dict[str, Any]:
    """三规则过滤效果分析：pos20<50、avg_cost>200、结构性强势×support_ma5。

    返回被过滤 vs 保留交易的 WR/avg/笔数，用于监控过滤策略效果。
    """
    import json as _json

    stmt = (
        select(Recommendation, RecommendationOutcome)
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .where(
            RecommendationOutcome.entry_triggered == True,
            RecommendationOutcome.outcome_label.in_([OUTCOME_SUCCESS, OUTCOME_FAILED]),
        )
    )
    if run_mode:
        stmt = stmt.join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id).where(
            RecommendationRun.run_mode == run_mode
        )
    rows = db.execute(stmt).all()
    if not rows:
        return {"count": 0, "error": "no executed trades"}

    kept = []
    filtered_by_rule: dict[str, list[float]] = {"pos20_low": [], "avg_cost_high": [], "regime_ma5": []}

    for rec, outcome in rows:
        snap = _json.loads(rec.feature_snapshot_json) if rec.feature_snapshot_json else {}
        ret = float(outcome.executed_return or 0)
        pos20 = snap.get("pos20")
        avg_cost = snap.get("avg_cost")
        regime = snap.get("market_regime", "")
        ma5 = snap.get("support_ma5")

        if pos20 is not None and float(pos20) < 50:
            filtered_by_rule["pos20_low"].append(ret)
            continue
        if avg_cost is not None and float(avg_cost) > 200:
            filtered_by_rule["avg_cost_high"].append(ret)
            continue
        if regime == "结构性强势" and ma5:
            filtered_by_rule["regime_ma5"].append(ret)
            continue
        kept.append(ret)

    total = len(kept) + sum(len(v) for v in filtered_by_rule.values())
    n_kept = len(kept)
    net_kept = [r - _ADDITIONAL_COST_PCT for r in kept]
    net_wins_kept = sum(1 for r in net_kept if r > 0)
    net_avg_kept = sum(net_kept) / n_kept if n_kept else 0
    net_profit = sum(r for r in net_kept if r > 0)
    net_loss = abs(sum(r for r in net_kept if r < 0))

    return {
        "count": total,
        "filtered_count": total - n_kept,
        "filter_rate_pct": round((total - n_kept) / total * 100, 1) if total else 0,
        "kept": {
            "count": n_kept,
            "net_win_rate": round(net_wins_kept / n_kept * 100, 1) if n_kept else 0,
            "net_avg_return": round(net_avg_kept, 2),
            "net_profit_factor": round(net_profit / net_loss, 2) if net_loss > 0 else 999.0,
        },
        "filtered_by_rule": {
            name: {
                "count": len(rets),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else 0,
                "avg_return": round(sum(rets) / len(rets), 2) if rets else 0,
            }
            for name, rets in filtered_by_rule.items()
        },
    }


def precision_at_k(
    db: Session,
    run_mode: str = "formal",
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> list[dict[str, Any]]:
    return _cached(f"precision_at_k:{run_mode}:{','.join(str(k) for k in ks)}", lambda: _precision_at_k(db, run_mode, ks))


def _precision_at_k(db: Session, run_mode: str, ks: tuple[int, ...]) -> list[dict[str, Any]]:
    recs = db.execute(
        select(
            Recommendation.run_id,
            Recommendation.recommendation_id,
            Recommendation.final_score,
            RecommendationOutcome.outcome_label,
            RecommendationOutcome.entry_triggered,
            RecommendationOutcome.executed_return,
            RecommendationOutcome.close_return,
        )
        .select_from(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .where(RecommendationRun.run_mode == run_mode)
        .order_by(Recommendation.run_id.asc(), Recommendation.final_score.desc(), Recommendation.recommendation_id.asc())
    ).all()
    by_run: dict[str, list[Any]] = {}
    for row in recs:
        by_run.setdefault(row.run_id, []).append(row)

    result: list[dict[str, Any]] = []
    for k in ks:
        sample_runs = 0
        evaluated_count = 0
        success_count = 0
        for rows in by_run.values():
            top_rows = rows[:k]
            labeled_rows = [row for row in top_rows if normalized_outcome_label(row) != OUTCOME_PENDING]
            if not labeled_rows:
                continue
            sample_runs += 1
            evaluated_count += len(labeled_rows)
            success_count += sum(1 for row in labeled_rows if is_accurate_recommendation(row))
        result.append(
            {
                "k": k,
                "sample_runs": sample_runs,
                "evaluated_count": evaluated_count,
                "success_count": success_count,
                "precision": round(success_count / evaluated_count, 3) if evaluated_count else 0.0,
            }
        )
    return result


def metrics_by_score_line(
    db: Session,
    run_mode: str = "formal",
    score_lines: tuple[int, ...] = (55, 60, 65, 70, 75, 80),
) -> list[dict[str, Any]]:
    key = f"by_score_line:{run_mode}:{','.join(str(line) for line in score_lines)}"
    return _cached(key, lambda: _metrics_by_score_line(db, run_mode, score_lines))


def _metrics_by_score_line(db: Session, run_mode: str, score_lines: tuple[int, ...]) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, run_mode)
    if not rows:
        return []
    benchmark_by_rec = _benchmark_returns_by_recommendation(db, rows)
    horizon_records = _horizon_metric_records_by_recommendation(db, rows)
    result: list[dict[str, Any]] = []
    total_count = len(rows)
    for score_line in score_lines:
        selected = [row for row in rows if float(row.final_score or 0) >= score_line]
        metrics = _compute_full_metrics(_metric_records_from_rows(selected, benchmark_by_rec))
        result.append(
            {
                "score_line": float(score_line),
                "sample_count": len(selected),
                "activation_rate": round(len(selected) / total_count, 3) if total_count else 0.0,
                "labeled_count": metrics["trade_count"],
                "triggered_count": metrics["executed_trade_count"],
                "missed_count": metrics["unexecuted_count"],
                "success_count": metrics["success_count"],
                "trigger_rate": metrics["trigger_rate"],
                "hit_rate": metrics["trade_accuracy"],
                "avg_return": metrics["average_executed_return"],
                "avg_drawdown": metrics["avg_max_drawdown"],
                "avg_close_return": metrics["avg_close_return"],
                "horizon_metrics": _horizon_metrics_for_rows(selected, horizon_records),
                **_group_metric_fields(metrics),
            }
        )
    return result


def _metric_records_from_rows(
    rows: list[Any],
    benchmark_by_recommendation: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    benchmark_by_recommendation = benchmark_by_recommendation or {}
    return [
        _metric_record_from_row(
            row,
            benchmark_by_recommendation.get(str(getattr(row, "recommendation_id", "") or ""), 0.0),
        )
        for row in rows
        if normalized_outcome_label(row) != OUTCOME_PENDING
    ]


def _metric_record_from_row(row: Any, benchmark_return_pct: float = 0.0) -> dict[str, Any]:
    executed = is_trade_executed(row)
    executed_return = resolved_trade_return(row) if executed else 0.0
    accurate = bool(executed and executed_return > 0)
    false_positive_reason = getattr(row, "outcome_reason", None) if not accurate else None
    if false_positive_reason is None and not executed:
        false_positive_reason = "未触发买点或不可真实成交"
    return {
        "net_return_pct": float(executed_return),
        "gross_return_pct": float(getattr(row, "close_return", 0) or 0),
        "achievable_return_pct": float(getattr(row, "achievable_return", 0) or 0),
        "morning_drawdown_pct": float(getattr(row, "max_drawdown", 0) or 0),
        "holding_days": float(getattr(row, "holding_days", 0 if not executed else 1) or 0),
        "benchmark_return_pct": float(benchmark_return_pct or 0.0),
        "accurate": accurate,
        "executed": executed,
        "trade_date": str(getattr(row, "trade_date", "")),
        "code": getattr(row, "code", ""),
        "name": getattr(row, "name", ""),
        "false_positive_reason": false_positive_reason,
        "resolved": True,
    }


def _horizon_metric_records_by_recommendation(db: Session, rows: list[Any]) -> dict[str, dict[int, dict[str, Any]]]:
    labeled_rows = [
        row
        for row in rows
        if normalized_outcome_label(row) != OUTCOME_PENDING
    ]
    if not labeled_rows:
        return {}
    if not all(hasattr(row, "recommend_price") and hasattr(row, "feature_snapshot_json") for row in labeled_rows):
        return {}

    codes = sorted({str(getattr(row, "code", "")) for row in labeled_rows if getattr(row, "code", None)})
    trade_dates = [str(getattr(row, "trade_date", "")) for row in labeled_rows if getattr(row, "trade_date", None)]
    if not codes or not trade_dates:
        return {}

    bars = db.scalars(
        select(DailyKbar)
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date >= min(trade_dates))
        .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
    ).all()

    bars_by_code: dict[str, list[DailyKbar]] = {}
    dates_by_code: dict[str, list[str]] = {}
    for bar in bars:
        code = str(bar.code)
        bars_by_code.setdefault(code, []).append(bar)
        dates_by_code.setdefault(code, []).append(str(bar.trade_date))

    records: dict[str, dict[int, dict[str, Any]]] = {}
    for row in labeled_rows:
        rec_id = str(getattr(row, "recommendation_id", "") or "")
        code = str(getattr(row, "code", "") or "")
        if not rec_id or code not in bars_by_code:
            continue
        for horizon_days in BREAKDOWN_HORIZON_DAYS:
            simulation = _simulate_outcome(
                row,
                bars_by_code.get(code, []),
                dates_by_code.get(code, []),
                horizon_days=horizon_days,
            )
            if simulation is None:
                continue
            records.setdefault(rec_id, {})[horizon_days] = _metric_record_from_simulation(row, simulation)
    return records


def _metric_record_from_simulation(row: Any, simulation: dict[str, Any]) -> dict[str, Any]:
    executed = bool(simulation.get("entry_triggered"))
    executed_return = float(simulation.get("executed_return", 0.0) or 0.0) if executed else 0.0
    accurate = bool(executed and executed_return > 0)
    false_positive_reason = None if accurate else simulation.get("outcome_reason")
    if false_positive_reason is None and not executed:
        false_positive_reason = "未触发买点或不可真实成交"
    return {
        "net_return_pct": executed_return,
        "gross_return_pct": float(simulation.get("close_return", 0.0) or 0.0),
        "achievable_return_pct": float(simulation.get("achievable_return", 0.0) or 0.0),
        "morning_drawdown_pct": float(simulation.get("max_drawdown", 0.0) or 0.0),
        "holding_days": float(simulation.get("holding_days", 0 if not executed else 1) or 0),
        "benchmark_return_pct": 0.0,
        "accurate": accurate,
        "executed": executed,
        "trade_date": str(getattr(row, "trade_date", "")),
        "code": getattr(row, "code", ""),
        "name": getattr(row, "name", ""),
        "false_positive_reason": false_positive_reason,
        "resolved": True,
    }


def _horizon_metrics_for_rows(
    rows: list[Any],
    horizon_records: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for horizon_days in BREAKDOWN_HORIZON_DAYS:
        records = [
            record
            for row in rows
            for record in [horizon_records.get(str(getattr(row, "recommendation_id", "") or ""), {}).get(horizon_days)]
            if record is not None
        ]
        metrics = _compute_full_metrics(records)
        result[str(horizon_days)] = {
            "horizon_days": horizon_days,
            "sample_count": metrics["trade_count"],
            "triggered_count": metrics["executed_trade_count"],
            "success_count": metrics["success_count"],
            "recommendation_accuracy": metrics["recommendation_accuracy"],
            "hit_rate": metrics["trade_accuracy"],
            "average_net_return": metrics["average_net_return"],
            "average_executed_return": metrics["average_executed_return"],
            "max_drawdown": metrics["max_drawdown"],
            "sample_confidence": metrics["sample_confidence"],
            "accuracy_numerator": metrics["accuracy_numerator"],
            "accuracy_denominator": metrics["accuracy_denominator"],
        }
    return result


def _benchmark_returns_by_recommendation(db: Session, rows: list[Any]) -> dict[str, float]:
    all_dates: set[str] = set()
    executable_rows = []
    for row in rows:
        if normalized_outcome_label(row) == OUTCOME_PENDING or not is_trade_executed(row):
            continue
        entry_date = str(getattr(row, "entry_date", "") or getattr(row, "trade_date", ""))
        exit_date = str(getattr(row, "exit_date", "") or getattr(row, "trade_date", ""))
        all_dates.add(entry_date)
        all_dates.add(exit_date)
        executable_rows.append((row, entry_date, exit_date))
    if not executable_rows:
        return {}

    from app.services.benchmark_service import compute_benchmark_return as _bench_return
    from app.services.benchmark_service import get_benchmark_close_map as _get_close_map

    close_map = _get_close_map(db, list(all_dates))
    return {
        str(getattr(row, "recommendation_id", "") or ""): _bench_return(close_map, entry_date, exit_date)
        for row, entry_date, exit_date in executable_rows
    }


def _group_metric_fields(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": metrics["trade_count"],
        "recommendation_accuracy": metrics["recommendation_accuracy"],
        "average_net_return": metrics["average_net_return"],
        "median_net_return": metrics["median_net_return"],
        "profit_loss_ratio": metrics["profit_loss_ratio"],
        "max_drawdown": metrics["max_drawdown"],
        "positive_month_ratio": metrics["positive_month_ratio"],
        "benchmark_excess_return": metrics["benchmark_excess_return"],
        "sample_confidence": metrics["sample_confidence"],
        "accuracy_numerator": metrics["accuracy_numerator"],
        "accuracy_denominator": metrics["accuracy_denominator"],
        "accuracy_basis": metrics["accuracy_basis"],
    }


def _scoped_recommendation_rows(db: Session, run_mode: str) -> list[Any]:
    rows = list(
        db.execute(
            select(
                Recommendation.recommendation_id,
                Recommendation.run_id,
                Recommendation.code,
                Recommendation.name,
                Recommendation.recommend_price,
                Recommendation.final_score,
                Recommendation.setup_type,
                Recommendation.feature_snapshot_json,
                Recommendation.skill_breakdown_json,
                Recommendation.trade_date,
                Recommendation.market_context_json,
                RecommendationOutcome.outcome_label,
                RecommendationOutcome.entry_triggered,
                RecommendationOutcome.executed_return,
                RecommendationOutcome.weighted_return,
                RecommendationOutcome.close_return,
                RecommendationOutcome.achievable_return,
                RecommendationOutcome.max_return,
                RecommendationOutcome.min_return,
                RecommendationOutcome.max_drawdown,
                RecommendationOutcome.holding_days,
                RecommendationOutcome.outcome_reason,
                RecommendationOutcome.entry_date,
                RecommendationOutcome.exit_date,
            )
            .select_from(Recommendation)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .outerjoin(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .where(RecommendationRun.run_mode == run_mode)
            .order_by(Recommendation.trade_date.asc(), Recommendation.final_score.desc(), Recommendation.recommendation_id.asc())
        ).all()
    )
    # 按 (trade_date, code) 去重，保留 final_score 最高的行
    # 原因：回测 DB 累积多个 bt_ run 产生同一股票同日的重复记录
    seen: set[tuple] = set()
    deduped: list[Any] = []
    for row in rows:
        key = (row[9], row[2])  # (trade_date, code)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _simulate_outcome(
    rec: Recommendation,
    bars: list[DailyKbar],
    dates: list[str],
    horizon_days: int,
) -> dict[str, Any] | None:
    if not bars:
        return None

    signal_index = bisect_left(dates, str(rec.trade_date))
    signal_bar = bars[signal_index] if signal_index < len(bars) and dates[signal_index] == str(rec.trade_date) else None
    future_start = signal_index + 1 if signal_bar is not None else signal_index
    future_bars = bars[future_start:]
    if not future_bars:
        return None

    feature_snapshot = _loads(getattr(rec, "feature_snapshot_json", None), {})
    static_metrics = _static_window_metrics(rec, signal_bar, future_bars, horizon_days)

    recommendation_block_reason = _recommendation_execution_block_reason(feature_snapshot)
    if recommendation_block_reason:
        return {
            **static_metrics,
            "entry_triggered": False,
            "entry_date": None,
            "entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "executed_return": 0.0,
            "weighted_return": 0.0,
            "position_size": _resolve_position_size(feature_snapshot),
            "holding_days": 0,
            "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
            "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
            "exit_reason": "recommendation_execution_blocked",
            "hit_target": False,
            "hit_invalid": False,
            "outcome_label": OUTCOME_MISSED,
            "outcome_label_legacy": OUTCOME_MISSED,
            "outcome_reason": f"{recommendation_block_reason}；推荐时已判定不可真实成交，静态窗口结果仅作旁路参考",
        }

    entry_result = _resolve_entry(rec, feature_snapshot, future_bars, horizon_days)
    position_size = _resolve_position_size(feature_snapshot)

    if not entry_result.get("triggered"):
        return {
            **static_metrics,
            "entry_triggered": False,
            "entry_date": None,
            "entry_price": None,
            "exit_date": None,
            "exit_price": None,
            "executed_return": 0.0,
            "weighted_return": 0.0,
            "position_size": position_size,
            "holding_days": 0,
            "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
            "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
            "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
            "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
            "exit_reason": "not_triggered",
            "hit_target": False,
            "hit_invalid": False,
            "outcome_label": OUTCOME_MISSED,
            "outcome_label_legacy": OUTCOME_MISSED,
            "outcome_reason": f"{entry_result.get('reason') or '未触发买点，未建仓'}；静态窗口结果仅作旁路参考",
        }

    entry_index = int(entry_result["bar_index"])
    raw_entry_price = float(entry_result["price"])
    entry_date = str(entry_result["date"])

    # 跳空高开过滤：gap > MAX_ENTRY_GAP_PCT 则放弃入场
    # 历史数据表明 gap > 10% 的交易胜率为 0%（12/12 全部失败，平均亏损 -4.5%）
    recommend_price = _as_float(rec.recommend_price) or 0
    if recommend_price > 0 and raw_entry_price > 0:
        gap_pct = (raw_entry_price - recommend_price) / recommend_price * 100
        if gap_pct > MAX_ENTRY_GAP_PCT:
            return {
                **static_metrics,
                "entry_triggered": False,
                "entry_date": None,
                "entry_price": None,
                "exit_date": None,
                "exit_price": None,
                "executed_return": 0.0,
                "weighted_return": 0.0,
                "position_size": _resolve_position_size(feature_snapshot),
                "holding_days": 0,
                "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
                "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
                "stop_loss_price": _resolve_stop_loss_price(None, feature_snapshot),
                "take_profit_price": _resolve_take_profit_price(None, feature_snapshot),
                "exit_reason": "gap_up_blocked",
                "hit_target": False,
                "hit_invalid": False,
                "outcome_label": OUTCOME_MISSED,
                "outcome_label_legacy": OUTCOME_MISSED,
                "outcome_reason": f"跳空高开 {gap_pct:.1f}% > {MAX_ENTRY_GAP_PCT:.0f}% 阈值，放弃入场",
            }

    executed_entry_price = round(raw_entry_price * (1 + ENTRY_SLIPPAGE_BPS / 10000.0), 4)
    stop_loss_price = _resolve_stop_loss_price(raw_entry_price, feature_snapshot)
    take_profit_price = _resolve_take_profit_price(raw_entry_price, feature_snapshot)

    holding_bars = future_bars[entry_index : entry_index + max(1, horizon_days)]
    if not holding_bars:
        return None

    exit_price, exit_date, exit_reason = _resolve_exit(
        holding_bars,
        stop_loss_price,
        take_profit_price,
        code=str(rec.code),
        entry_price=executed_entry_price,
    )
    executed_exit_price = round(exit_price * (1 - EXIT_SLIPPAGE_BPS / 10000.0), 4)
    executed_return = round(((executed_exit_price - executed_entry_price) / executed_entry_price) * 100, 2)

    # 除权/复权检测：当价格跳变 > 20% 但持有期每日 pct_chg 均在正常范围时，
    # 视为除权事件，使用 pct_chg 链重新计算收益。
    if abs(executed_return) > 20:
        is_star = str(rec.code or "").startswith("68")
        limit_pct = 20 if is_star else 10
        max_daily_pct = max(abs(_as_float(bar.pct_chg)) for bar in holding_bars) if holding_bars else 0
        if max_daily_pct < limit_pct * 0.6:  # 无单日接近涨跌停 → 除权
            cum = 1.0
            for bar in holding_bars:
                cum *= (1 + _as_float(bar.pct_chg) / 100.0)
            executed_return = round((cum - 1) * 100, 2)
            executed_exit_price = round(executed_entry_price * (1 + executed_return / 100.0), 4)

    weighted_return = round(executed_return * position_size, 2)
    hit_invalid = exit_reason.startswith("stop_loss")
    hit_target = exit_reason.startswith("take_profit") or executed_return >= SUCCESS_RETURN_PCT
    # outcome_label 与 accuracy policy 对齐：扣费滑点后净收益 > 0 即为准确推荐
    # SUCCESS_RETURN_PCT (3%) 是"优质推荐"阈值，不作为 outcome 硬门槛
    # executed_return 已含滑点但未扣佣金+印花税（_ADDITIONAL_COST_PCT=0.10%），
    # 阈值必须覆盖这部分成本，否则 0~0.10% 的微利交易被标 success 但实际亏损
    outcome_label = OUTCOME_SUCCESS if (executed_return > _ADDITIONAL_COST_PCT and not hit_invalid) else OUTCOME_FAILED

    return {
        **static_metrics,
        "entry_triggered": True,
        "entry_date": entry_date,
        "entry_price": round(executed_entry_price, 2),
        "exit_date": exit_date,
        "exit_price": round(executed_exit_price, 2),
        "executed_return": executed_return,
        "weighted_return": weighted_return,
        "position_size": position_size,
        "holding_days": len(holding_bars),
        "entry_slippage_bps": ENTRY_SLIPPAGE_BPS,
        "exit_slippage_bps": EXIT_SLIPPAGE_BPS,
        "stop_loss_price": round(stop_loss_price, 2),
        "take_profit_price": round(take_profit_price, 2),
        "exit_reason": exit_reason,
        "hit_target": hit_target,
        "hit_invalid": hit_invalid,
        "outcome_label": outcome_label,
        "outcome_reason": _build_outcome_reason(
            exit_reason, executed_return, static_metrics["close_return"],
            max_return=static_metrics.get("max_return"), max_drawdown=static_metrics.get("max_drawdown"),
        ),
    }


def _static_window_metrics(
    rec: Recommendation,
    signal_bar: DailyKbar | None,
    future_bars: list[DailyKbar],
    horizon_days: int,
) -> dict[str, Any]:
    window = future_bars[: max(1, horizon_days)]
    if not window:
        return {
            "max_return": 0.0,
            "min_return": 0.0,
            "close_return": 0.0,
            "achievable_return": 0.0,
            "max_drawdown": 0.0,
            "outcome_label_legacy": OUTCOME_MISSED,
        }
    baseline_entry = _as_float(rec.recommend_price) or _as_float(signal_bar.close if signal_bar else None) or _as_float(window[0].open) or _as_float(window[0].close)
    if baseline_entry <= 0:
        baseline_entry = max(0.01, _as_float(window[0].close))
    returns = [((_as_float(bar.high) - baseline_entry) / baseline_entry) * 100 for bar in window]
    lows = [((_as_float(bar.low) - baseline_entry) / baseline_entry) * 100 for bar in window]
    close_return = ((_as_float(window[-1].close) - baseline_entry) / baseline_entry) * 100
    max_return = max(returns) if returns else 0.0
    min_return = min(lows) if lows else 0.0
    achievable_return = (close_return + min(max_return, close_return * 1.5)) / 2.0
    legacy_label = OUTCOME_SUCCESS if (max_return >= 5 and min_return > -4) else OUTCOME_FAILED
    return {
        "max_return": round(max_return, 2),
        "min_return": round(min_return, 2),
        "close_return": round(close_return, 2),
        "achievable_return": round(achievable_return, 2),
        "max_drawdown": round(min_return, 2),
        "outcome_label_legacy": legacy_label,
    }


def _resolve_entry(
    rec: Recommendation,
    feature_snapshot: dict[str, Any],
    future_bars: list[DailyKbar],
    horizon_days: int,
) -> dict[str, Any]:
    entry_targets = [
        value for value in (
            _as_float(feature_snapshot.get("ideal_buy")),
            _as_float(feature_snapshot.get("secondary_buy")),
        )
        if value > 0
    ]
    entry_window = future_bars[: max(1, min(ENTRY_LOOKAHEAD_DAYS, horizon_days, len(future_bars)))]
    blocked_reasons: list[str] = []
    for bar_index, bar in enumerate(entry_window):
        low = _as_float(bar.low)
        bar_open = _as_float(bar.open)
        for target in entry_targets:
            if low <= target:
                block_reason = _entry_execution_block_reason(str(rec.code), bar)
                if block_reason:
                    blocked_reasons.append(block_reason)
                    continue
                return {
                    "triggered": True,
                    "bar_index": bar_index,
                    "price": min(bar_open, target) if bar_open > 0 else target,
                    "date": str(bar.trade_date),
                    "reason": "buy_target_touched",
                }

    if entry_targets:
        # 买点未在窗口内命中 → 回退到次日开盘市价入场，而不是直接放弃
        for bar_index, bar in enumerate(entry_window):
            block_reason = _entry_execution_block_reason(str(rec.code), bar)
            if block_reason:
                blocked_reasons.append(block_reason)
                continue
            raw_entry_price = _as_float(bar.open) or _as_float(bar.close)
            if raw_entry_price <= 0:
                blocked_reasons.append("入场日价格无效，无法真实成交")
                continue
            return {
                "triggered": True,
                "bar_index": bar_index,
                "price": raw_entry_price,
                "date": str(bar.trade_date),
                "reason": "next_open_fallback_entry（买点未命中，市价入场）",
            }
        return {
            "triggered": False,
            "reason": _entry_not_triggered_reason(blocked_reasons, has_targets=True),
        }

    for bar_index, bar in enumerate(entry_window):
        block_reason = _entry_execution_block_reason(str(rec.code), bar)
        if block_reason:
            blocked_reasons.append(block_reason)
            continue
        raw_entry_price = _as_float(bar.open) or _as_float(bar.close)
        if raw_entry_price <= 0:
            blocked_reasons.append("入场日价格无效，无法真实成交")
            continue
        return {
            "triggered": True,
            "bar_index": bar_index,
            "price": raw_entry_price,
            "date": str(bar.trade_date),
            "reason": "next_open_entry",
        }
    return {
        "triggered": False,
        "reason": _entry_not_triggered_reason(blocked_reasons, has_targets=False),
    }


def _resolve_exit(
    holding_bars: list[DailyKbar],
    stop_loss_price: float,
    take_profit_price: float,
    *,
    code: str = "",
    entry_price: float = 0.0,
) -> tuple[float, str, str]:
    """模拟退出逻辑：固定止损/止盈优先。

    日线级别无法确定 bar 内 open/high/low 的时间顺序，
    移动止盈会产生大量假信号（回测验证：56 笔改善 vs 116 笔恶化，净效果 -286%）。
    """
    locked_limit_down_bars = 0
    for bar in holding_bars:
        if _exit_execution_block_reason(code, bar):
            locked_limit_down_bars += 1
            continue
        bar_open = _as_float(bar.open)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        if bar_open > 0 and low > 0:
            if bar_open <= stop_loss_price:
                # 跳空止损：open 已远低于止损价，cap 最大亏损防止极端值（如连续跌停）
                if entry_price > 0:
                    min_exit_price = entry_price * (1 + STOP_LOSS_GAP_CAP_PCT / 100.0)
                    gap_exit = max(bar_open, min_exit_price)
                else:
                    gap_exit = bar_open
                return gap_exit, str(bar.trade_date), "stop_loss_gap"
            if bar_open >= take_profit_price:
                return bar_open, str(bar.trade_date), "take_profit_gap"
        if low <= stop_loss_price and high >= take_profit_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_intraday_both_hit"
        if low <= stop_loss_price:
            return stop_loss_price, str(bar.trade_date), "stop_loss_hit"
        if high >= take_profit_price:
            return take_profit_price, str(bar.trade_date), "take_profit_hit"

    last_bar = holding_bars[-1]
    if locked_limit_down_bars and locked_limit_down_bars == len(holding_bars):
        return _as_float(last_bar.close), str(last_bar.trade_date), "limit_down_locked_horizon_mark"
    if locked_limit_down_bars:
        return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close_after_locked_limit_down"
    return _as_float(last_bar.close), str(last_bar.trade_date), "horizon_close"


def _resolve_stop_loss_price(raw_entry_price: float | None, feature_snapshot: dict[str, Any]) -> float:
    """解析止损价，加入归一化逻辑防止过紧的止损。

    止损价必须在 [min_stop, max_stop] 范围内：
    - max_stop = entry * (1 + DEFAULT_STOP_LOSS_PCT/100) = 最紧 -6.5%
    - min_stop = entry * (1 + MAX_STOP_LOSS_PCT/100) = 最宽 -9%
    """
    raw_stop = _as_float(feature_snapshot.get("stop_loss"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_stop, 2) if raw_stop > 0 else 0.0
    max_stop = raw_entry_price * (1 + DEFAULT_STOP_LOSS_PCT / 100.0)
    if raw_stop <= 0 or raw_stop >= raw_entry_price:
        return round(max_stop, 2)
    min_stop = raw_entry_price * (1 + MAX_STOP_LOSS_PCT / 100.0)
    normalized = min(raw_stop, max_stop)   # 不紧于 -6.5%
    normalized = max(normalized, min_stop) # 不宽于 -9%
    return round(normalized, 2)


def _resolve_take_profit_price(raw_entry_price: float | None, feature_snapshot: dict[str, Any]) -> float:
    """解析止盈价，加入上限归一化防止过高的止盈目标。"""
    raw_target = _as_float(feature_snapshot.get("take_profit"))
    if raw_entry_price is None or raw_entry_price <= 0:
        return round(raw_target, 2) if raw_target > 0 else 0.0
    fallback = raw_entry_price * (1 + DEFAULT_TAKE_PROFIT_PCT / 100.0)
    if raw_target <= raw_entry_price:
        return round(fallback, 2)
    max_tp = raw_entry_price * (1 + MAX_TAKE_PROFIT_PCT / 100.0)
    normalized = min(raw_target, max_tp)
    return round(normalized, 2)


def _resolve_position_size(feature_snapshot: dict[str, Any]) -> float:
    discipline = feature_snapshot.get("market_discipline")
    if isinstance(discipline, dict):
        max_position_pct = _as_float(discipline.get("max_position_pct"))
        if max_position_pct > 0:
            return round(min(max_position_pct / 100.0, 1.0), 4)
        label = str(discipline.get("position_label") or "")
    else:
        max_position_pct = _as_float(feature_snapshot.get("max_position_pct"))
        if max_position_pct > 0:
            return round(min(max_position_pct / 100.0, 1.0), 4)
        label = str(feature_snapshot.get("position_label") or "")
    if "大仓" in label:
        return 0.75
    if "中仓" in label:
        return 0.55
    if "小仓" in label:
        return 0.35
    if "观察" in label:
        return 0.15
    return 1.0


def _entry_execution_block_reason(code: str, bar: DailyKbar) -> str | None:
    return entry_execution_block_reason_from_bar(code, bar)


def _exit_execution_block_reason(code: str, bar: DailyKbar) -> str | None:
    return exit_execution_block_reason_from_bar(code, bar)


def _recommendation_execution_block_reason(feature_snapshot: dict[str, Any]) -> str | None:
    execution_viability = feature_snapshot.get("execution_viability")
    if not isinstance(execution_viability, dict):
        return None
    if str(execution_viability.get("status") or "").lower() != "blocked":
        return None
    return str(
        execution_viability.get("block_reason")
        or execution_viability.get("label")
        or "推荐时执行可成交性判定为不可成交"
    )


def _entry_not_triggered_reason(blocked_reasons: list[str], *, has_targets: bool) -> str:
    if blocked_reasons:
        unique = list(dict.fromkeys(blocked_reasons))
        return "；".join(unique[:2])
    return "未触发目标买点，未建仓" if has_targets else "观察窗口内无可执行入场日"


def _has_no_liquidity(bar: DailyKbar) -> bool:
    return no_liquidity_reason_from_bar(bar, side="entry") is not None


def _is_one_word_limit_up(code: str, bar: DailyKbar) -> bool:
    return is_one_word_limit_up_bar(code, bar)


def _is_one_word_limit_down(code: str, bar: DailyKbar) -> bool:
    return is_one_word_limit_down_bar(code, bar)


def _build_outcome_reason(
    exit_reason: str,
    executed_return: float,
    close_return: float,
    *,
    max_return: float | None = None,
    max_drawdown: float | None = None,
) -> str:
    label = {
        "stop_loss_gap": "开盘跳空止损",
        "take_profit_gap": "开盘跳空止盈",
        "stop_loss_intraday_both_hit": "同日同时触发止盈止损，按保守止损处理",
        "stop_loss_hit": "盘中触发止损",
        "take_profit_hit": "盘中触发止盈",
        "horizon_close": "持有到观察期结束",
        "limit_down_locked_horizon_mark": "观察期内一字跌停或无流动性，按期末价格保守标记",
        "horizon_close_after_locked_limit_down": "出现一字跌停或无流动性后递延至观察期末",
    }.get(exit_reason, exit_reason)
    parts = [label, f"执行收益 {executed_return:.2f}%", f"静态收盘收益 {close_return:.2f}%"]
    if max_return is not None and max_return > 3:
        parts.append(f"持有期最高浮盈 {max_return:.1f}%")
    if max_drawdown is not None and max_drawdown < -3:
        parts.append(f"最大回撤 {max_drawdown:.1f}%")
    return "；".join(parts)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return {} if default is None else default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {} if default is None else default

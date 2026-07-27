import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.engine.trading_costs import (
    ADDITIONAL_COST_PCT as _ADDITIONAL_COST_PCT,
    COMMISSION_RATE_PCT as _COMMISSION_RATE_PCT,
    STAMP_TAX_RATE_PCT as _STAMP_TAX_RATE_PCT,
    is_net_profitable,
)

from app.models.tables import DailyKbar, Recommendation, RecommendationOutcome, RecommendationRun, SkillRegistry
from app.services.evaluation_metrics import compute_full_metrics as _compute_full_metrics

# ── 结果模拟引擎：权威实现已收口到 outcome_simulation.py（任务 6.1），
# 以下为兼容 re-export，保持 `from app.services.outcome_service import _simulate_outcome`
# 等既有导入路径不失效（见 stock_backtest_service.py / scripts/exit_rules.py /
# scripts/failure_attribution.py / scripts/verify_trailing_stop_params.py /
# tests/test_failure_attribution.py；对模拟引擎本身的单测已迁移至
# tests/test_outcome_simulation.py，直接 import 权威实现，不留双份覆盖）。
from app.services.outcome_simulation import (  # noqa: F401 - re-export for backward compatibility
    DEFAULT_STOP_LOSS_PCT,
    DEFAULT_TAKE_PROFIT_PCT,
    ENTRY_LOOKAHEAD_DAYS,
    ENTRY_SLIPPAGE_BPS,
    EXECUTED_OUTCOME_LABELS,
    EXIT_SLIPPAGE_BPS,
    MAX_ENTRY_GAP_PCT,
    MAX_STOP_LOSS_PCT,
    MAX_TAKE_PROFIT_PCT,
    OUTCOME_FAILED,
    OUTCOME_MISSED,
    OUTCOME_PENDING,
    OUTCOME_PROVISIONAL,
    OUTCOME_SUCCESS,
    STOP_LOSS_GAP_CAP_PCT,
    SUCCESS_RETURN_PCT,
    TRAILING_STOP_ACTIVATE_PCT,
    TRAILING_STOP_CALLBACK_PCT,
    TRAILING_STOP_MIN_LOCK_PCT,
    TRAILING_STOP_PROFIT_LOCK_RATIO,
    _as_float,
    _build_outcome_reason,
    _entry_execution_block_reason,
    _entry_not_triggered_reason,
    _exit_execution_block_reason,
    _has_no_liquidity,
    _is_one_word_limit_down,
    _loads,
    _recommendation_execution_block_reason,
    _resolve_entry,
    _resolve_exit,
    _resolve_exit_base,
    _resolve_exit_with_trailing,
    _resolve_position_size,
    _resolve_stop_loss_price,
    _resolve_take_profit_price,
    _simulate_outcome,
    _static_window_metrics,
)

BREAKDOWN_HORIZON_DAYS = (3, 5, 10)

# outcome 结算滞后天数：每日推荐需等 K 线走完 horizon（盘后 cron 默认 horizon_days=5）才会被
# label_pending_outcomes 标注为 success/failed/missed。滚动窗口模式下自动把
# end_date 截到 today - SETTLE_LAG_DAYS，保证窗口内推荐均已结算，避免 pending
# 污染样本（_metric_records_from_rows 虽会跳过 pending，但样本数会偏少且不可解释）。
# 5 天 horizon + 2 天缓冲（应对偶发结算延迟），过大会导致近期覆盖度补跑样本被滚动窗口截掉
# （曾出现 window_days=30 时有效区间被截到仅 10 天，最近补跑的策略样本全部消失）。
SETTLE_LAG_DAYS = 7


def _resolve_window(
    window_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str | None, str | None]:
    """将 window_days / start_date / end_date 解析为 (start, end) ISO 日期字符串。

    优先级：window_days > 显式 start_date/end_date。
    window_days=N 时：start = today - N，end = today - SETTLE_LAG_DAYS（已结算截止日）。
    显式模式：end_date 不强制截断（调用方自行决定是否含未结算区间）。
    """
    if window_days is not None and window_days > 0:
        today = date.today()
        start = (today - timedelta(days=window_days)).isoformat()
        end = (today - timedelta(days=SETTLE_LAG_DAYS)).isoformat()
        return start, end
    return start_date, end_date


def _window_meta(
    window_days: int | None,
    start_date: str | None,
    end_date: str | None,
    total_count: int,
    labeled_count: int,
) -> dict[str, Any]:
    """构建响应里的 date_range 元信息，供前端展示时间窗口与结算截止说明。"""
    return {
        "window_days": window_days,
        "start_date": start_date,
        "end_date": end_date,
        "settle_lag_days": SETTLE_LAG_DAYS,
        "settle_cutoff": (date.today() - timedelta(days=SETTLE_LAG_DAYS)).isoformat() if window_days else None,
        "total_in_window": total_count,
        "labeled_in_window": labeled_count,
        "pending_excluded": max(0, total_count - labeled_count),
    }

# 短时缓存
_metrics_cache: dict[str, tuple[float, Any]] = {}
_metrics_cache_lock = threading.Lock()
_metrics_compute_locks: dict[str, threading.Lock] = {}
_metrics_compute_locks_lock = threading.Lock()
_METRICS_CACHE_TTL = 30.0  # 指标缓存 30 秒（原 5 秒过短，冷启动 350ms 频繁触发）

# outcome 结算进程内串行锁：三个入口（startup 补结算线程 / 首屏预热 _auto_backfill_outcomes
# / cron 18:20 _run_outcome_label）都直接调 label_pending_outcomes，用各自 SessionLocal。
# 若不串行，两个 session 在各自事务隔离下都把同一批 pending rec 当"未结算"捞出（看不到对方
# 未提交的 INSERT），第二个 db.commit() 的 executemany 撞 PRIMARY KEY 1062 → 整批回滚、0 笔结算
# （app.log 2026-06-23 样本已实证 rec_20260623_001_0fcd 重复 INSERT 致 startup 补结算静默失败，
# 7/9 的 28 条前瞻样本因此长期 0 结算）。串行后第二个调用开始时第一个已 commit，db.get 能看到
# 已存在的 outcome → update 而非 INSERT，根因消除。D 项前瞻验证前置基础设施可靠性修复。
_label_outcome_lock = threading.Lock()


def _cached(key: str, factory):
    """带 per-key 计算锁的缓存，防止缓存击穿（多请求同时计算同一 key）。

    MetricsPage 并发发起 5 个请求，如果缓存未命中，不加锁会同时计算
    _compute_horizon_metric_records（705 次模拟），造成 CPU 浪费和延迟叠加。
    加 per-key 锁后，同一 key 只有一个请求计算，其它等待结果。
    """
    now = time.time()
    with _metrics_cache_lock:
        entry = _metrics_cache.get(key)
        if entry is not None and (now - entry[0]) < _METRICS_CACHE_TTL:
            return entry[1]

    # 获取 per-key 计算锁（防止多请求同时计算同一 key）
    with _metrics_compute_locks_lock:
        lock = _metrics_compute_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _metrics_compute_locks[key] = lock

    with lock:
        # double-check：持有锁后再次检查缓存（可能已被其它请求计算）
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
    if raw_label == OUTCOME_PROVISIONAL:
        # 临时态（数据仅至入场日）保持自身，不 fall through 到 failed；
        # rec.status 与所有走 normalized 口径的统计据此排除中间态
        return OUTCOME_PROVISIONAL
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

    executed_return 已含滑点但未扣佣金+印花税（_ADDITIONAL_COST_PCT，佣金万1双边+印花税0.05%卖出），
    阈值必须覆盖这部分成本，与 outcome_label 判定保持一致。
    """
    return bool(is_trade_executed(outcome) and is_net_profitable(resolved_trade_return(outcome)))


def _apply_outcome_simulation(
    db: Session,
    rec: Recommendation,
    simulation: dict[str, Any],
    horizon_days: int,
    label_source: str = "auto",
) -> bool:
    """把 _simulate_outcome 结果写入 RecommendationOutcome（已存在则 update）。
    返回 True 表示成功，False 表示出错（已 rollback savepoint）。
    """
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
        outcome.label_source = label_source
        outcome.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        rec.status = normalized_outcome_label(outcome)
        savepoint.commit()
        return True
    except Exception:
        savepoint.rollback()
        logger.warning("outcome labeling failed for %s", rec.recommendation_id, exc_info=True)
        return False


def label_pending_outcomes(
    db: Session,
    horizon_days: int = 5,
) -> dict[str, Any]:
    """对 pending 推荐做执行口径标注。

    保留原有静态窗口指标（close/max/min/achievable），同时新增：
    - 是否真实触发买点
    - 进出场价格/日期
    - 执行收益（含双边滑点）
    - 持仓天数 / 仓位权重 / 退出原因

    进程内串行（_label_outcome_lock）：防多入口并发双 session 捞同一批 pending 撞主键回滚
    （见 _label_outcome_lock 注释）。锁内完成查询→模拟→commit 全流程，第二个调用开始时
    第一个已 commit，db.get 命中 update 而非 INSERT。
    """
    with _label_outcome_lock:
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
                    # 临时态（数据仅至入场日）需在新交易日数据到位后重算为 success/failed，
                    # 否则永远停在 provisional 不会被翻新
                    RecommendationOutcome.outcome_label == OUTCOME_PROVISIONAL,
                    Recommendation.status == OUTCOME_PENDING,
                )
            )
        )
        recs = db.scalars(stmt.order_by(Recommendation.trade_date.asc())).unique().all()
        if not recs:
            return {"labeled": 0}

        bars_by_code, dates_by_code = _load_outcome_bars(db, recs)
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
            if _apply_outcome_simulation(db, rec, simulation, horizon_days, label_source="auto"):
                labeled += 1
            else:
                errors += 1

        db.commit()
        if labeled:
            invalidate_outcome_metrics_cache()
        return {"labeled": labeled, "errors": errors}


def _load_outcome_bars(
    db: Session,
    recs: list[Recommendation],
) -> tuple[dict[str, list[DailyKbar]], dict[str, list[str]]]:
    """批量加载 recs 涉及股票的日K（从最早 trade_date 起），返回 (bars_by_code, dates_by_code)。"""
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
    return bars_by_code, dates_by_code


def recompute_stale_outcomes(
    db: Session,
    horizon_days: int = 5,
    limit: int | None = None,
) -> dict[str, Any]:
    """重算所有已标注 outcome（simulation 逻辑变更后的数据一致性维护）。

    与 label_pending_outcomes 的区别：前者只处理 pending，本函数重算所有已标注的
    success/failed/missed outcome。用于 _resolve_exit / _simulate_outcome 逻辑变更后
    让历史 outcome 反映新逻辑（如 trailing stop 上线后让 exit_reason/executed_return 刷新）。

    label_source 标记为 "recompute" 以区分自动 backfill。
    """
    stmt = (
        select(Recommendation)
        .join(
            RecommendationOutcome,
            RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
        )
        .where(RecommendationOutcome.outcome_label.in_(EXECUTED_OUTCOME_LABELS | {OUTCOME_MISSED, OUTCOME_PROVISIONAL}))
    )
    stmt = stmt.order_by(Recommendation.trade_date.asc())
    if limit:
        stmt = stmt.limit(limit)
    recs = db.scalars(stmt).unique().all()
    if not recs:
        return {"recomputed": 0, "errors": 0}

    bars_by_code, dates_by_code = _load_outcome_bars(db, recs)
    recomputed = 0
    errors = 0
    flipped: dict[str, int] = {}
    for rec in recs:
        before_label = None
        existing = db.get(RecommendationOutcome, rec.recommendation_id)
        if existing is not None:
            before_label = str(existing.outcome_label)
        simulation = _simulate_outcome(
            rec,
            bars_by_code.get(str(rec.code), []),
            dates_by_code.get(str(rec.code), []),
            horizon_days=horizon_days,
        )
        if simulation is None:
            continue
        if _apply_outcome_simulation(db, rec, simulation, horizon_days, label_source="recompute"):
            after_label = str(simulation["outcome_label"])
            if before_label and before_label != after_label:
                key = f"{before_label}->{after_label}"
                flipped[key] = flipped.get(key, 0) + 1
            recomputed += 1
        else:
            errors += 1

    db.commit()
    if recomputed:
        invalidate_outcome_metrics_cache()
    return {"recomputed": recomputed, "errors": errors, "flipped": flipped}


def audit_trading_cost_boundary_labels(db: Session) -> dict[str, Any]:
    """Idempotently repair only labels affected by the former 0.10% threshold.

    Manual labels are never overwritten. The bounded 0.07%–0.10% scan avoids a
    full historical simulation and is a no-op when the database has no affected
    samples.
    """
    rows = list(
        db.scalars(
            select(RecommendationOutcome).where(
                RecommendationOutcome.entry_triggered.is_(True),
                RecommendationOutcome.executed_return > _ADDITIONAL_COST_PCT,
                RecommendationOutcome.executed_return <= 0.10,
                RecommendationOutcome.outcome_label.in_(EXECUTED_OUTCOME_LABELS),
                RecommendationOutcome.label_source != "manual",
            )
        ).all()
    )
    updated = 0
    for outcome in rows:
        expected = (
            OUTCOME_SUCCESS
            if is_net_profitable(resolved_trade_return(outcome)) and not outcome.hit_invalid
            else OUTCOME_FAILED
        )
        if outcome.outcome_label == expected and outcome.outcome_label_legacy == expected:
            continue
        outcome.outcome_label = expected
        outcome.outcome_label_legacy = expected
        outcome.label_source = "cost_policy_v2"
        outcome.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        updated += 1
    if updated:
        db.commit()
        invalidate_outcome_metrics_cache()
    return {
        "policy_version": "v2",
        "additional_cost_pct": _ADDITIONAL_COST_PCT,
        "scanned_count": len(rows),
        "updated_count": updated,
    }


def overview_metrics(
    db: Session,
    window_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    start, end = _resolve_window(window_days, start_date, end_date)
    key = f"overview:{start or ''}:{end or ''}"
    return _cached(key, lambda: _overview_metrics(db, start, end, window_days))


def _overview_metrics(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    rows = _scoped_recommendation_rows(db, start_date, end_date)
    total_recs = len(rows)

    # labeled = 已结算样本（success/failed/missed）；排除 provisional（数据仅至入场日的临时态，
    # executed_return 只是入场日收盘临时标记，不计入胜负统计，待新交易日数据到位后翻新）
    labeled = [
        row for row in rows
        if normalized_outcome_label(row) in {OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_MISSED}
    ]
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
            "total_recommendations": total_recs,
            "labeled_count": 0,
            "triggered_count": 0,
            "executed_trade_count": 0,
            "unexecuted_count": 0,
            "missed_count": missed_count,
            "pending_count": max(0, total_recs - n),
            "success_count": 0,
            "failed_count": 0,
            "date_range": _window_meta(window_days, start_date, end_date, total_recs, 0),
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
        "date_range": _window_meta(window_days, start_date, end_date, total_recs, n),
    }


def metrics_by_strategy(
    db: Session,
    window_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    run_mode: str | tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    start, end = _resolve_window(window_days, start_date, end_date)
    key = f"by_strategy:{start or ''}:{end or ''}:{run_mode or 'all'}"
    return _cached(key, lambda: _metrics_by_strategy(db, start, end, window_days, run_mode=run_mode))


def exposure_sample_counts(
    db: Session,
    window_days: int = 30,
    run_mode: tuple[str, ...] = ("formal", "experiment"),
) -> dict[str, int]:
    """近 window_days 各策略「真实」样本数（formal+experiment，排除 backtest 合成回测）。

    与 scripts/self_check.py 的 E 项曝光分布口径**完全一致**：按 recommended_at 近 N 天、
    run_mode IN (formal, experiment)、JSON_TABLE 展开 skill_breakdown_json，按推荐去重后
    每策略各计一次。供策略曝光补跑判定「哪些策略样本不足」。

    ⚠️ 必须与 self_check 口径一致（recommended_at，而非 trade_date）：早期 _run_strategy_exposure
    误用 metrics_by_strategy（按 trade_date 窗口），而 trade_date 与 recommended_at 严重偏离
    （大量推荐 trade_date 落在窗口外），导致 metrics_by_strategy 严重低估样本数——daily-stock
    真实 115 样本被误判为 7，曝光补跑错把高覆盖策略也选入、真正低覆盖策略反被淹没。改用
    recommended_at 口径后，曝光补跑才能精确命中 self_check 标出的 <SAMPLE_FLOOR 策略。
    """
    placeholders = ", ".join(f":m{i}" for i in range(len(run_mode)))
    params: dict[str, Any] = {f"m{i}": m for i, m in enumerate(run_mode)}
    params["days"] = window_days
    rows = db.execute(
        text(
            f"""
            SELECT jt.strategy_id, COUNT(DISTINCT rec.recommendation_id) AS cnt
            FROM recommendations rec
            JOIN recommendation_runs u ON rec.run_id = u.run_id
            JOIN JSON_TABLE(
                rec.skill_breakdown_json, '$[*]'
                COLUMNS (strategy_id VARCHAR(80) PATH '$.strategy_id')
            ) jt
            WHERE u.run_mode IN ({placeholders})
              AND rec.skill_breakdown_json IS NOT NULL
              AND rec.recommended_at >= DATE_SUB(NOW(), INTERVAL :days DAY)
            GROUP BY jt.strategy_id
            """
        ),
        params,
    ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}


def _metrics_by_strategy(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    window_days: int | None = None,
    run_mode: str | tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, start_date, end_date, run_mode=run_mode)
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
        # 最近一次该策略出现在该口径推荐中的交易日（北极星 C 项：策略可追溯）
        last_rec_date = max(
            (str(getattr(r, "trade_date", "") or "") for r in item["rows"]),
            default=None,
        ) or None
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
            "last_recommendation_date": last_rec_date,
            "horizon_metrics": _horizon_metrics_for_rows(item["rows"], horizon_records),
            **_group_metric_fields(metrics),
            "date_range": _window_meta(window_days, start_date, end_date, len(rows), metrics["trade_count"]),
        })
    return sorted(result, key=lambda x: (x["sample_count"], x["hit_rate"], x["avg_score"]), reverse=True)


def strategy_perf_summary(
    db: Session,
    strategy_id: str,
    window_days: int | None = 180,
) -> dict[str, Any] | None:
    """单策略历史表现摘要（轻量 SQL，供推荐详情卡片展示"历史同策略胜率"）。

    北极星 B 项：每条推荐一屏可见该策略历史战绩（样本/胜率/盈亏比/最近命中日）。
    与 metrics_by_strategy 的区别：只查一个策略、不构建完整 horizon_metrics，响应快。
    按 strategy_breakdown_json 命中该 strategy_id 的推荐归因（与 metrics_by_strategy 一致口径）。
    """
    if not strategy_id:
        return None
    start, _ = _resolve_window(window_days, None, None)
    # JSON_CONTAINS 在 MySQL 上对字符串数组可行；strategy_id 来自 bucket/skill_breakdown
    stmt = (
        select(
            Recommendation.recommendation_id,
            Recommendation.trade_date,
            RecommendationOutcome.outcome_label,
            RecommendationOutcome.executed_return,
        )
        .select_from(Recommendation)
        .join(RecommendationRun, Recommendation.run_id == RecommendationRun.run_id)
        .outerjoin(RecommendationOutcome, Recommendation.recommendation_id == RecommendationOutcome.recommendation_id)
        .where(
            Recommendation.skill_breakdown_json.like(f'%"{strategy_id}"%'),
        )
    )
    if start:
        stmt = stmt.where(Recommendation.recommended_at >= start)
    rows = db.execute(stmt).all()

    sample_count = len(rows)
    if sample_count == 0:
        return None
    labeled = [r for r in rows if r.outcome_label in ("success", "failed")]
    labeled_count = len(labeled)
    wins = [r for r in labeled if r.outcome_label == "success"]
    losses = [r for r in labeled if r.outcome_label == "failed"]
    win_avg = sum(r.executed_return or 0 for r in wins) / len(wins) if wins else 0.0
    loss_avg = sum(abs(r.executed_return or 0) for r in losses) / len(losses) if losses else 0.0
    last_rec_date = max((str(r.trade_date or "") for r in rows), default=None) or None
    return {
        "strategy_id": strategy_id,
        "sample_count": sample_count,
        "labeled_count": labeled_count,
        "success_count": len(wins),
        "hit_rate": round(len(wins) * 100 / labeled_count, 1) if labeled_count else None,
        "avg_return": round(sum(r.executed_return or 0 for r in labeled) / labeled_count, 2) if labeled_count else None,
        "profit_loss_ratio": round(win_avg / loss_avg, 2) if loss_avg else None,
        "last_recommendation_date": last_rec_date,
        "window_days": window_days,
    }


def recent_formal_win_rate(db: Session, window_trade_days: int = 5) -> dict[str, Any]:
    """近 N 个交易日 formal 推荐聚合胜率（北极星 B/C：驾驶舱状态条一眼可见系统近期战绩）。

    口径（与 R133/R148 一致，只用实盘 rec_ 推荐、已结算 outcome）：
    - trade_date 取最近 N 个 status=completed 的 formal 交易日（按交易日而非日历日，避免空窗虚算）。
    - 仅统计 outcome_label∈{success,failed} 的已结算推荐；未结算不计入分母（诚实，不虚构胜率）。
    - 内连接 recommendation_outcomes 天然排除回测 btrec_*；再叠加 rec_ 前缀过滤双保险。
    - 去污染反事实（2026-07-21 T18）：剔除 pure 低线(<72) + pure soft@震荡 后的净盈亏比，
      供状态条解释「近 N 日净 PL 偏低是否历史污染拖累」（契约层/soft ban 已拦新污染，
      历史票不重标；不改主口径 win_rate/net_pl，仅附加 clean_* 字段）。
    """
    cache_key = f"recent_formal_win_rate:{window_trade_days}"
    return _cached(cache_key, lambda: _recent_formal_win_rate(db, window_trade_days))


def _truthy_soft(v: Any) -> bool:
    return v is not None and str(v).lower() in ("1", "true", "yes")


def _pure_pollution_keys_from_rows(
    rows: list,
    *,
    score_floor: float = 72.0,
    banned_soft_bases: frozenset[str] | set[str] = frozenset({"震荡"}),
) -> set[tuple]:
    """从 win-rate 行提取 pure 污染 (code, trade_date)。

    行形：任一序列，至少含 code/td/sl/soft/mbase（见调用方映射）。
    pure_low = 仅 sl<floor 产出且同日同股无 hard(sl>=floor) 孪生
    pure_soft = 仅 soft@banned_base 产出且同日同股无 hard(非 soft) 孪生
    """
    hard_sl: set[tuple] = set()
    low_sl: set[tuple] = set()
    hard_soft: set[tuple] = set()
    soft_ban: set[tuple] = set()
    for code, td, sl, soft, base in rows:
        key = (code, str(td))
        try:
            sl_f = float(sl or 0)
        except (TypeError, ValueError):
            sl_f = 0.0
        if sl_f >= score_floor:
            hard_sl.add(key)
        else:
            low_sl.add(key)
        if not _truthy_soft(soft):
            hard_soft.add(key)
        elif str(base or "") in banned_soft_bases:
            soft_ban.add(key)
    pure_low = low_sl - hard_sl
    pure_soft = soft_ban - hard_soft
    return pure_low | pure_soft


def _net_pl_ratio(rets: list[float]) -> float | None:
    """扣 0.07%/笔后的盈亏比；无亏损侧时 None。"""
    net = [x - _ADDITIONAL_COST_PCT for x in rets]
    wins = [x for x in net if x > 0]
    losses = [abs(x) for x in net if x < 0]
    if not losses:
        return None
    win_avg = sum(wins) / len(wins) if wins else 0.0
    loss_avg = sum(losses) / len(losses)
    return round(win_avg / loss_avg, 2)


def _recent_formal_win_rate(db: Session, window_trade_days: int) -> dict[str, Any]:
    td_rows = (
        db.execute(
            select(RecommendationRun.trade_date)
            .where(RecommendationRun.run_mode == "formal", RecommendationRun.status == "completed")
            .distinct()
            .order_by(RecommendationRun.trade_date.desc())
            .limit(window_trade_days)
        )
        .scalars()
        .all()
    )
    trade_dates = list(td_rows)
    empty = {
        "window_trade_days": window_trade_days,
        "trade_dates": trade_dates,
        "sample_count": 0,
        "success_count": 0,
        "win_rate": None,
        "avg_return": None,
        "profit_loss_ratio": None,
        "max_loss": None,  # 窗口内单笔最差收益（D 项「最大亏损」口径，与 self_check 一致）
        "net_win_rate": None,
        "net_avg_return": None,
        "net_profit_loss_ratio": None,
        "clean_sample_count": 0,
        "clean_net_profit_loss_ratio": None,
        "pollution_n": 0,
        "pollution_pl_delta": None,
    }
    if not trade_dates:
        return empty

    # 带污染诊断字段：run.score_line + feature_snapshot 里 soft/mbase
    # 用原生 SQL + expanding IN，避免 text().label() 在部分 SA 版本不可用
    from sqlalchemy import bindparam
    td_strs = [str(d) for d in trade_dates]
    stmt = text("""
        SELECT r.code, r.trade_date, o.outcome_label, o.executed_return,
               u.score_line,
               JSON_EXTRACT(r.feature_snapshot_json,
                            '$.score_line_meta.dynamic_soft_floor_applied') AS soft,
               JSON_UNQUOTE(JSON_EXTRACT(r.feature_snapshot_json,
                            '$.score_line_meta.market_base')) AS mbase
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE r.recommendation_id LIKE 'rec_%'
          AND r.trade_date IN :tds
          AND o.outcome_label IN ('success', 'failed')
    """).bindparams(bindparam("tds", expanding=True))
    rows = db.execute(stmt, {"tds": td_strs}).all()

    sample_count = len(rows)
    if sample_count == 0:
        return empty
    wins = [r for r in rows if r.outcome_label == "success"]
    losses = [r for r in rows if r.outcome_label == "failed"]
    win_avg = sum(r.executed_return or 0 for r in wins) / len(wins) if wins else 0.0
    loss_avg = sum(abs(r.executed_return or 0) for r in losses) / len(losses) if losses else 0.0
    rets = [float(r.executed_return or 0) for r in rows]
    worst_loss = min(rets) if rets else None  # D 项「最大亏损」：窗口内单笔最差收益（与 self_check 口径一致）
    net_rets = [x - _ADDITIONAL_COST_PCT for x in rets]
    net_wins = [x for x in net_rets if x > 0]
    net_losses = [abs(x) for x in net_rets if x < 0]
    net_win_avg = sum(net_wins) / len(net_wins) if net_wins else 0.0
    net_loss_avg = sum(net_losses) / len(net_losses) if net_losses else 0.0
    net_pl = round(net_win_avg / net_loss_avg, 2) if net_loss_avg else None

    # 去污染：pure 低线 + pure soft@震荡 + 所有 soft 票（T39 对齐 self_check _extract_clean_dedup_rows）
    pol_input = [
        (r.code, r.trade_date, r.score_line, r.soft, r.mbase)
        for r in rows
    ]
    pol_keys = _pure_pollution_keys_from_rows(pol_input)
    # T39：self_check clean 口径排除所有 soft（非仅 pure_soft）——07-16 的 13 笔
    # "clean"全是 soft@震荡，混入让 clean PL 虚低。API 驾驶舱须与 self_check 对齐。
    all_soft_keys = {
        (r.code, str(r.trade_date))
        for r in rows
        if _truthy_soft(r.soft)
    }
    clean_rets = [
        float(r.executed_return or 0)
        for r in rows
        if (r.code, str(r.trade_date)) not in pol_keys
        and (r.code, str(r.trade_date)) not in all_soft_keys
    ]
    clean_pl = _net_pl_ratio(clean_rets) if clean_rets else None
    pol_n = len(pol_keys | all_soft_keys)
    pl_delta = (
        round(clean_pl - net_pl, 2)
        if clean_pl is not None and net_pl is not None and pol_n > 0
        else None
    )

    return {
        "window_trade_days": window_trade_days,
        "trade_dates": trade_dates,
        "sample_count": sample_count,
        "success_count": len(wins),
        "win_rate": round(len(wins) * 100.0 / sample_count, 1),
        "avg_return": round(sum(r.executed_return or 0 for r in rows) / sample_count, 2),
        "profit_loss_ratio": round(win_avg / loss_avg, 2) if loss_avg else None,
        "max_loss": round(worst_loss, 2) if worst_loss is not None else None,
        "net_win_rate": round(len(net_wins) * 100.0 / sample_count, 1),
        "net_avg_return": round(sum(net_rets) / sample_count, 2),
        "net_profit_loss_ratio": net_pl,
        # 去污染反事实（主口径不变，附加字段）
        "clean_sample_count": len(clean_rets),
        "clean_net_profit_loss_ratio": clean_pl,
        "pollution_n": pol_n,
        "pollution_pl_delta": pl_delta,
    }


def _is_20cm_code(code: str) -> bool:
    """20cm 板块判定（创业板 300/301、科创板 688/689），与 self_check._is_20cm_code 一致。"""
    return str(code).startswith(("300", "301", "688", "689"))


def board_and_net_breakdown(db: Session, window_trade_days: int = 10) -> dict[str, Any]:
    """D 项净成本 + 板块(20cm/10cm)分解 —— 北极星 D 项 observability，供前端指标页透出。

    净口径与 self_check._compute_d_cost 一致（扣 0.07%/笔）；板块判定与 self_check._is_20cm_code
    一致。复用 recent_formal_win_rate 的窗口口径（仅 LIVE rec_ + run_mode=formal + 已结算）。
    """
    cache_key = f"board_and_net_breakdown:{window_trade_days}"
    return _cached(cache_key, lambda: _board_and_net_breakdown(db, window_trade_days))


def _board_and_net_breakdown(db: Session, window_trade_days: int) -> dict[str, Any]:
    td_rows = (
        db.execute(
            select(RecommendationRun.trade_date)
            .where(RecommendationRun.run_mode == "formal", RecommendationRun.status == "completed")
            .distinct()
            .order_by(RecommendationRun.trade_date.desc())
            .limit(window_trade_days)
        )
        .scalars()
        .all()
    )
    trade_dates = [str(d) for d in td_rows]
    if not trade_dates:
        return {
            "window_trade_days": window_trade_days,
            "sample_count": 0,
            "net": None,
            "board": {"20cm": None, "10cm": None},
        }

    from sqlalchemy import bindparam

    stmt = text("""
        SELECT r.code, o.outcome_label, o.executed_return
        FROM recommendation_outcomes o
        JOIN recommendations r ON r.recommendation_id = o.recommendation_id
        JOIN recommendation_runs u ON r.run_id = u.run_id AND u.run_mode = 'formal'
        WHERE r.recommendation_id LIKE 'rec_%'
          AND r.trade_date IN :tds
          AND o.outcome_label IN ('success', 'failed')
    """).bindparams(bindparam("tds", expanding=True))
    rows = db.execute(stmt, {"tds": trade_dates}).all()

    rets = [float(r.executed_return or 0) for r in rows]
    n = len(rets)
    wins = [x for x in rets if x > 0]
    losses = [abs(x) for x in rets if x < 0]
    net = [x - _ADDITIONAL_COST_PCT for x in rets]
    net_wins = [x for x in net if x > 0]
    net_losses = [abs(x) for x in net if x < 0]

    net_summary: dict[str, Any] = {
        "sample_count": n,
        "win_rate": round(len(wins) * 100.0 / n, 1) if n else None,
        "net_win_rate": round(len(net_wins) * 100.0 / n, 1) if n else None,
        "avg_return": round(sum(rets) / n, 2) if n else None,
        "net_avg_return": round(sum(net) / n, 2) if n else None,
        "profit_loss_ratio": round(sum(wins) / sum(losses), 2) if losses else None,
        "net_profit_loss_ratio": round(sum(net_wins) / sum(net_losses), 2) if net_losses else None,
        "max_loss": round(min(rets), 2) if rets else None,
    }

    board: dict[str, Any] = {}
    for label in ("20cm", "10cm"):
        grp = [x for r, x in zip(rows, rets) if _is_20cm_code(r.code) == (label == "20cm")]
        gn = len(grp)
        if gn == 0:
            board[label] = None
            continue
        gw = [x for x in grp if x > 0]
        gl = [abs(x) for x in grp if x < 0]
        gnet = [x - _ADDITIONAL_COST_PCT for x in grp]
        gnw = [x for x in gnet if x > 0]
        gnl = [abs(x) for x in gnet if x < 0]
        board[label] = {
            "n": gn,
            "win_rate": round(len(gw) * 100.0 / gn, 1),
            "net_win_rate": round(len(gnw) * 100.0 / gn, 1),
            "avg_return": round(sum(grp) / gn, 2),
            "net_avg_return": round(sum(gnet) / gn, 2),
            "profit_loss_ratio": round(sum(gw) / sum(gl), 2) if gl else None,
            "net_profit_loss_ratio": round(sum(gnw) / sum(gnl), 2) if gnl else None,
        }

    return {
        "window_trade_days": window_trade_days,
        "trade_dates": trade_dates,
        "sample_count": n,
        "net": net_summary,
        "board": board,
    }


def metrics_by_setup(
    db: Session,
    window_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    start, end = _resolve_window(window_days, start_date, end_date)
    key = f"by_setup:{start or ''}:{end or ''}"
    return _cached(key, lambda: _metrics_by_setup(db, start, end, window_days))


def _metrics_by_setup(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    window_days: int | None = None,
) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, start_date, end_date)
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
                "date_range": _window_meta(window_days, start_date, end_date, len(rows), metrics["trade_count"]),
            }
        )
    return sorted(result, key=lambda item: (item["sample_count"], item["hit_rate"], item["avg_score"]), reverse=True)


def failure_attribution(db: Session) -> dict[str, Any]:
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


def alpha_decay_detection(db: Session) -> dict[str, Any]:
    """Alpha 衰减检测：对比近期 vs 早期各因子的 point-biserial 相关系数。

    如果因子的预测力在下降（衰减 > 30%），标记为"衰减中"。
    第一性原理：市场会学习并套利掉公开因子，alpha 必然衰减。
    """
    # 30s 缓存：alpha-decay 计算重（预解析 ~1594 个 feature_snapshot），
    # MetricsPage 打开时多次调用，缓存避免重复计算。
    return _cached("alpha_decay", lambda: _alpha_decay_detection(db))


def _alpha_decay_detection(db: Session) -> dict[str, Any]:
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
    rows = db.execute(stmt).all()
    if len(rows) < 20:
        return {"factors": [], "error": "insufficient samples", "total": len(rows)}

    mid = len(rows) // 2
    early_rows = rows[:mid]
    recent_rows = rows[mid:]

    # 预解析 feature_snapshot_json 一次（T4 性能优化：原 _extract_factor 每因子×2半段
    # 重新 json.loads，11 因子 × 2 × ~1594 行 = 35068 次解析 → 3883ms。预解析后仅 ~1594 次）
    def _preload_snapshots(row_pairs: list) -> list[dict[str, Any]]:
        return [
            {"snapshot": _loads(getattr(rec, "feature_snapshot_json", None), {}),
             "return": float(getattr(outcome, "executed_return", 0) or 0)}
            for rec, outcome in row_pairs
        ]

    early_preloaded = _preload_snapshots(early_rows)
    recent_preloaded = _preload_snapshots(recent_rows)

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

    def _extract_factor(preloaded: list[dict[str, Any]], factor: str) -> tuple[list[int], list[float]]:
        binary, returns = [], []
        for item in preloaded:
            val = item["snapshot"].get(factor)
            if val is not None:
                binary.append(1 if val else 0)
                returns.append(item["return"])
        return binary, returns

    factors_result = []
    for factor in _ALPHA_DECAY_FACTORS:
        eb, er = _extract_factor(early_preloaded, factor)
        rb, rr = _extract_factor(recent_preloaded, factor)
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


def cost_adjusted_metrics(db: Session) -> dict[str, Any]:
    """扣除佣金+印花税后的真实表现指标。

    注意：executed_return 已包含 entry/exit slippage（各 10bps），
    此函数额外扣除佣金（万 1 双边，单边 0.01%）和印花税（0.05% 卖出）。
    """
    stmt = select(RecommendationOutcome).where(
        RecommendationOutcome.entry_triggered == True,  # noqa: E712
        RecommendationOutcome.outcome_label.in_([OUTCOME_SUCCESS, OUTCOME_FAILED]),
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
    window_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """三规则过滤效果分析：pos20<50、avg_cost>200、结构性强势×support_ma5。

    返回被过滤 vs 保留交易的 WR/avg/笔数，用于监控过滤策略效果。
    """
    w_start, w_end = _resolve_window(window_days, start_date, end_date)
    key = f"filter_eff:{w_start or ''}:{w_end or ''}"
    return _cached(key, lambda: _filter_effectiveness_impl(db, w_start, w_end, window_days))


def _filter_effectiveness_impl(
    db: Session,
    w_start: str | None,
    w_end: str | None,
    window_days: int | None = None,
) -> dict[str, Any]:
    import json as _json

    stmt = (
        select(Recommendation, RecommendationOutcome)
        .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
        .where(
            RecommendationOutcome.entry_triggered == True,
            RecommendationOutcome.outcome_label.in_([OUTCOME_SUCCESS, OUTCOME_FAILED]),
        )
    )
    if w_start:
        stmt = stmt.where(Recommendation.trade_date >= w_start)
    if w_end:
        stmt = stmt.where(Recommendation.trade_date <= w_end)
    rows = db.execute(stmt).all()
    if not rows:
        return {
            "count": 0,
            "error": "no executed trades",
            "date_range": _window_meta(window_days, w_start, w_end, 0, 0),
        }

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
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 4) if rets else 0,
                "avg_return": round(sum(rets) / len(rets), 2) if rets else 0,
            }
            for name, rets in filtered_by_rule.items()
        },
        "date_range": _window_meta(window_days, w_start, w_end, total, total),
    }


def precision_at_k(
    db: Session,
    ks: tuple[int, ...] = (1, 3, 5, 10),
    window_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    w_start, w_end = _resolve_window(window_days, start_date, end_date)
    key = f"precision_at_k:{','.join(str(k) for k in ks)}:{w_start or ''}:{w_end or ''}"
    return _cached(key, lambda: _precision_at_k(db, ks, w_start, w_end, window_days))


def _precision_at_k(
    db: Session,
    ks: tuple[int, ...],
    start_date: str | None = None,
    end_date: str | None = None,
    window_days: int | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            Recommendation.run_id,
            Recommendation.recommendation_id,
            Recommendation.final_score,
            Recommendation.trade_date,
            RecommendationOutcome.outcome_label,
            RecommendationOutcome.entry_triggered,
            RecommendationOutcome.executed_return,
            RecommendationOutcome.close_return,
        )
        .select_from(Recommendation)
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .outerjoin(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
    )
    if start_date:
        stmt = stmt.where(Recommendation.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(Recommendation.trade_date <= end_date)
    recs = db.execute(
        stmt.order_by(Recommendation.run_id.asc(), Recommendation.final_score.desc(), Recommendation.recommendation_id.asc())
    ).all()
    total_in_window = len(recs)
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
            labeled_rows = [row for row in top_rows if normalized_outcome_label(row) not in (OUTCOME_PENDING, OUTCOME_PROVISIONAL)]
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
                "date_range": _window_meta(window_days, start_date, end_date, total_in_window, evaluated_count),
            }
        )
    return result


def metrics_by_score_line(
    db: Session,
    score_lines: tuple[int, ...] = (55, 60, 65, 70, 75, 80),
    window_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    start, end = _resolve_window(window_days, start_date, end_date)
    key = f"by_score_line:{','.join(str(line) for line in score_lines)}:{start or ''}:{end or ''}"
    return _cached(key, lambda: _metrics_by_score_line(db, score_lines, start, end, window_days))


def _metrics_by_score_line(
    db: Session,
    score_lines: tuple[int, ...],
    start_date: str | None = None,
    end_date: str | None = None,
    window_days: int | None = None,
) -> list[dict[str, Any]]:
    rows = _scoped_recommendation_rows(db, start_date, end_date)
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
                "date_range": _window_meta(window_days, start_date, end_date, total_count, metrics["trade_count"]),
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
        if normalized_outcome_label(row) not in (OUTCOME_PENDING, OUTCOME_PROVISIONAL)
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
    # 跨 metrics 端点共享缓存：by_score_line/by_strategy/by_setup/overview 各自调用此函数，
    # 冷启动时重复模拟 4 次（每次 ~526ms）。用行数 + 最新 trade_date 做指纹缓存。
    # 30 秒 TTL 与 metrics 缓存一致，label_pending 后会 invalidate。
    latest_td = max((str(getattr(r, "trade_date", "") or "") for r in rows), default="")
    cache_key = f"horizon_records:{len(rows)}:{latest_td}"
    return _cached(cache_key, lambda: _compute_horizon_metric_records(db, rows))



def _compute_horizon_metric_records(db: Session, rows: list[Any]) -> dict[str, dict[int, dict[str, Any]]]:
    labeled_rows = [
        row
        for row in rows
        if normalized_outcome_label(row) not in (OUTCOME_PENDING, OUTCOME_PROVISIONAL)
    ]
    if not labeled_rows:
        return {}
    if not all(hasattr(row, "recommend_price") and hasattr(row, "feature_snapshot_json") for row in labeled_rows):
        return {}

    codes = sorted({str(getattr(row, "code", "")) for row in labeled_rows if getattr(row, "code", None)})
    trade_dates = [str(getattr(row, "trade_date", "")) for row in labeled_rows if getattr(row, "trade_date", None)]
    if not codes or not trade_dates:
        return {}

    # 只取 _simulate_outcome 需要的 7 列（code/trade_date/open/high/low/close/pct_chg），
    # 不加载完整 DailyKbar 实体（19778 bars × 全列 ORM 实例化 = 0.86s 主因，T4 优化）。
    # Row 对象支持 .code/.open 等属性访问，_simulate_outcome 无需改。
    bar_rows = db.execute(
        select(
            DailyKbar.code,
            DailyKbar.trade_date,
            DailyKbar.open,
            DailyKbar.high,
            DailyKbar.low,
            DailyKbar.close,
            DailyKbar.pct_chg,
        )
        .where(DailyKbar.code.in_(codes), DailyKbar.trade_date >= min(trade_dates))
        .order_by(DailyKbar.code.asc(), DailyKbar.trade_date.asc())
    ).all()

    bars_by_code: dict[str, list] = {}
    dates_by_code: dict[str, list[str]] = {}
    for bar in bar_rows:
        code = str(bar.code)
        bars_by_code.setdefault(code, []).append(bar)
        dates_by_code.setdefault(code, []).append(str(bar.trade_date))

    records: dict[str, dict[int, dict[str, Any]]] = {}
    import bisect
    from app.services.outcome_simulation import _resolve_entry as _resolve_entry_fn
    max_horizon = max(BREAKDOWN_HORIZON_DAYS)
    for row in labeled_rows:
        rec_id = str(getattr(row, "recommendation_id", "") or "")
        code = str(getattr(row, "code", "") or "")
        if not rec_id or code not in bars_by_code:
            continue
        # 预解析 feature_snapshot，3 个 horizon 共享，省 2/3 JSON 解析（705→235 次）
        row_snapshot = _loads(getattr(row, "feature_snapshot_json", None), {})
        row_bars = bars_by_code.get(code, [])
        row_dates = dates_by_code.get(code, [])
        # 预解析 entry：BREAKDOWN_HORIZON_DAYS=[3,5,10] 都 ≥ ENTRY_LOOKAHEAD_DAYS=2，
        # entry_window=future_bars[:2] 与 horizon_days 无关 → 3 horizon 共享一次 _resolve_entry
        # （entry 解析占 _simulate_outcome 77% 耗时，省 2/3）。
        td = str(getattr(row, "trade_date", "") or "")
        sig_idx = bisect.bisect_left(row_dates, td) if td and row_dates else 0
        sig_present = sig_idx < len(row_dates) and row_dates[sig_idx] == td
        future_start = sig_idx + 1 if sig_present else sig_idx
        future_bars = row_bars[future_start:]
        signal_bar = row_bars[sig_idx] if sig_present else None
        pre_entry = _resolve_entry_fn(row, row_snapshot, future_bars, max_horizon, signal_bar=signal_bar) if future_bars else None
        for horizon_days in BREAKDOWN_HORIZON_DAYS:
            simulation = _simulate_outcome(
                row,
                row_bars,
                row_dates,
                horizon_days=horizon_days,
                feature_snapshot=row_snapshot,
                pre_resolved_entry=pre_entry,
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
        if normalized_outcome_label(row) in (OUTCOME_PENDING, OUTCOME_PROVISIONAL) or not is_trade_executed(row):
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


def _scoped_recommendation_rows(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    run_mode: str | tuple[str, ...] | list[str] | None = None,
) -> list[Any]:
    # 两步查询：先 SQL 去重拿 (trade_date, code) 分组下 final_score 最高的 recommendation_id（235 个），
    # 再按 id 取详情。避免查全部 1479 行大字段（feature_snapshot_json/market_context_json）再 Python 去重。
    # MySQL 8+ 窗口函数：ROW_NUMBER() OVER (PARTITION BY trade_date, code ORDER BY final_score DESC)
    # 时间窗口：start_date/end_date 为 ISO 日期字符串（YYYY-MM-DD），按 trade_date 过滤；
    # window_days 模式下调用方应先用 _resolve_window 转成 start/end 再传入。
    # run_mode：可选，按 recommendation_runs.run_mode 过滤（如 ('formal','experiment') 排除 backtest 合成回测）。
    dedup_ids_stmt = (
        select(
            Recommendation.recommendation_id,
            Recommendation.trade_date,
            Recommendation.code,
            Recommendation.final_score,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
    )
    if start_date:
        dedup_ids_stmt = dedup_ids_stmt.where(Recommendation.trade_date >= start_date)
    if end_date:
        dedup_ids_stmt = dedup_ids_stmt.where(Recommendation.trade_date <= end_date)
    if run_mode:
        if isinstance(run_mode, (list, tuple, set)):
            _modes = tuple(str(m) for m in run_mode)
            dedup_ids_stmt = dedup_ids_stmt.where(RecommendationRun.run_mode.in_(_modes))
        else:
            dedup_ids_stmt = dedup_ids_stmt.where(RecommendationRun.run_mode == str(run_mode))
    # 用子查询取每组 final_score 最大的 id（GROUP BY + MAX 定位，再 JOIN 回表）
    dedup_subq = dedup_ids_stmt.subquery()
    max_score_subq = (
        select(
            dedup_subq.c.trade_date,
            dedup_subq.c.code,
            func.max(dedup_subq.c.final_score).label("max_score"),
        )
        .group_by(dedup_subq.c.trade_date, dedup_subq.c.code)
    ).subquery()
    # JOIN 回原表取对应 recommendation_id（同 trade_date+code+final_score 的取第一个）
    id_rows = db.execute(
        select(dedup_subq.c.recommendation_id)
        .join(
            max_score_subq,
            (max_score_subq.c.trade_date == dedup_subq.c.trade_date)
            & (max_score_subq.c.code == dedup_subq.c.code)
            & (max_score_subq.c.max_score == dedup_subq.c.final_score),
        )
        .order_by(dedup_subq.c.trade_date.asc(), dedup_subq.c.final_score.desc())
    ).all()
    deduped_ids = [str(row[0]) for row in id_rows]
    if not deduped_ids:
        return []

    rows = list(
        db.execute(
            select(
                Recommendation.recommendation_id,
                Recommendation.run_id,
                Recommendation.code,
                Recommendation.name,
                Recommendation.recommend_price,
                Recommendation.final_score,
                Recommendation.recommended_at,
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
            .outerjoin(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .where(Recommendation.recommendation_id.in_(deduped_ids))
            .order_by(Recommendation.trade_date.asc(), Recommendation.final_score.desc(), Recommendation.recommendation_id.asc())
        ).all()
    )
    # 已通过 SQL 去重，但仍可能有同 (trade_date, code) 的重复（同分多笔），保留第一次
    # 下标对应 select 列：trade_date=10, code=2（recommended_at 插入后 trade_date 从 9 移到 10）
    seen: set[tuple] = set()
    deduped: list[Any] = []
    for row in rows:
        key = (row[10], row[2])  # (trade_date, code)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped




def regime_win_loss_breakdown(db: Session) -> dict[str, dict[str, float]]:
    """按 breadth.regime 分组统计胜率 + 盈亏比。

    防御性 T5 工具：判定 regime 优劣必须看盈亏比不能只看胜率。
    强势 WR 低但盈亏比高（大赢小亏，净正收益），震荡 WR 高但盈亏比可能 <1（赚的少亏的多）。

    返回 {regime: {count, win_rate, win_avg, loss_avg, profit_loss_ratio, avg_return}}。
    """
    rows = db.execute(
        text(
            """
            SELECT o.outcome_label, o.executed_return, r.market_context_json
            FROM recommendation_outcomes o
            JOIN recommendations r ON o.recommendation_id = r.recommendation_id
            WHERE o.outcome_label IN ('success', 'failed')
              AND r.market_context_json IS NOT NULL
            """
        )
    ).all()
    buckets: dict[str, dict[str, list[float]]] = {}
    for label, ret, mj in rows:
        try:
            ctx = _loads(mj)
            regime = (ctx.get("breadth") or {}).get("regime") or "unknown"
        except Exception:
            regime = "parse_error"
        b = buckets.setdefault(regime, {"wins": [], "losses": []})
        ret = float(ret or 0)
        if ret > 0:
            b["wins"].append(ret)
        elif ret < 0:
            b["losses"].append(abs(ret))
    result: dict[str, dict[str, float]] = {}
    for regime, b in buckets.items():
        wins, losses = b["wins"], b["losses"]
        total = len(wins) + len(losses)
        win_avg = sum(wins) / len(wins) if wins else 0.0
        loss_avg = sum(losses) / len(losses) if losses else 0.0
        result[regime] = {
            "count": total,
            "win_rate": round(len(wins) * 100 / total, 1) if total else 0.0,
            "win_avg": round(win_avg, 2),
            "loss_avg": round(loss_avg, 2),
            "profit_loss_ratio": round(win_avg / loss_avg, 2) if loss_avg else 0.0,
            "avg_return": round((sum(wins) - sum(losses)) / total, 2) if total else 0.0,
        }
    return result

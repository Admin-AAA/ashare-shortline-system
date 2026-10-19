"""Pure helpers extracted from app.services.outcome_service (no monkeypatched names; re-exported there)."""
import logging
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session
from app.engine.canonical import canonical_fingerprint, canonical_json
from app.engine.execution_core import IntradayExitEvidence
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY
from app.core.time import utcnow_naive
from app.engine.trading_costs import ADDITIONAL_COST_PCT as _ADDITIONAL_COST_PCT, COMMISSION_RATE_PCT as _COMMISSION_RATE_PCT, STAMP_TAX_RATE_PCT as _STAMP_TAX_RATE_PCT, is_net_profitable, net_return_after_cost
from app.models.tables import DailyKbar, Recommendation, RecommendationExecutionState, RecommendationOutcome, RecommendationOutcomeVersion, RecommendationRun, SkillRegistry
from app.services.evaluation_metrics import compute_exit_ambiguity_statistics, compute_full_metrics as _compute_full_metrics
from app.services.evidence.identity import EvidenceClass, has_canonical_persisted_identity
from app.services.evidence.provenance_writer import resolve_code_revision
from app.services.evidence.selectors import historical_reconstruction_run_conditions
from app.services.evidence.reconstruction_batch import ValidatedReconstructionCohort
from app.services.evidence.validation import canonical_historical_reconstruction_cohort, canonical_live_forward_run_ids
from app.services.execution_outcome_policy import apply_execution_outcome_policy
from app.services.entry_policy import execution_policy_fingerprint_for_entry_policy
from app.services.official_sample_scope import HISTORICAL_REPLAY_SCOPE, LIVE_FORWARD_SCOPE, official_sample_conditions
from app.services.outcome_identity import bounded_outcome_bars, label_policy_fingerprint, outcome_market_artifact_hash, outcome_market_artifact_payload, recommendation_identity_hash
from app.services.quality_outcome import CurrentProjection, OutcomeCalculationIdentity, OutcomeContractError, OutcomeRecomputeCandidate, OutcomeVersionRecord, audit_outcome_history, cas_current, select_stale_outcomes, upsert_outcome_version
from app.services.outcome_simulation import DEFAULT_STOP_LOSS_PCT, DEFAULT_TAKE_PROFIT_PCT, ENTRY_LOOKAHEAD_DAYS, ENTRY_SLIPPAGE_BPS, EXECUTED_OUTCOME_LABELS, EXIT_SLIPPAGE_BPS, MAX_ENTRY_GAP_PCT, MAX_STOP_LOSS_PCT, MAX_TAKE_PROFIT_PCT, OUTCOME_FAILED, OUTCOME_MISSED, OUTCOME_PENDING, OUTCOME_PROVISIONAL, OUTCOME_SUCCESS, STOP_LOSS_GAP_CAP_PCT, SUCCESS_RETURN_PCT, TRAILING_STOP_ACTIVATE_PCT, TRAILING_STOP_CALLBACK_PCT, TRAILING_STOP_MIN_LOCK_PCT, TRAILING_STOP_PROFIT_LOCK_RATIO, _as_float, _build_outcome_reason, _entry_execution_block_reason, _exit_execution_block_reason, _has_no_liquidity, _is_one_word_limit_down, _loads, _recommendation_execution_block_reason, _resolve_entry, _resolve_exit, _resolve_position_size, _resolve_stop_loss_price, _resolve_take_profit_price, _simulate_outcome, _static_window_metrics


# outcome 结算滞后天数：每日推荐需等 K 线走完 horizon（盘后 cron 默认 horizon_days=5）才会被
# label_pending_outcomes 标注为 success/failed/missed。滚动窗口模式下自动把
# end_date 截到 today - SETTLE_LAG_DAYS，保证窗口内推荐均已结算，避免 pending
# 污染样本（_metric_records_from_rows 虽会跳过 pending，但样本数会偏少且不可解释）。
# 5 天 horizon + 2 天缓冲（应对偶发结算延迟），过大会导致近期覆盖度补跑样本被滚动窗口截掉
# （曾出现 window_days=30 时有效区间被截到仅 10 天，最近补跑的策略样本全部消失）。
SETTLE_LAG_DAYS = 7


# 产品侧“正式表现”的唯一数据范围。实验补跑仍可由调用方显式传入
# run_mode=("formal", "experiment")，但不得默认混入胜率/KPI。
OFFICIAL_RUN_MODE = "formal"


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


def invalidate_outcome_metrics_cache() -> None:
    with _metrics_cache_lock:
        _metrics_cache.clear()
    from app.services.cache_hooks import invalidate as _invalidate_hook

    _invalidate_hook("outcome_metrics")


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


OUTCOME_CALCULATION_VERSION = "outcome_simulation_v2"


NON_VALID_OUTCOME_STATUS = "NON_VALID"


INCOMPLETE_PROVENANCE_STATUS = "INCOMPLETE_PROVENANCE"


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


def _is_20cm_code(code: str) -> bool:
    """20cm 板块判定（创业板 300/301、科创板 688/689），与 self_check._is_20cm_code 一致。"""
    return str(code).startswith(("300", "301", "688", "689"))


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

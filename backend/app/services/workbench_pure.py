"""Pure helpers extracted from app.services.workbench_service (no monkeypatched names; re-exported there)."""
import json
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session, defer
from app.agents.review_runner import run_skill_reviews
from app.agents.session_registry import AgentSessionRegistry
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.engine.agent_skill_runner import agent_only_signal, enrich_signal_with_agent_review
from app.engine.trading_costs import is_net_profitable
from app.services.model_router_service import has_active_model_routes
from app.engine.aggregator import aggregate, apply_pool_rules, market_regime_weights, resolve_display_limit, resolve_score_line_context
from app.engine.skill_loader import StrategySkill, load_skills, validate_skill
from app.engine.strategy import StrategyContext, build_registry_from_skills
from app.models.tables import AgentDecisionLog, CalibrationConfig, ConceptSectorStock, DailyKbar, MarketSnapshot, Preset, PresetSkill, Recommendation, RecommendationOutcome, RecommendationRun, ReviewNote, SkillRegistry, SkillVersion, Stock, StrategyVersion, WatchItem, ApiCallLog, StockQuote
from app.services.feature_builder import build_features, market_regime_score
from app.services.factors.registry import bootstrap_default_factor_registry
from app.services.auxiliary.application import inject_auxiliary_prefilter_flags, load_auxiliary_feature_views, run_auxiliary_application
from app.services.market_source.local_pool_adapter import load_legacy_market_universe
from app.services.daily_bar_write_policy import group_rows_by_quote_metric_presence, is_transient_bar, normalize_quote_metric, quote_metrics_look_unready
from app.services.failure_attribution import classify_false_positive
from app.services.execution_confirmation_state import load_execution_state_map
from app.services.execution_outcome_policy import build_cancel_observations, build_missed_observations
from app.services.market_data import MarketDataError, get_market_universe
from app.services.market_pool import get_market_snapshot_from_pool, get_market_universe_from_pool
from app.services.outcome_service import _ADDITIONAL_COST_PCT, invalidate_outcome_metrics_cache, is_accurate_recommendation, is_outcome_labeled, is_trade_executed, label_pending_outcomes as label_pending_outcomes_service, normalized_outcome_label, resolved_trade_return, strategy_perf_summary
from app.services.official_sample_scope import evaluation_scope_for_run, official_sample_conditions, recommendation_history_conditions
from app.services.evidence.selectors import historical_reconstruction_provenance_conditions, historical_reconstruction_run_conditions, live_forward_run_conditions
from app.services.evidence.validation import catchup_formal_run_ids, canonical_historical_reconstruction_run_ids, canonical_live_forward_run_ids
from app.services.prefilter_service import apply_prefilters
from app.services.research_context import build_research_contexts
from app.services.registry.data_source import ALLOWED_DATA_SOURCES, DATA_SOURCE_AUTO, DATA_SOURCE_LOCAL_POOL, DATA_SOURCE_LOCAL_POOL_PREFERRED, DATA_SOURCE_LOCAL_POOL_REALTIME, DATA_SOURCE_REALTIME, DEFAULT_PRESETS, DEFAULT_RECOMMENDATION_DATA_SOURCE, normalize_data_source
from app.services.registry.presets import _serialize_preset, create_preset, delete_preset, list_presets, update_preset
from app.services.registry.diagnostics import UnknownRegistryValueError, build_registry_diagnostics
from app.services.registry.skills import backtest_skill, batch_skill_performance_summaries, get_skill_detail, install_skill, list_skills, set_skill_enabled, skill_performance as _skill_performance, skill_performance_summary, skill_version_id as _skill_version_id, sync_skills
from app.services.runs.bootstrap_cache import get_bootstrap_cache, get_initial_state_cache, invalidate_bootstrap_cache, set_bootstrap_cache, set_initial_state_cache
from app.services.runs.filter_audit import apply_skill_review_failure_audit_to_stage_summary as _apply_skill_review_failure_audit_to_stage_summary, build_filter_audit as _build_filter_audit, build_pipeline_audit as _build_pipeline_audit, skill_review_failure_audit as _skill_review_failure_audit, skill_review_failure_audit_from_logs as _skill_review_failure_audit_from_logs, strip_audit_rows as _strip_audit_rows, upgrade_filter_audit as _upgrade_filter_audit
from app.services.candidates.trace import coverage_heatmap as _coverage_heatmap, fallback_filter_audit as _fallback_filter_audit, quality_distribution as _quality_distribution, score_distribution as _score_distribution
from app.services.recommendations.projection import serialize_recommendation as _project_recommendation, serialize_run_summary as _project_run_summary
from app.services.runs.persistence import _slim_market_snapshot, persist_recommendation_cards
from app.services.runs.progress import progress_pct as compute_progress_pct
from app.services.runs.contracts import CanonicalExecutionVersionBuilder, ExecutionVersionInput, RunRequest, StrategyExecutionFingerprint, RunStatus, application_identity_from_environment, parse_run_request, selected_recommendation_execution_policy, truncate_run_error_message, validate_run_request_no_credentials
from app.services.runs.request_persistence import assert_run_request_index_consistency, load_canonical_run_request
from app.services.runs.coordinator import RunCoordinator
from app.services.runs.deletion_guard import assert_recommendation_physically_deletable, assert_run_physically_deletable
from app.services.runs.repository import CreateQueuedRun
from app.services.runs.recovery import RecoveryCapabilityError, evaluate_recovery_capability
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from app.orchestration import trigger_recommendation
from app.services.runs.skill_gate import build_skill_rule_gate, finalize_skill_gate_audit, resolve_prefilter_target_count, resolve_skill_review_limit


BOOTSTRAP_CACHE_TTL_SECONDS = 30.0


INITIAL_STATE_CACHE_TTL_SECONDS = 30.0


RUN_HEARTBEAT_STALE_SECONDS = 1200  # Agent 单次审查可达 250s+，心跳必须大于外层超时(900s)


RUN_ACTIVE_STATUSES = {"queued", "running"}


RUN_RESUMABLE_STATUSES = {"interrupted"}


PROGRESS_COMMIT_INTERVAL_SECONDS = 10.0


UPSERT_BATCH_SIZE = 1000


# daily_kbars is an 80-column wide table whose upsert takes far more row locks per
# statement than the narrow stocks table, so it cannot share the batch size above:
# a full-market batch of 1000 overflowed InnoDB's lock table (MySQL 1206) and
# aborted the market_data stage.  200 matches what the gap-remediation service and
# the market pool already use for this same table (L-141).
DAILY_BAR_UPSERT_BATCH_SIZE = 200


_progress_commit_state_lock = threading.Lock()


_progress_commit_state: dict[str, dict[str, Any]] = {}


_outcome_backfill_lock = threading.Lock()


def _behaviour_identity(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Project only the execution-audit facts that change what a run does.

    ``application`` is deliberately excluded.  The deployed revision resolves to
    ``git rev-parse HEAD`` of a working tree that keeps moving, and the enqueueing
    API process and the executing Prefect worker pin it at their own startup — so
    any commit landing between enqueue and execution makes the two sides disagree
    on a fact that does not affect behaviour at all.  The executing revision is
    already recorded per run in ``recommendation_runs.code_revision``, so blocking
    on it here only bricks runs (L-068 follow-up).
    """
    policy = audit.get("execution_policy")
    policy = policy if isinstance(policy, Mapping) else {}
    factor_plan = audit.get("factor_plan")
    factor_plan = factor_plan if isinstance(factor_plan, Mapping) else {}
    fingerprints = audit.get("strategy_fingerprints")
    return {
        "request_schema_version": audit.get("request_schema_version"),
        "execution_policy_fingerprint": policy.get("fingerprint"),
        "factor_plan_fingerprint": factor_plan.get("fingerprint"),
        "strategy_fingerprints": list(fingerprints) if isinstance(fingerprints, list) else [],
    }


def _behaviour_identity_differences(
    persisted: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> list[str]:
    """Return one readable line per drifted component, both sides side by side.

    L-068 requires a fingerprint comparison to be able to print both sides; the
    previous message only said "不一致" and forced a manual investigation.
    """
    before = _behaviour_identity(persisted)
    after = _behaviour_identity(candidate)
    differences: list[str] = []
    for key in ("request_schema_version", "execution_policy_fingerprint", "factor_plan_fingerprint"):
        if before[key] != after[key]:
            differences.append(f"{key}: 入队={before[key]!r} 执行={after[key]!r}")
    if before["strategy_fingerprints"] != after["strategy_fingerprints"]:
        keyed_before = {
            str(item.get("strategy_id")): item
            for item in before["strategy_fingerprints"]
            if isinstance(item, Mapping)
        }
        keyed_after = {
            str(item.get("strategy_id")): item
            for item in after["strategy_fingerprints"]
            if isinstance(item, Mapping)
        }
        for strategy_id in sorted(set(keyed_before) | set(keyed_after)):
            if keyed_before.get(strategy_id) != keyed_after.get(strategy_id):
                differences.append(
                    f"strategy[{strategy_id}]: 入队={keyed_before.get(strategy_id)!r} "
                    f"执行={keyed_after.get(strategy_id)!r}"
                )
    return differences


# 推荐历史短时缓存：历史数据不频繁变化（新 run 后才变），30s TTL 足够
_history_cache: dict[str, tuple[float, Any]] = {}


_history_cache_lock = threading.Lock()


_HISTORY_CACHE_TTL = 30.0


def invalidate_recommendation_history_cache() -> None:
    """推荐或执行确认状态变化后清空历史页短缓存。"""
    with _history_cache_lock:
        _history_cache.clear()
    # 版本化缓存（workbench_service._history_*_cache）：库里的指纹已能发现变化，这里同进程内再立即清一次
    from app.services.data_version import clear_named

    clear_named("history_")


def _build_recommendation_history_summary(
    outcomes: list[Any],
    *,
    excluded_recommendation_ids: set[str] | None = None,
) -> dict[str, Any]:
    """按统一的已成交净收益口径汇总复盘指标。"""
    excluded = excluded_recommendation_ids or set()
    settled = [
        outcome
        for outcome in outcomes
        if str(getattr(outcome, "recommendation_id", "") or "") not in excluded
        and is_trade_executed(outcome)
        and normalized_outcome_label(outcome) in {"success", "failed"}
    ]
    net_returns = [resolved_trade_return(outcome) - _ADDITIONAL_COST_PCT for outcome in settled]
    win_count = sum(value > 0 for value in net_returns)
    trade_count = len(net_returns)
    total_net_return = sum(net_returns)
    return {
        "trade_count": trade_count,
        "win_count": win_count,
        "win_rate": round(win_count * 100.0 / trade_count, 1) if trade_count else None,
        "average_net_return": round(total_net_return / trade_count, 2) if trade_count else None,
        "total_net_return": round(total_net_return, 2),
        "additional_cost_pct": _ADDITIONAL_COST_PCT,
        "methodology": {
            "sample_scope": "canonical_history_live_and_reconstruction_v1",
            "entry_policy": "strategy_declared_with_legacy_fallback_v1",
            "win_definition": "executed_net_return_gt_zero",
            "deduplication": "trade_date_and_code_highest_score",
            "return_unit": "percentage_points_per_trade",
        },
    }


# filter_audit.rows 持久化了每只股票的完整诊断对象，但前端实际只消费
# rule_gate / score_line / trace / final_* 字段。prefilter(每股票 15 键 dict) 与
# trace_summary(字符串) 在 RunFunnelSection / RunStockScoreSection 中从未被读取
# （types.ts 虽声明但未使用；StrategyDetailPage 的 prefilter 是策略契约对象，无关）。
# 单 run 全市场 ~4955 行时这两个字段占 rows 载荷 ~40%(prefilter 约 4.5MB)，
# 纯属浪费带宽。剥离它们不影响任何 UI 行为，把 /api/runs/{id}/funnel 响应从
# ~12MB 降到 ~7MB。
_FUNNEL_ROW_DEAD_FIELDS = ("prefilter", "trace_summary")


def _strip_funnel_row_dead_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """原地剥离 funnel rows 中前端从不消费的诊断字段（纯函数、可单测）。"""
    if not rows:
        return rows
    for r in rows:
        for f in _FUNNEL_ROW_DEAD_FIELDS:
            r.pop(f, None)
    return rows


def _selected_skill_rows(selected_skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "skill_id": item["skill_id"],
            "name": item["name"],
            "weight": item["weight"],
            "version_id": item["version_id"],
            "has_agent_contract": item["skill"].agent_enabled,
            "has_executable_contract": item["skill"].contract is not None,
        }
        for item in selected_skills
    ]


def _serialize_run_summary_from_cache(run: RecommendationRun, preset: Preset | None, hit_rate: float) -> dict[str, Any]:
    return _project_run_summary(
        run,
        preset,
        hit_rate,
        active_statuses=RUN_ACTIVE_STATUSES,
        heartbeat_stale_seconds=RUN_HEARTBEAT_STALE_SECONDS,
        slim_result_summary=True,
    )


def _prelim_rule_score(stock: dict[str, Any], rule_signals_by_skill: dict[str, Any], snapshot: dict[str, Any]) -> float:
    scores = [
        float(signal.get("score") or 0)
        for signal in rule_signals_by_skill.values()
        if signal and not signal.get("hard_veto")
    ]
    prelim_score = max(scores) if scores else 0.0
    industry = stock.get("industry", "")
    if industry and industry != "未分类" and industry in (snapshot.get("main_industries") or []):
        prelim_score += 3.0
    return round(prelim_score, 2)


def _serialize_outcome(outcome: RecommendationOutcome | None) -> dict[str, Any] | None:
    if outcome is None:
        return None
    normalized_label = normalized_outcome_label(outcome)
    return {
        "calculation_version_id": outcome.calculation_version_id,
        "entry_policy_id": outcome.entry_policy_id,
        "horizon_days": outcome.horizon_days,
        "entry_triggered": outcome.entry_triggered,
        "entry_date": outcome.entry_date,
        "entry_price": outcome.entry_price,
        "exit_date": outcome.exit_date,
        "exit_price": outcome.exit_price,
        "executed_return": outcome.executed_return,
        "weighted_return": outcome.weighted_return,
        "position_size": outcome.position_size,
        "holding_days": outcome.holding_days,
        "entry_slippage_bps": outcome.entry_slippage_bps,
        "exit_slippage_bps": outcome.exit_slippage_bps,
        "stop_loss_price": outcome.stop_loss_price,
        "take_profit_price": outcome.take_profit_price,
        "exit_reason": outcome.exit_reason,
        "max_return": outcome.max_return,
        "min_return": outcome.min_return,
        "close_return": outcome.close_return,
        "achievable_return": outcome.achievable_return,
        "max_drawdown": outcome.max_drawdown,
        "hit_target": outcome.hit_target,
        "hit_invalid": outcome.hit_invalid,
        "outcome_label": normalized_label,
        "outcome_label_raw": outcome.outcome_label,
        "outcome_label_legacy": outcome.outcome_label_legacy,
        "outcome_reason": outcome.outcome_reason,
        "label_source": outcome.label_source,
        "labeled_at": outcome.labeled_at.isoformat() if outcome.labeled_at else None,
    }


def _summarize_replay_analysis_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    false_positive_rows = [row for row in rows if row.get("is_false_positive")]
    reason_counts: Counter[str] = Counter(
        str(row.get("false_positive_reason") or "未归因")
        for row in false_positive_rows
    )
    category_counts: Counter[tuple[str, str, str]] = Counter(
        (
            str(row.get("false_positive_category") or "unknown"),
            str(row.get("false_positive_category_label") or "未归因"),
            str(row.get("false_positive_stage") or "other"),
        )
        for row in false_positive_rows
    )
    stage_counts: Counter[str] = Counter(
        str(row.get("false_positive_stage") or "other")
        for row in false_positive_rows
    )
    label_counts: Counter[str] = Counter(
        str(row.get("user_label") or row.get("outcome_label") or "unlabeled")
        for row in rows
    )
    market_regime_counts: Counter[str] = Counter(
        str(row.get("market_regime") or "unknown")
        for row in rows
    )
    average_net_return = round(
        sum(float(row.get("net_return_pct") or 0) for row in rows if row.get("net_return_pct") is not None) / len(rows),
        3,
    ) if rows else 0.0
    false_positive_rate = round(len(false_positive_rows) / len(rows), 3) if rows else 0.0
    # 真实每笔：只算已成交并卖出的（扣往返成本）；没成交的只进成交率，不当 0% 摊进均值。
    # average_net_return 保留旧口径（全部行平均、未成交时回退收盘收益）只为兼容，页面不再展示它。
    executed_net = [
        float(row["executed_net_return_pct"])
        for row in rows
        if row.get("executed_settled") and row.get("executed_net_return_pct") is not None
    ]
    return {
        "total_count": len(rows),
        "false_positive_count": len(false_positive_rows),
        "false_positive_rate": false_positive_rate,
        "average_net_return": average_net_return,
        "executed_settled_count": len(executed_net),
        "execution_rate": round(len(executed_net) / len(rows), 3) if rows else 0.0,
        "average_executed_net_return": round(sum(executed_net) / len(executed_net), 3) if executed_net else None,
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in reason_counts.most_common()
        ],
        "category_counts": [
            {"category": category, "category_label": label, "stage": stage, "count": count}
            for (category, label, stage), count in category_counts.most_common()
        ],
        "stage_counts": [
            {"stage": stage, "count": count}
            for stage, count in stage_counts.most_common()
        ],
        "label_counts": [
            {"label": label, "count": count}
            for label, count in label_counts.most_common()
        ],
        "market_regime_counts": [
            {"market_regime": regime, "count": count}
            for regime, count in market_regime_counts.most_common()
        ],
    }


def _drop_as_repeat_card(
    card: dict[str, Any],
    params: dict[str, Any],
    recent_repeat: bool,
    same_day_repeat: bool,
) -> bool:
    """是否因「重复推荐」在最终过滤阶段剔除该卡片。

    - hide_recent_repeats：剔除近 7 日已被推荐过的 code（既有逻辑，跨日去重）。
    - dedup_same_day：剔除当日已被其他 formal run 推荐过的 code（新增，同日跨 run 去重）。
    两者正交，任一命中即剔除。
    """
    code = card.get("code")
    if not code:
        return False
    if params.get("hide_recent_repeats", True) and recent_repeat:
        return True
    if params.get("dedup_same_day", True) and same_day_repeat:
        return True
    return False


# 选股政策否决规则代号 → 中文（policy_drop_owner，见 aggregator_pure / daily_pick_cap）；映射不到的原样显示代号。
POLICY_RULE_LABELS: dict[str, str] = {
    "high_amount_veto": "大成交额",
    "high_turnover_veto": "高换手",
    "high_chg20_veto": "20日涨幅过大",
    "high_chg60_veto": "60日涨幅过大",
    "high_pos60_veto": "60日高位",
    "max_dd60_veto": "60日回撤过深",
    "limit_up_days20_veto": "20日涨停过多",
    "strong_climax_top_n": "高潮日限只数",
    "strong_high_ivol_veto": "强势高特质波动",
    "weak_low_ivoldn20_veto": "弱势低下行波动",
    "no_support_high_ivol_veto": "无均线支撑高波动",
    "require_ma_support": "无均线支撑",
    "extra_tail_veto": "尾部风险",
    "consensus_gate": "共识闸门",
    "anti_consensus_gate": "反向共识闸门",
    "board_universe": "板块范围",
    "excluded_strategy_bucket": "排除的策略类别",
    "daily_top_n": "当日名额已满",
    "daily_strategy_cap": "单策略当日上限",
    "score_line": "分数线",
}


# 名额类规则不是风控：原因文字单独写「名额限制」。
QUOTA_RULE_IDS = frozenset({"daily_top_n", "daily_strategy_cap"})


def _policy_block_detail(empty_reason: str | None) -> tuple[str | None, str | None]:
    """解析 filter_audit.empty_reason（"production_policy_blocked: n/m；a=1, b=2"）
    → (风控否决 "大成交额×1、高换手×2", 名额限制 "当日名额已满×3")，没有的一类为 None。"""
    text = str(empty_reason or "")
    if not text.startswith("production_policy_blocked"):
        return None, None
    _, _, detail = text.partition("；")
    risk: list[str] = []
    quota: list[str] = []
    for part in detail.split(","):
        rule, sep, count = part.strip().partition("=")
        if not rule:
            continue
        label = POLICY_RULE_LABELS.get(rule, rule)
        (quota if rule in QUOTA_RULE_IDS else risk).append(f"{label}×{count}" if sep else label)
    return ("、".join(risk) or None), ("、".join(quota) or None)


def _no_opportunity_reason(
    cards: list[dict[str, Any]],
    score_line: dict[str, Any],
    empty_reason: str | None = None,
) -> str:
    """0 推荐时的可读原因：最高分 / 有效线 / 被哪条风控否决 / 名额限制 / 市场阶段。"""
    top = max((float(c.get("final_score") or 0) for c in cards), default=0.0)
    effective = score_line.get("effective_score_line")
    market_base = score_line.get("market_base") or ""
    if top <= 0:
        return "无候选通过规则门（全部低于 Skill 门槛）"
    market = f"市场阶段 {market_base or '未知'}"
    risk, quota = _policy_block_detail(empty_reason)
    blocks = [x for x in ((f"被风控否决：{risk}" if risk else None), (f"受名额限制：{quota}" if quota else None)) if x]
    if effective and top >= float(effective) and blocks:
        return f"最高分 {top:.1f} 已过分数线 {float(effective):.1f}，但{'；'.join(blocks)}；{market}"
    parts = [f"最高分 {top:.1f}"]
    if effective:
        parts.append(f"未过有效分数线 {float(effective):.1f}" if top < float(effective) else f"有效分数线 {float(effective):.1f}")
    parts.extend(blocks)
    parts.append(market)
    return "；".join(parts)


def _chunked(rows: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + chunk_size] for index in range(0, len(rows), chunk_size)]


def _float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_market_snapshot() -> dict[str, Any]:
    """返回一个空的市场快照，用于本地数据池尚未初始化时的降级展示。"""
    return {
        "trade_date": "",
        "sample_count": 0,
        "up_count": 0,
        "down_count": 0,
        "limit_up_count": 0,
        "limit_down_count": 0,
        "main_industries": [],
        "breadth": {
            "regime": "未知",
            "data_source": "none",
            "note": "本地行情池尚未初始化，请先刷新数据池。",
        },
        "snapshot_id": "empty",
        "data_quality": "unavailable",
    }

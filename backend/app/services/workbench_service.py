import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import asyncio
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.agents.review_runner import run_skill_reviews
from app.agents.session_registry import AgentSessionRegistry
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.engine.agent_skill_runner import agent_only_signal, enrich_signal_with_agent_review
from app.services.model_router_service import has_active_model_routes
from app.engine.aggregator import (
    aggregate,
    apply_pool_rules,
    market_regime_weights,
    resolve_display_limit,
    resolve_score_line_context,
)
from app.engine.skill_loader import StrategySkill, load_skills, validate_skill
from app.engine.strategy import StrategyContext, build_registry_from_skills
from app.models.tables import (
    AgentDecisionLog,
    CalibrationConfig,
    ConceptSectorStock,
    DailyKbar,
    MarketSnapshot,
    Preset,
    PresetSkill,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    ReviewNote,
    SkillRegistry,
    SkillVersion,
    Stock,
    StrategyVersion,
    WatchItem,
    ApiCallLog,
    StockQuote,
)
from app.services.feature_builder import build_features, market_regime_score
from app.services.factors.registry import bootstrap_default_factor_registry
from app.services.auxiliary.application import (
    inject_auxiliary_prefilter_flags,
    load_auxiliary_feature_views,
    run_auxiliary_application,
)
from app.services.market_source.local_pool_adapter import load_legacy_market_universe
from app.services.failure_attribution import classify_false_positive
from app.services.market_data import MarketDataError, get_market_universe
from app.services.market_pool import (
    get_market_snapshot_from_pool,
    get_market_universe_from_pool,
)
from app.services.outcome_service import (
    _ADDITIONAL_COST_PCT,
    invalidate_outcome_metrics_cache,
    is_accurate_recommendation,
    is_outcome_labeled,
    is_trade_executed,
    label_pending_outcomes as label_pending_outcomes_service,
    normalized_outcome_label,
    resolved_trade_return,
    strategy_perf_summary,
)
from app.services.prefilter_service import apply_prefilters
from app.services.research_context import build_research_contexts
from app.services.registry.data_source import (
    ALLOWED_DATA_SOURCES,
    DATA_SOURCE_AUTO,
    DATA_SOURCE_LOCAL_POOL,
    DATA_SOURCE_LOCAL_POOL_PREFERRED,
    DATA_SOURCE_LOCAL_POOL_REALTIME,
    DATA_SOURCE_REALTIME,
    DEFAULT_PRESETS,
    DEFAULT_RECOMMENDATION_DATA_SOURCE,
    normalize_data_source,
)
from app.services.registry.presets import (
    _serialize_preset,
    create_preset,
    delete_preset,
    list_presets,
    update_preset,
)
from app.services.registry.skills import (
    backtest_skill,
    batch_skill_performance_summaries,
    get_skill_detail,
    install_skill,
    list_skills,
    set_skill_enabled,
    skill_performance as _skill_performance,
    skill_performance_summary,
    skill_version_id as _skill_version_id,
    sync_skills,
)
from app.services.runs.bootstrap_cache import (
    get_bootstrap_cache,
    get_initial_state_cache,
    invalidate_bootstrap_cache,
    set_bootstrap_cache,
    set_initial_state_cache,
)
from app.services.runs.filter_audit import (
    apply_skill_review_failure_audit_to_stage_summary as _apply_skill_review_failure_audit_to_stage_summary,
    build_filter_audit as _build_filter_audit,
    build_pipeline_audit as _build_pipeline_audit,
    skill_review_failure_audit as _skill_review_failure_audit,
    skill_review_failure_audit_from_logs as _skill_review_failure_audit_from_logs,
    strip_audit_rows as _strip_audit_rows,
    upgrade_filter_audit as _upgrade_filter_audit,
)
from app.services.candidates.trace import (
    coverage_heatmap as _coverage_heatmap,
    fallback_filter_audit as _fallback_filter_audit,
    quality_distribution as _quality_distribution,
    score_distribution as _score_distribution,
)
from app.services.recommendations.projection import (
    serialize_recommendation as _project_recommendation,
    serialize_run_summary as _project_run_summary,
)
from app.services.runs.persistence import persist_recommendation_cards
from app.services.runs.progress import progress_pct as compute_progress_pct
from app.services.runs.contracts import (
    CanonicalExecutionVersionBuilder,
    ExecutionVersionInput,
    RunRequest,
    StrategyExecutionFingerprint,
    RunStatus,
    application_identity_from_environment,
    parse_run_request,
    selected_recommendation_execution_policy,
    validate_run_request_no_credentials,
)
from app.services.runs.request_persistence import (
    assert_run_request_index_consistency,
    load_canonical_run_request,
)
from app.services.runs.coordinator import LegacyWorkbenchRunCoordinator
from app.services.runs.repository import CreateQueuedRun
from app.services.runs.sqlalchemy_repository import SQLAlchemyRunRepository
from app.services.runs.queue import BackgroundRunDispatcher, spawn_run_subprocess
from app.services.runs.skill_gate import (
    build_skill_rule_gate,
    finalize_skill_gate_audit,
    resolve_prefilter_target_count,
    resolve_skill_review_limit,
)


BOOTSTRAP_CACHE_TTL_SECONDS = 30.0
INITIAL_STATE_CACHE_TTL_SECONDS = 30.0
RUN_HEARTBEAT_STALE_SECONDS = 1200  # Agent 单次审查可达 250s+，心跳必须大于外层超时(900s)
RUN_ACTIVE_STATUSES = {"queued", "running"}
RUN_RESUMABLE_STATUSES = {"interrupted"}
PROGRESS_COMMIT_INTERVAL_SECONDS = 10.0
UPSERT_BATCH_SIZE = 1000

# 断点阶段常量，按执行顺序排列
CHECKPOINT_ORDER = [
    "prepare",
    "market_data",
    "scope_filter",
    "auxiliary_data",
    "feature_build",
    "rule_scan",
    "skill_gate",
    "skill_review",
    "aggregate",
    "persist",
]
def _select_aux_prefetch_codes(stocks: list[dict[str, Any]], count: int) -> list[str]:
    if count <= 0 or not stocks:
        return []

    ordered = [item for item in stocks if str(item.get("code") or "")]
    if count >= len(ordered):
        return [str(item["code"]) for item in ordered]

    def _num_field(item: dict[str, Any], field: str) -> float:
        return float(item.get(field) or 0)

    def _small_mid_cap_score(item: dict[str, Any]) -> tuple[float, float]:
        free_mv = _num_field(item, "free_mv_yi")
        if free_mv <= 0:
            return (-1.0, _num_field(item, "amount_yi"))
        if free_mv <= 300:
            return (300.0 - free_mv, _num_field(item, "amount_yi"))
        return (-free_mv, _num_field(item, "amount_yi"))

    ranking_groups = [
        sorted(ordered, key=lambda item: (_num_field(item, "amount_yi"), _num_field(item, "pct")), reverse=True),
        sorted(ordered, key=lambda item: (_num_field(item, "turnover_rate"), _num_field(item, "pct")), reverse=True),
        sorted(ordered, key=lambda item: (_num_field(item, "pct"), _num_field(item, "amount_yi")), reverse=True),
        sorted(ordered, key=_small_mid_cap_score, reverse=True),
    ]

    industry_best: list[dict[str, Any]] = []
    seen_industries: set[str] = set()
    industry_quota = max(1, min(6, count // 3))
    for item in ranking_groups[0]:
        industry = str(item.get("industry") or "")
        if not industry or industry == "未分类" or industry in seen_industries:
            continue
        seen_industries.add(industry)
        industry_best.append(item)
        if len(industry_best) >= industry_quota:
            break

    selected: list[str] = []
    seen_codes: set[str] = set()

    def _append(item: dict[str, Any]) -> None:
        code = str(item.get("code") or "")
        if not code or code in seen_codes:
            return
        seen_codes.add(code)
        selected.append(code)

    for item in industry_best:
        _append(item)
        if len(selected) >= count:
            return selected

    pointers = [0 for _ in ranking_groups]
    while len(selected) < count:
        progressed = False
        for idx, group in enumerate(ranking_groups):
            while pointers[idx] < len(group):
                item = group[pointers[idx]]
                pointers[idx] += 1
                code = str(item.get("code") or "")
                if not code or code in seen_codes:
                    continue
                _append(item)
                progressed = True
                break
            if len(selected) >= count:
                return selected
        if not progressed:
            break
    return selected


def _resolve_aux_prefetch_count(
    prefiltered_count: int,
    min_prefilter_count: int,
    *,
    skip_aux_refresh: bool,
    max_codes: int | None = None,
) -> int:
    if prefiltered_count <= 0:
        return 0
    bounded_max = max(1, int(max_codes or get_settings().auxiliary_prefetch_max_codes or 120))
    if skip_aux_refresh:
        return min(prefiltered_count, max(18, min_prefilter_count // 2), 48, bounded_max)
    return min(prefiltered_count, max(min_prefilter_count, bounded_max), bounded_max)


_progress_commit_state_lock = threading.Lock()
_progress_commit_state: dict[str, dict[str, Any]] = {}
_outcome_backfill_lock = threading.Lock()
_outcome_backfill_ts = 0.0
# Kept as a module-level injection point for callers/tests; the dispatcher resolves
# it at spawn time so a test can safely suppress child-process creation.
_spawn_run_subprocess = spawn_run_subprocess
_run_dispatcher = BackgroundRunDispatcher(
    session_factory=SessionLocal,
    mark_running=lambda db, run_id: _mark_run_dispatched(db, run_id),
    mark_failed=lambda db, run_id, exc: _mark_run_failed(db, run_id, exc),
    spawn=lambda run_id: _spawn_run_subprocess(run_id),
)


def _canonical_workbench_request(params: dict[str, Any], payload: dict[str, Any]) -> RunRequest:
    """Freeze one legacy/preset merge into the only durable RunRequest form.

    The parser owns the formal default.  An explicit caller value is copied exactly
    once, so malformed values fail validation and a preselected experiment/backtest
    mode cannot be silently replaced during a preset merge.
    """
    validate_run_request_no_credentials(payload)
    document = dict(params)
    if "run_mode" in payload:
        document["run_mode"] = payload["run_mode"]
    return parse_run_request(document)


def _factor_execution_identity() -> tuple[str, dict[str, Any]]:
    """Return the frozen Factor plan audit projection used by execution-version input."""
    plan = bootstrap_default_factor_registry()
    return (
        plan.fingerprint,
        {
            "contract_version": plan.contract_version,
            "fingerprint": plan.fingerprint,
            "factor_ids": list(plan.factor_ids),
        },
    )


def _execution_version_identity(
    request: RunRequest,
    selected_skills: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Build audit identity only; it never contributes to filtering or scoring.

    The selected skills are resolved through the same frozen registry contract as
    execution.  Persisting its immutable Descriptor projections makes the version
    change whenever a selected strategy manifest or implementation changes.
    """
    strategy_registry = build_registry_from_skills([selected["skill"] for selected in selected_skills])
    strategy_fingerprints = tuple(
        StrategyExecutionFingerprint.from_descriptor(
            strategy_registry.get_descriptor(str(selected["skill_id"]))
        )
        for selected in selected_skills
    )
    factor_plan_fingerprint, factor_plan_audit = _factor_execution_identity()
    version_input = ExecutionVersionInput(
        application=application_identity_from_environment(),
        request_schema_version=request.schema_version,
        execution_policy=selected_recommendation_execution_policy(request),
        strategy_fingerprints=strategy_fingerprints,
        factor_execution_plan_fingerprint=factor_plan_fingerprint,
    )
    execution_version = CanonicalExecutionVersionBuilder().build(version_input)
    return (
        execution_version,
        {
            "execution_version_schema_version": version_input.schema_version,
            "application": version_input.application.to_canonical_payload(),
            "request_schema_version": version_input.request_schema_version,
            "strategy_fingerprints": [
                fingerprint.to_canonical_payload()
                for fingerprint in version_input.strategy_fingerprints
            ],
            "factor_plan": factor_plan_audit,
            "execution_policy": {
                "policy_id": version_input.execution_policy.policy_id,
                "policy_version": version_input.execution_policy.policy_version,
                "fingerprint": version_input.execution_policy.fingerprint,
            },
        },
    )


def _execution_audit_from_result_summary(raw_summary: str | None) -> dict[str, Any] | None:
    """Return the first persisted execution audit without reconstructing it."""
    summary = _loads(raw_summary, {})
    execution = summary.get("execution") if isinstance(summary, dict) else None
    return dict(execution) if isinstance(execution, dict) else None


def _persist_execution_version(run: RecommendationRun, candidate: str) -> None:
    """Write a version once, or reject descriptor/policy drift before execution."""
    if not run.execution_version:
        run.execution_version = candidate
        return
    if run.execution_version != candidate:
        raise RuntimeError("Run 已持久化的 execution_version 与当前策略/因子/执行策略身份不一致")


def _mark_run_dispatched(db: Session, run_id: str) -> bool:
    """Move canonical queued runs through the state machine before spawning."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    repository = SQLAlchemyRunRepository(db)
    coordinator = LegacyWorkbenchRunCoordinator(repository)
    managed = repository.get_run(run_id) is not None
    started = coordinator.start_dispatch(run_id, now=now) if managed else False
    if managed and not started:
        logger.warning("dispatcher did not acquire queued Run lifecycle transition: %s", run_id)
        return False
    _update_run_progress(
        db,
        run_id,
        phase="starting",
        current=0,
        total=0,
        message="后台任务已接管，开始准备运行",
        status=None if managed else "running",
        # ``BackgroundRunDispatcher`` commits this lifecycle/progress pair before
        # spawning the child.  Keep both facts in its one explicit transaction.
        commit=False,
    )
    return True


def bootstrap_workbench_state(db: Session) -> dict[str, Any]:
    settings = get_settings()
    cache_key = (settings.database_url, str(settings.skills_dir.resolve()))
    cached = get_bootstrap_cache(cache_key, BOOTSTRAP_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    _auto_backfill_outcomes(db)

    t0 = time.perf_counter()
    sync_skills(db)
    t1 = time.perf_counter()
    presets = list_presets(db)
    t2 = time.perf_counter()
    recent_runs = list_runs(db, limit=20)
    latest_run = recent_runs[0] if recent_runs else None
    t3 = time.perf_counter()
    try:
        market_snapshot = get_market_snapshot_from_pool(db)
    except MarketDataError:
        market_snapshot = _empty_market_snapshot()
    t4 = time.perf_counter()
    skills = list_skills(db)
    t5 = time.perf_counter()
    logger.info(
        "bootstrap timing: sync_skills=%.0fms list_presets=%.0fms list_runs=%.0fms market_snapshot=%.0fms list_skills=%.0fms total=%.0fms",
        (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000, (t4 - t3) * 1000, (t5 - t4) * 1000, (t5 - t0) * 1000,
    )
    state = {
        "presets": presets,
        "recent_runs": {
            "latest": latest_run,
        },
        "market_snapshot": market_snapshot,
        "skill_summary": {
            "enabled_count": sum(1 for skill in skills if skill["enabled"]),
            "disabled_count": sum(1 for skill in skills if not skill["enabled"]),
        },
    }
    set_bootstrap_cache(cache_key, state)
    return state


def _auto_backfill_outcomes(db: Session, horizon_days: int = 5, min_interval_seconds: float = 600.0) -> None:
    global _outcome_backfill_ts
    now = time.monotonic()
    with _outcome_backfill_lock:
        if (now - _outcome_backfill_ts) < min_interval_seconds:
            return
        _outcome_backfill_ts = now
    try:
        label_pending_outcomes_service(db, horizon_days=horizon_days)
    except Exception:
        logger.exception("automatic outcome backfill failed")


def get_initial_workbench_state(db: Session) -> dict[str, Any]:
    from app.services.cost_service import list_scheduled_task_runs

    settings = get_settings()
    cache_key = (settings.database_url, str(settings.skills_dir.resolve()))
    cached = get_initial_state_cache(cache_key, INITIAL_STATE_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    from app.services.data_sources import list_data_sources

    _auto_backfill_outcomes(db)

    t0 = time.perf_counter()
    home_state = bootstrap_workbench_state(db)
    t1 = time.perf_counter()
    presets = list_presets(db)
    t2 = time.perf_counter()
    runs = list_runs(db)
    t3 = time.perf_counter()
    task_runs = list_scheduled_task_runs(db)
    t35 = time.perf_counter()
    skills = list_skills(db)
    t4 = time.perf_counter()
    data_sources = list_data_sources(db)
    t5 = time.perf_counter()
    payload = {
        "home": home_state,
        "presets": presets,
        "runs": runs,
        "task_runs": task_runs,
        "skills": skills,
        "data_sources": data_sources,
    }
    logger.info(
        "initial_state timing: home=%.0fms presets=%.0fms runs=%.0fms task_runs=%.0fms skills=%.0fms data_sources=%.0fms total=%.0fms",
        (t1 - t0) * 1000,
        (t2 - t1) * 1000,
        (t3 - t2) * 1000,
        (t35 - t3) * 1000,
        (t4 - t35) * 1000,
        (t5 - t4) * 1000,
        (t5 - t0) * 1000,
    )
    set_initial_state_cache(cache_key, payload)
    return payload


def enqueue_workbench_run(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a canonical queued request before the dispatcher may spawn a worker."""
    skills = sync_skills(db)
    skill_map = {skill.skill_id: skill for skill in skills}
    preset = db.get(Preset, payload["preset_id"]) if payload.get("preset_id") else None
    if payload.get("preset_id") and preset is None:
        raise ValueError("预设不存在，请刷新后重试")
    params = _merge_run_params(db, preset, payload)
    request = _canonical_workbench_request(params, payload)
    # ``params_json`` remains a legacy display projection, so keep its mode
    # aligned with the canonical request instead of leaving an implicit formal
    # default that would misdescribe experiment/backtest runs.
    params["run_mode"] = request.run_mode.value
    selected_skills = _select_skills_for_run(db, skill_map, params["skills"])
    if not selected_skills:
        raise ValueError("没有可运行的 Skill，请检查预设配置或 Skill 状态")

    run_id = _new_run_id()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    execution_version, execution_audit = _execution_version_identity(request, selected_skills)
    repository = SQLAlchemyRunRepository(db)
    created = repository.create_queued_run(
        CreateQueuedRun(
            run_id=run_id,
            request=request,
            execution_version=execution_version,
            created_at=now,
        )
    )
    if not created.applied:
        raise RuntimeError(f"无法持久化 queued Run: {created.outcome.value}")
    run = db.get(RecommendationRun, run_id)
    assert run is not None  # The repository just inserted and flushed this row.
    # Keep legacy projections byte-compatible while request_json remains canonical.
    run.selected_skills_json = _dumps(_selected_skill_rows(selected_skills))
    run.params_json = _dumps(params)
    run.result_summary_json = _dumps(
        {
            "agent_runtime": "queued",
            "execution": {**execution_audit, "execution_version": execution_version},
        }
    )
    db.commit()
    invalidate_bootstrap_cache()
    _run_dispatcher.enqueue(run_id)
    return {
        "run": _serialize_run_summary(db, run),
        "recommendations": [],
        "queued": True,
    }


def dispatch_workbench_run(db: Session, run_id: str) -> None:
    """Dispatch an already-persisted run after its state transition is committed.

    Recovery uses this same boundary as initial enqueue.  The repository read is a
    cutover guard: an invalid or legacy-only row cannot be sent to a worker that
    would have to reconstruct business input from ``params_json``.
    """
    normalized_run_id = str(run_id).strip()
    if not normalized_run_id:
        raise ValueError("run_id 不能为空")
    record = SQLAlchemyRunRepository(db).get_run(normalized_run_id)
    if record is None:
        raise RuntimeError(f"Run {normalized_run_id} 没有可用的 canonical request")
    if record.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
        raise RuntimeError(f"Run {normalized_run_id} 当前状态不可派发: {record.status.value}")
    # The caller owns the preceding CAS commit.  No request payload is allowed to
    # cross this boundary; the dispatcher carries only run_id.
    _run_dispatcher.enqueue(normalized_run_id)


def list_runs(db: Session, limit: int = 100) -> list[dict[str, Any]]:
    stmt = select(RecommendationRun).order_by(RecommendationRun.started_at.desc()).limit(limit)
    runs = db.scalars(stmt).all()
    _auto_recover_stale_runs(db, runs)
    return _serialize_run_summaries_batched(db, runs)


def _auto_recover_stale_runs(db: Session, runs: list[RecommendationRun]) -> int:
    """Route dashboard watchdog recovery through the same lifecycle CAS."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_ids = [
        run.run_id
        for run in runs
        if run.status in RUN_ACTIVE_STATUSES
        and run.heartbeat_at
        and (now - run.heartbeat_at).total_seconds() > RUN_HEARTBEAT_STALE_SECONDS
    ]
    if not stale_ids:
        return 0
    return recover_interrupted_runs(db, stale_ids)


def get_run_detail(db: Session, run_id: str) -> dict[str, Any] | None:
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return None
    data = _serialize_run_summary(db, run)
    data["params"] = _loads(run.params_json, {})
    if isinstance(data["params"], dict):
        data["params"].pop("output_count", None)
    data["market_snapshot"] = _loads(run.market_snapshot_json, {})
    data["result_summary"] = _loads(run.result_summary_json, {})
    data["pipeline_audit"] = (
        (data["result_summary"] or {}).get("pipeline")
        or ((data["market_snapshot"] or {}).get("breadth") or {}).get("recommendation_pipeline")
        or {}
    )
    return data


def delete_run(db: Session, run_id: str) -> bool:
    run = db.get(RecommendationRun, run_id)
    if run is None:
        logger.warning("delete_run: run_id=%s not found (already deleted or never existed), treating as idempotent success", run_id)
        invalidate_bootstrap_cache()
        return True
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run_id)).all()
    rec_ids = [rec.recommendation_id for rec in recs]
    if rec_ids:
        db.execute(select(AgentDecisionLog).where(AgentDecisionLog.recommendation_id.in_(rec_ids)))
        for aidl in db.scalars(select(AgentDecisionLog).where(AgentDecisionLog.recommendation_id.in_(rec_ids))).all():
            db.delete(aidl)
        for rid in rec_ids:
            db.execute(text("DELETE FROM review_notes WHERE recommendation_id = :rid"), {"rid": rid})
            db.execute(text("DELETE FROM watch_items WHERE source_recommendation_id = :rid"), {"rid": rid})
            db.execute(text("DELETE FROM recommendation_outcomes WHERE recommendation_id = :rid"), {"rid": rid})
        for rec in recs:
            db.delete(rec)
    db.execute(text("DELETE FROM watch_items WHERE source_run_id = :run_id"), {"run_id": run_id})
    db.delete(run)
    db.commit()
    invalidate_bootstrap_cache()
    return True


def delete_recommendation(db: Session, recommendation_id: str) -> bool:
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        logger.warning("delete_recommendation: recommendation_id=%s not found, treating as idempotent success", recommendation_id)
        invalidate_bootstrap_cache()
        return True
    db.execute(text("DELETE FROM agent_decision_logs WHERE recommendation_id = :rid"), {"rid": recommendation_id})
    db.execute(text("DELETE FROM review_notes WHERE recommendation_id = :rid"), {"rid": recommendation_id})
    db.execute(text("DELETE FROM watch_items WHERE source_recommendation_id = :rid"), {"rid": recommendation_id})
    db.execute(text("DELETE FROM recommendation_outcomes WHERE recommendation_id = :rid"), {"rid": recommendation_id})
    db.delete(rec)
    db.commit()
    invalidate_bootstrap_cache()
    return True


def list_run_recommendations(db: Session, run_id: str) -> list[dict[str, Any]]:
    recs = db.scalars(
        select(Recommendation).where(Recommendation.run_id == run_id).order_by(Recommendation.final_score.desc())
    ).all()
    return _serialize_recommendations_batched(db, recs)


# 推荐历史短时缓存：历史数据不频繁变化（新 run 后才变），30s TTL 足够
_history_cache: dict[str, tuple[float, Any]] = {}
_history_cache_lock = threading.Lock()
_HISTORY_CACHE_TTL = 30.0


def list_recommendation_history(
    db: Session,
    *,
    code: str | None = None,
    label: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    bucket: str | None = None,
    setup_type: str | None = None,
    strategy_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """跨 run 推荐历史（带 outcome），支持分页 + 筛选。

    填补北极星"推荐历史记录页"缺口：用户可查看所有推荐过的股票及其实际表现。
    按 (trade_date, code) 去重，保留 final_score 最高的（回测 DB 累积重复 run）。

    性能：先只取 recommendation_id + trade_date + code + final_score（轻量列，不取整行），
    Python 去重后切片分页，再只对 50 个 id 批量取详情。避免查全部 1584 行整行 + outcome。
    """
    # 短时缓存：历史数据不频繁变化（新 run 后才变），30s TTL
    cache_key = f"hist:{code or ''}:{label or ''}:{date_from or ''}:{date_to or ''}:{bucket or ''}:{setup_type or ''}:{strategy_id or ''}:{limit}:{offset}"
    now = time.time()
    with _history_cache_lock:
        entry = _history_cache.get(cache_key)
        if entry is not None and (now - entry[0]) < _HISTORY_CACHE_TTL:
            return entry[1]
    # 第 1 步：轻量查询——只取去重所需列（不取 feature_snapshot_json 等大字段）
    id_stmt = (
        select(
            Recommendation.recommendation_id,
            Recommendation.trade_date,
            Recommendation.code,
            Recommendation.final_score,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .order_by(Recommendation.trade_date.desc(), Recommendation.final_score.desc())
    )
    if code:
        id_stmt = id_stmt.where(Recommendation.code == code)
    if date_from:
        id_stmt = id_stmt.where(Recommendation.trade_date >= date_from)
    if date_to:
        id_stmt = id_stmt.where(Recommendation.trade_date <= date_to)
    if bucket:
        id_stmt = id_stmt.where(Recommendation.bucket == bucket)
    if setup_type:
        id_stmt = id_stmt.where(Recommendation.setup_type == setup_type)
    if strategy_id:
        # skill_breakdown_json 是 text 列（Python json.dumps 默认 "key": value 带空格），
        # 用 JSON_SEARCH 做 spacing-agnostic 的 strategy_id 过滤。
        # 北极星 C 项：策略清单卡可下钻到该策略的全部推荐历史。
        id_stmt = id_stmt.where(
            text(
                "JSON_SEARCH(recommendations.skill_breakdown_json,'one',:sid,NULL,'$[*].strategy_id') IS NOT NULL"
            ).bindparams(sid=str(strategy_id))
        )
    if label:
        if label == "pending":
            id_stmt = id_stmt.outerjoin(
                RecommendationOutcome,
                RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
            ).where(
                or_(
                    RecommendationOutcome.outcome_label.is_(None),
                    RecommendationOutcome.outcome_label == "pending",
                )
            )
        else:
            id_stmt = id_stmt.join(
                RecommendationOutcome,
                RecommendationOutcome.recommendation_id == Recommendation.recommendation_id,
            ).where(RecommendationOutcome.outcome_label == label)

    id_rows = db.execute(id_stmt).all()

    seen: set[tuple[str, str]] = set()
    deduped_ids: list[str] = []
    for rec_id, td, c, _score in id_rows:
        key = (str(td), str(c))
        if key in seen:
            continue
        seen.add(key)
        deduped_ids.append(rec_id)

    total = len(deduped_ids)
    page_ids = deduped_ids[offset : offset + limit]
    if not page_ids:
        return {"items": [], "total": total, "offset": offset, "limit": limit}

    # 第 2 步：只对分页的 id 批量取详情（推荐 + outcome）
    page_recs = db.scalars(
        select(Recommendation).where(Recommendation.recommendation_id.in_(page_ids))
    ).all()
    rec_by_id = {r.recommendation_id: r for r in page_recs}
    page_outcomes = db.scalars(
        select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_(page_ids))
    ).all()
    outcome_map = {o.recommendation_id: o for o in page_outcomes}

    # 按去重后的 id 顺序排序（保证 trade_date DESC, final_score DESC）
    ordered_recs = [rec_by_id[rid] for rid in page_ids if rid in rec_by_id]
    items = _serialize_recommendations_batched(db, ordered_recs)
    for item in items:
        rec_id = item.get("recommendation_id")
        item["outcome"] = _serialize_outcome(outcome_map.get(rec_id)) if rec_id else None

    result = {"items": items, "total": total, "offset": offset, "limit": limit}
    with _history_cache_lock:
        _history_cache[cache_key] = (time.time(), result)
    return result


def get_run_replay_analysis(db: Session, run_id: str) -> dict[str, Any] | None:
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return None

    recs = db.scalars(
        select(Recommendation)
        .where(Recommendation.run_id == run_id)
        .order_by(Recommendation.trade_date.desc(), Recommendation.final_score.desc())
    ).all()
    if not recs:
        return {
            "run": _serialize_run_summary(db, run),
            "rows": [],
            "summary": _summarize_replay_analysis_rows([]),
        }

    rec_ids = [rec.recommendation_id for rec in recs]
    outcome_map = {
        item.recommendation_id: item
        for item in db.scalars(
            select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_(rec_ids))
        ).all()
    }
    review_map = {
        item.recommendation_id: item
        for item in db.scalars(
            select(ReviewNote).where(ReviewNote.recommendation_id.in_(rec_ids))
        ).all()
    }

    rows: list[dict[str, Any]] = []
    for rec in recs:
        detail = _serialize_recommendation_cached(rec, None)
        detail["outcome"] = _serialize_outcome(outcome_map.get(rec.recommendation_id))
        review = review_map.get(rec.recommendation_id)
        if review is not None:
            detail["review_note"] = {
                "user_label": review.user_label,
                "failure_reason": review.failure_reason,
                "review_text": review.review_text,
                "updated_at": review.updated_at.isoformat() if review.updated_at else None,
            }
        rows.append(_build_replay_analysis_row(detail))

    return {
        "run": _serialize_run_summary(db, run),
        "rows": rows,
        "summary": _summarize_replay_analysis_rows(rows),
    }


def _mark_run_failed(db: Session, run_id: str, exc: Exception) -> None:
    """Persist dispatcher/worker failure through lifecycle CAS when available."""
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        repository = SQLAlchemyRunRepository(db)
        lifecycle = LegacyWorkbenchRunCoordinator(repository)
        record = repository.get_run(run_id)
        if record is not None:
            if record.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                transition_applied = lifecycle.spawn_failed(run_id, now=now, message=str(exc))
                if not transition_applied:
                    logger.warning("run failure CAS lost for %s; refusing direct status overwrite", run_id)
                    db.rollback()
                    return
                db.expire_all()
                run = db.get(RecommendationRun, run_id)
                if run is None:
                    return
                # The lifecycle CAS owns status, finished_at, heartbeat and lease
                # cleanup.  Only legacy projection fields are written here.
                run.progress_phase = "failed"
                run.progress_message = str(exc)
                db.commit()
                invalidate_bootstrap_cache()
                _record_progress_commit(run)
            return

        # Historical rows without canonical request columns cannot participate in
        # the state machine; preserve their old projection-only failure fallback.
        run = db.get(RecommendationRun, run_id)
        if run is None or run.status not in {"queued", "running"}:
            return
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = now
        run.heartbeat_at = now
        run.progress_phase = "failed"
        run.progress_message = str(exc)
        db.commit()
        invalidate_bootstrap_cache()
        _record_progress_commit(run)
    except Exception:
        db.rollback()
        logger.exception("failed to persist background run failure: %s", run_id)


def _update_run_progress(
    db: Session,
    run_id: str | None,
    phase: str,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
    status: str | None = None,
    commit: bool = True,
) -> RecommendationRun | None:
    if not run_id:
        return None
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lifecycle_repository = SQLAlchemyRunRepository(db)
    lifecycle_record = lifecycle_repository.get_run(run_id)
    if lifecycle_record is not None and lifecycle_record.lease is not None:
        renewed = LegacyWorkbenchRunCoordinator(lifecycle_repository).renew_worker_lease(
            run_id,
            now=now,
            ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
        )
        if not renewed:
            raise RuntimeError("Run lifecycle lease 已丢失或过期，worker 必须停止写入")
        # Repository CAS expires the identity map so this legacy progress write
        # cannot accidentally flush an obsolete status/revision view.
        run = db.get(RecommendationRun, run_id)
        if run is None:
            return None
    run.heartbeat_at = now
    run.progress_phase = phase
    if current is not None:
        run.progress_current = max(0, int(current))
    if total is not None:
        run.progress_total = max(0, int(total))
    if message is not None:
        run.progress_message = message
    if status is not None:
        run.status = status
        if status in RUN_ACTIVE_STATUSES:
            run.finished_at = None
            run.interrupted_at = None
        if status != "failed":
            run.error_message = None
    if commit:
        if _should_commit_progress(
            run_id=run.run_id,
            phase=run.progress_phase or phase,
            status=run.status,
            current=run.progress_current,
            total=run.progress_total,
            now=now,
        ):
            db.commit()
            invalidate_bootstrap_cache()
            _record_progress_commit(run)
        else:
            db.flush()
    else:
        db.flush()
    return run


def recover_interrupted_runs(db: Session, run_ids: list[str] | None = None) -> int:
    """Move stale active runs to ``interrupted`` through the lifecycle CAS."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = select(RecommendationRun).where(RecommendationRun.status.in_(tuple(RUN_ACTIVE_STATUSES)))
    if run_ids is not None:
        stmt = stmt.where(RecommendationRun.run_id.in_(run_ids))
    runs = db.scalars(stmt).all()
    repository = SQLAlchemyRunRepository(db)
    lifecycle = LegacyWorkbenchRunCoordinator(repository)
    recovered = 0
    for run in runs:
        heartbeat_age = (now - run.heartbeat_at).total_seconds() if run.heartbeat_at else float("inf")
        if heartbeat_age <= RUN_HEARTBEAT_STALE_SECONDS:
            logger.info("keep running run %s alive (heartbeat age=%.0fs)", run.run_id, heartbeat_age)
            continue

        record = repository.get_run(run.run_id)
        if record is not None:
            if lifecycle.heartbeat_timeout(
                run.run_id,
                now=now,
                message="服务重启或进程退出，后台任务已中断；请重新发起运行。",
            ):
                recovered += 1
                db.expire_all()
                refreshed = db.get(RecommendationRun, run.run_id)
                if refreshed is not None:
                    refreshed.progress_phase = "interrupted"
                    if not refreshed.progress_message:
                        refreshed.progress_message = "任务未正常结束"
            continue

        # Historical rows without canonical request columns retain a projection-only
        # fallback; new rows must never bypass the state machine.
        run.status = "interrupted"
        run.interrupted_at = now
        run.finished_at = now
        run.heartbeat_at = now
        run.error_message = "服务重启或进程退出，后台任务已中断；请重新发起运行。"
        run.progress_phase = "interrupted"
        if not run.progress_message:
            run.progress_message = "任务未正常结束"
        recovered += 1
    if recovered:
        db.commit()
        invalidate_bootstrap_cache()
        for run in runs:
            if run.status == "interrupted":
                _record_progress_commit(run)
        logger.warning("marked %d stale recommendation runs as interrupted on startup", recovered)
    return recovered


def _save_checkpoint(db: Session, run_id: str, phase: str) -> None:
    """Commit the legacy checkpoint and, when present, its canonical lifecycle twin."""
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run.checkpoint_phase = phase
    run.heartbeat_at = now
    # New runs have an opaque lease and receive monotonic RunPhase checkpoints.
    # Renew in the same worker turn so a long stage can never commit after expiry.
    lifecycle_repository = SQLAlchemyRunRepository(db)
    lifecycle = LegacyWorkbenchRunCoordinator(lifecycle_repository)
    lifecycle_record = lifecycle_repository.get_run(run_id)
    if lifecycle_record is not None and lifecycle_record.lease is not None:
        if not lifecycle.renew_worker_lease(
            run_id,
            now=now,
            ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
        ):
            raise RuntimeError("Run lifecycle lease 已丢失或过期，不能提交 checkpoint")
    # Old runs keep their original resume behavior without a forced backfill.
    lifecycle.sync_checkpoint(
        run_id,
        legacy_phase=phase,
        now=now,
    )
    db.commit()
    _record_progress_commit(run)


def _should_commit_progress(
    run_id: str,
    phase: str,
    status: str,
    current: int,
    total: int,
    now: datetime,
) -> bool:
    with _progress_commit_state_lock:
        state = _progress_commit_state.get(run_id)
    if state is None:
        return True
    if state.get("phase") != phase or state.get("status") != status:
        return True
    if total > 0 and current >= total:
        return True
    last_committed_at = state.get("committed_at")
    if not isinstance(last_committed_at, datetime):
        return True
    return (now - last_committed_at).total_seconds() >= PROGRESS_COMMIT_INTERVAL_SECONDS


def _record_progress_commit(run: RecommendationRun) -> None:
    if not run.run_id:
        return
    with _progress_commit_state_lock:
        if run.status in RUN_ACTIVE_STATUSES:
            _progress_commit_state[run.run_id] = {
                "phase": run.progress_phase,
                "status": run.status,
                "committed_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }
        else:
            _progress_commit_state.pop(run.run_id, None)


def _phase_index(phase: str) -> int:
    """返回阶段在 CHECKPOINT_ORDER 中的索引，未知阶段返回 -1。"""
    try:
        return CHECKPOINT_ORDER.index(phase)
    except ValueError:
        return -1


def _persist_run_start(
    db: Session,
    run_id: str,
    params: dict[str, Any],
    selected_skills: list[dict[str, Any]],
    *,
    request: RunRequest,
    execution_version: str | None = None,
    phase: str = "prepare",
    message: str = "同步 Skill 与运行参数完成，准备拉取市场数据",
) -> RecommendationRun:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run = db.get(RecommendationRun, run_id)
    if run is None:
        run = RecommendationRun(run_id=run_id, started_at=now)
        db.add(run)
    run.preset_id = params.get("preset_id")
    run.run_name = params["run_name"]
    # The indexed column and the canonical request are written from the same
    # validated value; a later worker can therefore reject drift deterministically.
    run.run_mode = request.run_mode.value
    run.trade_date = run.trade_date or _latest_trade_date(db)
    run.finished_at = None
    run.universe_count = 0
    run.recommended_count = 0
    run.market_scope = params["market_scope"]
    run.market_scope_value = params.get("market_scope_value")
    run.risk_template = params["risk_template"]
    run.score_line = float(params["score_line"])
    run.output_count = params["output_count"]
    run.selected_skills_json = _dumps(_selected_skill_rows(selected_skills))
    # Dual-write: params_json remains a legacy projection, while these two
    # columns are the only durable request identity used by workers.
    run.params_json = _dumps(params)
    run.request_schema_version = request.schema_version
    run.request_json = request.to_canonical_json()
    if execution_version is not None:
        _persist_execution_version(run, execution_version)
    run.market_snapshot_json = run.market_snapshot_json or _dumps({})
    result_summary = {"agent_runtime": "starting"}
    existing_execution_audit = _execution_audit_from_result_summary(run.result_summary_json)
    if existing_execution_audit is not None:
        result_summary["execution"] = existing_execution_audit
    run.result_summary_json = _dumps(result_summary)
    run.filter_audit_json = run.filter_audit_json or _dumps({})
    run.status = "running"
    run.error_message = None
    run.heartbeat_at = now
    run.progress_phase = phase
    run.progress_current = 0
    run.progress_total = 0
    run.progress_message = message
    run.interrupted_at = None
    run.checkpoint_phase = phase
    assert_run_request_index_consistency(run, request)
    db.commit()
    invalidate_bootstrap_cache()
    _record_progress_commit(run)
    return run


async def run_workbench(db: Session, payload: dict[str, Any], existing_run_id: str | None = None) -> dict[str, Any]:
    run_id = existing_run_id or _new_run_id()
    is_resume = bool(existing_run_id)

    # 读取断点信息；已有 run 必须先通过 canonical request cutover guard。
    checkpoint_phase: str | None = None
    existing_run: RecommendationRun | None = None
    existing_request: RunRequest | None = None
    if is_resume:
        existing_run = db.get(RecommendationRun, run_id)
        if existing_run is None:
            raise RuntimeError(f"Run {run_id} 不存在")
        existing_request = load_canonical_run_request(existing_run)
        if existing_run.checkpoint_phase:
            checkpoint_phase = existing_run.checkpoint_phase
        logger.info("resume run %s from checkpoint %s", run_id, checkpoint_phase or "start")

    def _should_run(phase: str) -> bool:
        """断点之前的阶段跳过，从断点下一阶段开始执行。"""
        if not checkpoint_phase:
            return True
        return _phase_index(phase) > _phase_index(checkpoint_phase)

    lifecycle_repository = SQLAlchemyRunRepository(db)
    lifecycle = LegacyWorkbenchRunCoordinator(lifecycle_repository)
    if existing_run is not None:
        lifecycle_now = datetime.now(timezone.utc).replace(tzinfo=None)
        if existing_run.status == "interrupted" and not lifecycle.resume(run_id, now=lifecycle_now):
            raise RuntimeError("Run interrupted resume 缺少有效 checkpoint、存在活跃 lease 或 CAS 冲突")
        lease = lifecycle.acquire_worker_lease(
            run_id,
            now=lifecycle_now,
            ttl=timedelta(seconds=RUN_HEARTBEAT_STALE_SECONDS),
        )
        if lifecycle_repository.get_run(run_id) is not None and lease is None:
            raise RuntimeError("该 Run 已被另一个 worker 接管或缺少可用生命周期 lease")

    if not is_resume or _should_run("prepare"):
        _update_run_progress(
            db,
            existing_run_id,
            phase="prepare",
            current=0,
            total=0,
            message="同步 Skill 与运行参数",
            status="running",
        )
        skills = sync_skills(db)
        skill_map = {skill.skill_id: skill for skill in skills}
        if existing_request is not None:
            # A resumed run never re-merges caller payload/preset defaults.  The
            # persisted canonical request is the sole execution input.
            run_request = existing_request
            params = run_request.model_dump(mode="json")
            preset = db.get(Preset, run_request.preset_id) if run_request.preset_id else None
        else:
            preset = db.get(Preset, payload["preset_id"]) if payload.get("preset_id") else None
            if payload.get("preset_id") and preset is None:
                raise ValueError("预设不存在，请刷新后重试")
            params = _merge_run_params(db, preset, payload)
            run_request = _canonical_workbench_request(params, payload)
            params["run_mode"] = run_request.run_mode.value
        selected_skills = _select_skills_for_run(db, skill_map, params["skills"])
        if not selected_skills:
            raise ValueError("没有可运行的 Skill，请检查预设配置或 Skill 状态")
        execution_version, _execution_audit = _execution_version_identity(run_request, selected_skills)
        run = _persist_run_start(
            db,
            run_id,
            params,
            selected_skills,
            request=run_request,
            execution_version=execution_version,
        )
        _save_checkpoint(db, run_id, "prepare")
    else:
        # 从已有 Run 重建状态；params_json 仅保留为历史投影，不能驱动恢复。
        run = existing_run
        assert run is not None
        run_request = existing_request or load_canonical_run_request(run)
        params = run_request.model_dump(mode="json")
        skills = sync_skills(db)
        skill_map = {skill.skill_id: skill for skill in skills}
        selected_skills = _select_skills_for_run(db, skill_map, params["skills"])
        if not selected_skills:
            raise ValueError("恢复任务时没有可运行的 Skill，请检查 Skill 状态后重新发起运行")
        preset = db.get(Preset, run.preset_id) if run.preset_id else None

    # Re-read and validate the persisted canonical pair after the prepare branch.
    # This also protects direct/scheduler callers that create the row themselves.
    run_request = load_canonical_run_request(run)
    params = run_request.model_dump(mode="json")
    execution_version, execution_audit = _execution_version_identity(run_request, selected_skills)
    factor_plan_audit = execution_audit["factor_plan"]
    _persist_execution_version(run, execution_version)
    persisted_execution_audit = _execution_audit_from_result_summary(run.result_summary_json)
    if persisted_execution_audit is None:
        persisted_execution_audit = {**execution_audit, "execution_version": run.execution_version}
        result_summary = _loads(run.result_summary_json, {})
        if not isinstance(result_summary, dict):
            result_summary = {}
        result_summary["execution"] = persisted_execution_audit
        run.result_summary_json = _dumps(result_summary)
    db.commit()

    data_source = normalize_data_source(params.get("data_source"))
    trade_date_override = params.get("trade_date_override") or None
    if trade_date_override:
        logger.info("run %s using trade_date_override=%s", run_id, trade_date_override)
        # 覆盖 run 记录的 trade_date，确保推荐入库的 trade_date 与历史日期一致
        existing_run = db.get(RecommendationRun, run_id)
        if existing_run:
            existing_run.trade_date = trade_date_override
            db.commit()
    _update_run_progress(
        db,
        run_id,
        phase="market_data",
        current=0,
        total=0,
        message="拉取并校验市场股票池",
        status="running",
    )
    try:
        stocks, snapshot, market_source_audit = load_legacy_market_universe(
            db,
            data_source=data_source,
            as_of_date=trade_date_override,
        )
    except Exception as exc:
        _mark_run_failed(db, run_id, exc)
        raise
    snapshot.setdefault("breadth", {})["run_data_source_mode"] = data_source
    snapshot["breadth"]["market_source_audit"] = market_source_audit
    snapshot["breadth"]["factor_execution_plan"] = factor_plan_audit
    _update_run_progress(
        db,
        run_id,
        phase="market_data",
        current=len(stocks),
        total=len(stocks),
        message=f"股票池已就绪，共 {len(stocks)} 只",
        status="running",
    )
    _persist_market(db, snapshot)
    # 仅在实时行情模式下写回股票元数据；本地池模式数据本就从 DB 读出，写入为冗余操作，
    # 且全市场扫描时 6000+ 行批量 INSERT 会在远程 MySQL 高负载下阻塞推荐管道。
    if data_source == DATA_SOURCE_REALTIME:
        _persist_stocks_and_bars(db, stocks, persist_bars=True)
    elif data_source == DATA_SOURCE_LOCAL_POOL_REALTIME:
        _persist_stocks_and_bars(db, stocks, persist_bars=True)
    else:
        logger.info("skip stock/bar persistence for local-pool run %s; mode=%s stocks=%d", run_id, data_source, len(stocks))
    scoped_stocks = _apply_market_scope_to_stocks(db, stocks, params)
    if not scoped_stocks:
        raise ValueError("当前市场范围内没有可运行的股票，请调整预设范围后重试")
    snapshot.setdefault("breadth", {})["scoped_candidate_count"] = len(scoped_stocks)
    # Prefilter uses current state plus active event pointers through the same S4
    # application boundary.  This is pointer-only/current-state loading: no network.
    prefilter_auxiliary = load_auxiliary_feature_views(
        db,
        [str(stock.get("code") or "") for stock in scoped_stocks if stock.get("code")],
        trade_date=str(snapshot.get("trade_date") or ""),
    )
    prefilter_hits = inject_auxiliary_prefilter_flags(scoped_stocks, prefilter_auxiliary)
    if prefilter_hits:
        logger.info("prefilter 前注入 S4 auxiliary state: %d field hits (run=%s)", prefilter_hits, run_id)
    min_prefilter_count = resolve_prefilter_target_count(params, selected_skill_count=len(selected_skills))
    prefiltered_stocks, prefilter_audit = apply_prefilters(
        scoped_stocks,
        selected_skills,
        min_candidate_count=min_prefilter_count,
    )
    if not prefiltered_stocks:
        raise ValueError("前置预过滤后没有可运行的股票，请放宽候选条件后重试")
    snapshot["breadth"]["prefilter_candidate_count"] = len(prefiltered_stocks)
    snapshot["breadth"]["prefilter_audit"] = _strip_audit_rows(prefilter_audit)
    _save_checkpoint(db, run_id, "scope_filter")
    _update_run_progress(
        db,
        run_id,
        phase="scope_filter",
        current=len(prefiltered_stocks),
        total=len(stocks),
        message=f"市场范围与前置预过滤完成，候选 {len(prefiltered_stocks)} 只",
        status="running",
    )

    # S4 owns one application/composition boundary for all frozen auxiliary
    # descriptors.  ``skip_aux_refresh`` is deliberately pointer/current-state
    # loading only; it never acquires a provider or force-refreshes missing rows.
    skip_aux_refresh = bool(params.get("skip_aux_refresh"))
    auxiliary_codes = [str(stock.get("code") or "") for stock in prefiltered_stocks if stock.get("code")]
    _update_run_progress(
        db,
        run_id,
        phase="auxiliary_data",
        current=0,
        total=18,
        message="通过 S4 registry 准备 18 个辅助数据集",
        status="running",
    )
    auxiliary_result = run_auxiliary_application(
        db,
        instruments=auxiliary_codes,
        trade_date=str(snapshot["trade_date"]),
        refresh=not skip_aux_refresh,
    )
    _update_run_progress(
        db,
        run_id,
        phase="auxiliary_data",
        current=18,
        total=18,
        message="S4 auxiliary registry 已完成当前状态/活动快照处理",
        status="running",
    )
    snapshot.setdefault("breadth", {})["auxiliary_data_audit"] = dict(auxiliary_result.audit_payload)
    snapshot["breadth"]["auxiliary_orchestration"] = {
        "usable": auxiliary_result.orchestrated.is_usable,
        "inventory": tuple(item.dataset_key for item in auxiliary_result.orchestrated.results),
        "datasets": dict(auxiliary_result.audit_payload),
        "skip_aux_refresh": skip_aux_refresh,
    }
    _save_checkpoint(db, run_id, "auxiliary_data")

    _update_run_progress(
        db,
        run_id,
        phase="feature_build",
        current=0,
        total=len(prefiltered_stocks),
        message=f"构建 {len(prefiltered_stocks)} 只股票的特征与研究上下文",
        status="running",
    )
    features = build_features(
        prefiltered_stocks, snapshot, _load_calibrated_dsa_weights(db), db=db,
        on_progress=lambda current, total: _update_run_progress(
            db, run_id, phase="feature_build",
            current=current, total=total,
            message=f"构建特征 {current}/{total}",
            status="running",
        ),
    )
    research_contexts = build_research_contexts(features, snapshot)
    _save_checkpoint(db, run_id, "feature_build")

    run = db.get(RecommendationRun, run_id)
    if run is None:
        run = RecommendationRun(run_id=run_id)
        db.add(run)
    run.preset_id = params.get("preset_id")
    run.run_name = params["run_name"]
    run.run_mode = run_request.run_mode.value
    run.trade_date = snapshot["trade_date"]
    if not run.started_at:
        run.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run.finished_at = None
    run.universe_count = len(research_contexts)
    run.recommended_count = 0
    run.market_scope = params["market_scope"]
    run.market_scope_value = params.get("market_scope_value")
    run.risk_template = params["risk_template"]
    run.score_line = float(params["score_line"])
    run.output_count = params["output_count"]
    run.selected_skills_json = _dumps(_selected_skill_rows(selected_skills))
    run.params_json = _dumps(params)
    # Keep the legacy projection for historical queries, but always refresh the
    # canonical pair from the same validated RunRequest used by execution.
    run.request_schema_version = run_request.schema_version
    run.request_json = run_request.to_canonical_json()
    run.market_snapshot_json = _dumps(snapshot)
    run.result_summary_json = _dumps({"execution": persisted_execution_audit})
    run.status = "running"
    run.error_message = None
    run.heartbeat_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run.progress_phase = "feature_build"
    run.progress_current = len(research_contexts)
    run.progress_total = len(research_contexts)
    run.progress_message = f"特征构建完成，进入规则扫描：{len(research_contexts)} 只"
    run.interrupted_at = None
    assert_run_request_index_consistency(run, run_request)
    db.flush()
    db.commit()  # Persist immediately so the run record survives failures
    invalidate_bootstrap_cache()

    agent_session_id = f"sess_{run_id}"
    all_skill_objs = [s["skill"] for s in selected_skills]
    # 策略分发只经 descriptor-backed registry：命令式与声明式实现都先由同一
    # bootstrap 装配，再统一调用 `registry.get(skill_id).evaluate(stock, ctx)`。
    # Adapter 负责把命令式 StrategyDecision 投影为规范 Signal，调用方不得读取
    # 实现类身份字段或绕过 manifest/Descriptor。
    strategy_registry = build_registry_from_skills(all_skill_objs)

    try:
        AgentSessionRegistry.register(
            session_id=agent_session_id,
            session_factory=lambda: SessionLocal(),
            skills=all_skill_objs,
            snapshot=snapshot,
        )

        recent_codes = _recent_recommended_codes(db, snapshot["trade_date"])
        same_day_codes = _same_day_recommended_codes(db, snapshot["trade_date"], run.run_id)
        same_day_industry_counts = _same_day_industry_counts(db, snapshot["trade_date"], run.run_id)
        market_score = market_regime_score(snapshot)
        calibrated_weights = _load_calibrated_agg_weights(db)
        cards: list[dict[str, Any]] = []

        # ═══ 阶段一：策略规则全市场扫描（所有基础过滤合格股票，无 LLM） ═══
        settings = get_settings()
        agent_review_disabled = bool(params.get("disable_agent_review"))
        skill_review_limit = resolve_skill_review_limit(params, settings, selected_skill_count=len(selected_skills))
        skill_review_concurrency = max(1, int(params.get("agent_review_concurrency") or settings.market_skill_review_concurrency or 1))
        prelim_entries: list[dict[str, Any]] = []
        total_contexts = len(research_contexts)
        _update_run_progress(
            db,
            run_id,
            phase="rule_scan",
            current=0,
            total=total_contexts,
            message=f"开始规则扫描：{total_contexts} 只",
            status="running",
        )

        for i, context in enumerate(research_contexts, start=1):
            if i % 50 == 0:
                await asyncio.sleep(0)
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
                    ctx = StrategyContext(main_industries=snapshot["main_industries"], skip_universe_check=skip_universe)
                    signal = strategy_registry.get(skill_obj.skill_id).evaluate(stock, ctx) if skill_obj.executable else None
                rule_signals_by_skill[skill_obj.skill_id] = signal

            prelim_entries.append(
                {
                    "prelim_score": _prelim_rule_score(stock, rule_signals_by_skill, snapshot),
                    "context": context,
                    "rule_signals": rule_signals_by_skill,
                    "stock": stock,
                }
            )
            if i == 1 or i % 100 == 0 or i == total_contexts:
                _update_run_progress(
                    db,
                    run_id,
                    phase="rule_scan",
                    current=i,
                    total=total_contexts,
                    message=f"规则扫描 {i}/{total_contexts}",
                    status="running",
                )

        prelim_entries.sort(key=lambda item: float(item.get("prelim_score") or 0), reverse=True)
        gate_result = build_skill_rule_gate(prelim_entries, selected_skills, settings)
        skill_candidates = gate_result["skill_candidates"]
        non_skill_entries = gate_result["non_skill_entries"]
        explicit_review_limit_skipped = 0
        if skill_review_limit > 0 and len(skill_candidates) > skill_review_limit:
            explicit_review_limit_skipped = len(skill_candidates) - skill_review_limit
            non_skill_entries = skill_candidates[skill_review_limit:] + non_skill_entries
            skill_candidates = skill_candidates[:skill_review_limit]
            skill_demo_limited = True
        else:
            skill_demo_limited = False
        skill_gate_audit = finalize_skill_gate_audit(
            gate_result["audit"],
            skill_candidates,
            explicit_review_limit_skipped=explicit_review_limit_skipped,
        )
        if agent_review_disabled:
            skill_gate_audit["agent_review_disabled"] = True
            skill_gate_audit["skill_agent_call_count"] = 0
            for stats in (skill_gate_audit.get("per_skill") or {}).values():
                stats["llm_reviewed"] = 0
        skill_agent_call_count = (
            0
            if agent_review_disabled
            else sum(len(entry.get("review_skill_ids") or []) for entry in skill_candidates)
        )

        pipeline_audit = _build_pipeline_audit(
            snapshot=snapshot,
            params=params,
            total_contexts=len(research_contexts),
            prelim_count=len(prelim_entries),
            skill_input_count=len(skill_candidates),
            skill_agent_call_count=skill_agent_call_count,
            skill_gate_audit=skill_gate_audit,
            skill_review_limit=skill_review_limit,
            skill_review_concurrency=skill_review_concurrency,
            skill_demo_limited=skill_demo_limited,
            prefilter_audit=prefilter_audit,
        )
        pipeline_audit["agent_review_disabled"] = agent_review_disabled
        snapshot.setdefault("breadth", {})["recommendation_pipeline"] = _strip_audit_rows(pipeline_audit)
        _save_checkpoint(db, run_id, "rule_scan")
        _update_run_progress(
            db,
            run_id,
            phase="skill_gate",
            current=len(skill_candidates),
            total=len(prelim_entries),
            message=(
                f"规则门完成，Agent 深审已禁用，进入规则聚合 {len(skill_candidates)} 只"
                if agent_review_disabled
                else f"规则门完成，进入 Skill 深审 {len(skill_candidates)} 只"
            ),
            status="running",
        )

        # ═══ 阶段二：Agent / Skill 深度审查（显式扫描集） ═══
        _save_checkpoint(db, run_id, "skill_gate")
        semaphore = asyncio.Semaphore(skill_review_concurrency)

        async def _process_candidate(entry):
            stock = entry["stock"]
            review_skill_ids = set(entry.get("review_skill_ids") or [])
            review_skills = [
                selected["skill"]
                for selected in selected_skills
                if selected["skill_id"] in review_skill_ids
            ]
            async with semaphore:
                skill_reviews = await run_skill_reviews(
                    stock=stock,
                    skills=review_skills,
                    snapshot=snapshot,
                    rule_signals_by_skill=entry["rule_signals"],
                    session_id=agent_session_id,
                )
            return {
                **entry,
                "skill_reviews": skill_reviews,
            }

        candidate_results: list[dict[str, Any]] = []
        review_total = len(skill_candidates)
        _update_run_progress(
            db,
            run_id,
            phase="skill_review",
            current=review_total if agent_review_disabled else 0,
            total=review_total,
            message=(
                f"Skill 深审已按参数禁用，使用规则预审聚合 {review_total} 只"
                if agent_review_disabled
                else f"Skill 深审启动：{review_total} 只，最多并发 {skill_review_concurrency}"
            ),
            status="running",
        )
        if agent_review_disabled:
            candidate_results = [
                {
                    **entry,
                    "review_skill_ids": [],
                    "agent_review_disabled": True,
                    "skill_reviews": [],
                }
                for entry in skill_candidates
            ]
        elif skill_candidates:
            tasks = [asyncio.create_task(_process_candidate(entry)) for entry in skill_candidates]
            for i, task in enumerate(asyncio.as_completed(tasks), start=1):
                candidate_results.append(await task)
                _update_run_progress(
                    db,
                    run_id,
                    phase="skill_review",
                    current=i,
                    total=review_total,
                    message=f"Skill 深审 {i}/{review_total}",
                    status="running",
                )

        # 将 Agent API 调用成本写入数据库
        from app.agents.agno_skill_agent import drain_api_costs
        from app.services.cost_service import record_api_call
        _save_checkpoint(db, run_id, "skill_review")
        api_costs = drain_api_costs()
        for cost in api_costs:
            record_api_call(db, run_id=run_id, **cost)

        # ═══ 阶段三：聚合（Skill 输入集有 Agent 审查，非输入集仅作显式降权补充） ═══
        _update_run_progress(
            db,
            run_id,
            phase="aggregate",
            current=0,
            total=max(len(candidate_results), 1),
            message="聚合 Skill 结论并生成候选卡片",
            status="running",
        )
        # 处理 Skill 输入集（有 Agent 审查）— 这些是主力推荐
        candidate_success_count = 0
        for entry in candidate_results:
            skill_reviews = entry.get("skill_reviews") or []
            card = _build_recommendation_card(
                entry,
                skill_reviews,
                skill_map=skill_map,
                selected_skills=selected_skills,
                market_score=market_score,
                risk_template=params["risk_template"],
                calibrated_weights=calibrated_weights,
                snapshot=snapshot,
                recent_codes=recent_codes,
                same_day_codes=same_day_codes,
            )
            if card.get("agent_logs"):
                candidate_success_count += 1
            if _drop_as_repeat_card(
                card,
                params,
                recent_repeat=bool(card.get("recent_repeat")),
                same_day_repeat=bool(card.get("same_day_repeat")),
            ):
                continue
            cards.append(card)

        logger.info(
            "Agent review: %d/%d skill inputs got at least one successful review",
            candidate_success_count, len(candidate_results),
        )
        pipeline_audit["skill_review_success_count"] = candidate_success_count
        review_failure_audit = _skill_review_failure_audit(candidate_results, api_costs)
        if review_failure_audit["failed_count"]:
            pipeline_audit["skill_review_failure_audit"] = review_failure_audit
        pipeline_audit["rule_only_fallback_count"] = 0
        scored_cards = list(cards)
        pool_result = apply_pool_rules(
            scored_cards,
            output_count=int(params["output_count"]),
            score_line=float(params["score_line"]),
            market_snapshot=snapshot,
            risk_template=params["risk_template"],
            return_watchlist=True,
            same_day_industry_counts=same_day_industry_counts,
            # 配置级降级信号：未配置任何有效模型路由（自带 key）时系统整体降级，
            # 高潮/强牛阶段也启用软降线兜底，保证「每天可见推荐」。
            # 注意：基于数据库 model_route_config 表，不再依赖已弃用的全局 env key。
            agent_available=has_active_model_routes(),
        )
        if isinstance(pool_result, dict):
            cards = pool_result.get("recommendations", [])
            climax_watchlist = pool_result.get("watchlist", [])
        else:
            cards = pool_result
            climax_watchlist = []
        pipeline_audit["filter_audit"] = _build_filter_audit(
            prefilter_audit=prefilter_audit,
            skill_gate_audit=skill_gate_audit,
            scored_cards=scored_cards,
            recommended_cards=cards,
            pipeline=pipeline_audit,
        )
        _save_checkpoint(db, run_id, "aggregate")
        _update_run_progress(
            db,
            run_id,
            phase="persist",
            current=0,
            total=len(cards),
            message=f"候选池裁剪完成，开始保存 {len(cards)} 条推荐",
            status="running",
        )
        saved = persist_recommendation_cards(
            db,
            run_id=run_id,
            trade_date=snapshot["trade_date"],
            cards=cards,
            dump_json=_dumps,
            on_progress=lambda index, total: _update_run_progress(
                db,
                run_id,
                phase="persist",
                current=index,
                total=total,
                message=f"保存推荐 {index}/{total}",
                status="running",
                commit=False,
            ),
        )

        _save_checkpoint(db, run_id, "persist")

        run.recommended_count = len(saved)
        completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        managed_record = lifecycle_repository.get_run(run_id)
        if managed_record is not None:
            if managed_record.lease is None:
                raise RuntimeError("Run lifecycle completion requires an active worker lease")
            db.flush()
            if not lifecycle.complete(run_id, now=completed_at):
                raise RuntimeError("Run lifecycle completion CAS failed")
            db.expire_all()
            run = db.get(RecommendationRun, run_id)
            if run is None:
                raise RuntimeError("Run disappeared during lifecycle completion")
        else:
            # Historical/direct runs without canonical lifecycle columns retain the
            # old projection-only completion path.
            run.finished_at = completed_at
            run.status = "completed"
            run.heartbeat_at = completed_at
        run.progress_phase = "completed"
        run.progress_current = len(saved)
        run.progress_total = len(saved)
        run.progress_message = "运行完成"
        run.filter_audit_json = _dumps(pipeline_audit.get("filter_audit") or {})
        summary = _build_run_summary(cards, saved, snapshot, _strip_audit_rows(pipeline_audit))
        summary["execution"] = persisted_execution_audit
        summary["score_line_meta"] = resolve_score_line_context(
            float(params["score_line"]),
            market_snapshot=snapshot,
            risk_template=params["risk_template"],
        )
        summary["top_candidate_score"] = round(
            max((float(c.get("final_score") or 0) for c in scored_cards), default=0.0), 2
        )
        if climax_watchlist:
            summary["climax_watchlist"] = [
                {
                    "code": c.get("code"),
                    "name": c.get("name"),
                    "industry": c.get("industry"),
                    "final_score": c.get("final_score"),
                    "effective_score_line": (c.get("score_line_meta") or {}).get("effective_score_line"),
                    "watchlist_gap": c.get("watchlist_gap"),
                    "hit_strategies": c.get("watchlist_hit_strategies"),
                    "note": c.get("watchlist_note"),
                }
                for c in climax_watchlist
            ]
        if params.get("matrix_batch_id"):
            summary["matrix_batch_id"] = params.get("matrix_batch_id")
            summary["matrix_batch_name"] = params.get("matrix_batch_name") or "盘后预设矩阵"
            summary["matrix_run"] = True
        run.result_summary_json = _dumps(summary)
        db.commit()
        invalidate_bootstrap_cache()
        return {
            "run": _serialize_run_summary(db, run),
            "recommendations": [_serialize_recommendation(db, rec) for rec in saved],
        }
    except Exception as exc:
        failed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        managed_record = lifecycle_repository.get_run(run_id)
        if managed_record is not None:
            if not lifecycle.fail(run_id, now=failed_at, message=str(exc)):
                db.rollback()
                logger.warning("run failure CAS lost for %s; refusing direct status overwrite", run_id)
                raise
            db.expire_all()
            run = db.get(RecommendationRun, run_id) or run
            # Lifecycle status, error code, timestamp and durable lease cleanup are
            # owned by the CAS.  Only the legacy progress projection is updated.
            run.progress_phase = "failed"
            run.progress_message = str(exc)
        else:
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = failed_at
            run.heartbeat_at = failed_at
            run.progress_phase = "failed"
            run.progress_message = str(exc)
        db.commit()
        invalidate_bootstrap_cache()
        raise
    finally:
        AgentSessionRegistry.unregister(agent_session_id)


def get_recommendation_detail(db: Session, recommendation_id: str) -> dict[str, Any] | None:
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    data = _serialize_recommendation(db, rec)
    run = db.get(RecommendationRun, rec.run_id)
    data["run"] = _serialize_run_summary(db, run) if run else None
    outcome = db.get(RecommendationOutcome, rec.recommendation_id)
    data["outcome"] = _serialize_outcome(outcome)
    data["timeline"] = _build_timeline(db, rec)
    logs = db.scalars(select(AgentDecisionLog).where(AgentDecisionLog.recommendation_id == recommendation_id)).all()
    data["agent_logs"] = [
        {"agent_name": log.agent_name, "output": _loads(log.output_json, {}), "created_at": log.created_at.isoformat()}
        for log in logs
    ]
    review = db.scalar(select(ReviewNote).where(ReviewNote.recommendation_id == recommendation_id))
    if review:
        data["review_note"] = {
            "user_label": review.user_label,
            "failure_reason": review.failure_reason,
            "review_text": review.review_text,
            "updated_at": review.updated_at.isoformat(),
        }
    data["history"] = _historical_recommendations(db, rec.code, rec.recommendation_id)
    data["replay_summary"] = _build_replay_summary(data)
    # 北极星 B 项：该推荐所属策略的历史战绩（样本/胜率/盈亏比/最近命中日）
    data["strategy_perf"] = strategy_perf_summary(db, rec.bucket or "")
    return data


def get_run_filter_audit(db: Session, run_id: str) -> dict[str, Any] | None:
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return None
    summary = _loads(run.result_summary_json, {})
    snapshot = _loads(run.market_snapshot_json, {})
    pipeline = (
        summary.get("pipeline")
        or ((snapshot.get("breadth") or {}).get("recommendation_pipeline") if snapshot else {})
        or {}
    )
    audit = _loads(getattr(run, "filter_audit_json", "") or "{}", {})
    if not audit:
        audit = pipeline.get("filter_audit") if isinstance(pipeline, dict) else None
    if not audit:
        audit = _fallback_filter_audit(summary, pipeline if isinstance(pipeline, dict) else {}, run.recommended_count)
    audit = _upgrade_filter_audit(audit or {})
    skill_review_failure_audit = (
        (pipeline.get("skill_review_failure_audit") if isinstance(pipeline, dict) else None)
        or _skill_review_failure_audit_from_logs(db, run_id)
    )
    if skill_review_failure_audit.get("failed_count"):
        _apply_skill_review_failure_audit_to_stage_summary(
            audit.get("stage_summary") or [],
            skill_review_failure_audit,
        )
    return {
        "run": _serialize_run_summary(db, run),
        "stage_summary": audit.get("stage_summary", []),
        "distributions": audit.get("distributions", {}),
        "reason_counts": audit.get("reason_counts", {}),
        "rows": audit.get("rows", []),
        "empty_reason": audit.get("empty_reason"),
        "skill_review_failure_audit": skill_review_failure_audit if skill_review_failure_audit.get("failed_count") else {},
    }


def set_watch_status(db: Session, recommendation_id: str, watch_status: str, note: str | None = None) -> dict[str, Any] | None:
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    item = db.scalar(select(WatchItem).where(WatchItem.source_recommendation_id == recommendation_id))
    if watch_status == "none":
        if item is not None:
            db.delete(item)
            db.commit()
            invalidate_bootstrap_cache()
        return {"recommendation_id": recommendation_id, "watch_status": "none"}
    if item is None:
        item = WatchItem(
            watch_id=f"watch_{uuid.uuid4().hex}",
            code=rec.code,
            source_run_id=rec.run_id,
            source_recommendation_id=recommendation_id,
            watch_status=watch_status,
            note=note,
        )
        db.add(item)
    else:
        item.watch_status = watch_status
        item.note = note
        item.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    invalidate_bootstrap_cache()
    return {"recommendation_id": recommendation_id, "watch_status": watch_status, "note": note}


def save_review_note(db: Session, recommendation_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    note = db.scalar(select(ReviewNote).where(ReviewNote.recommendation_id == recommendation_id))
    if note is None:
        note = ReviewNote(
            note_id=f"note_{uuid.uuid4().hex}",
            recommendation_id=recommendation_id,
            user_label=payload.get("user_label"),
            failure_reason=payload.get("failure_reason"),
            review_text=payload.get("review_text"),
        )
        db.add(note)
    else:
        note.user_label = payload.get("user_label")
        note.failure_reason = payload.get("failure_reason")
        note.review_text = payload.get("review_text")
        note.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    invalidate_bootstrap_cache()
    return {
        "recommendation_id": recommendation_id,
        "user_label": note.user_label,
        "failure_reason": note.failure_reason,
        "review_text": note.review_text,
    }


def label_outcome_manually(db: Session, recommendation_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        return None
    outcome = db.get(RecommendationOutcome, recommendation_id)
    if outcome is None:
        outcome = RecommendationOutcome(recommendation_id=recommendation_id)
        db.add(outcome)
    outcome_label = str(payload.get("outcome_label") or "pending").lower()
    if outcome_label == "not_triggered":
        outcome_label = "missed"
    if outcome_label == "risk_hit":
        outcome_label = "failed"
    outcome.outcome_label = outcome_label
    outcome.entry_triggered = outcome_label in {"success", "failed"}
    outcome.outcome_reason = payload.get("outcome_reason")
    outcome.horizon_days = int(payload.get("horizon_days") or outcome.horizon_days or 5)
    outcome.label_source = "manual"
    outcome.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rec.status = normalized_outcome_label(outcome)
    db.commit()
    invalidate_outcome_metrics_cache()
    invalidate_bootstrap_cache()
    return _serialize_outcome(outcome)


def label_pending_outcomes(db: Session, horizon_days: int = 5) -> dict[str, Any]:
    result = label_pending_outcomes_service(db, horizon_days=horizon_days)
    labeled = int(result.get("labeled") or 0)
    if labeled:
        invalidate_bootstrap_cache()
    return {"labeled": labeled}





def _new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _latest_trade_date(db: Session) -> str:
    latest = db.scalar(select(MarketSnapshot.trade_date).order_by(MarketSnapshot.trade_date.desc()).limit(1))
    if latest:
        return str(latest)
    latest_quote = db.scalar(
        select(StockQuote.trade_date)
        .where(StockQuote.freshness_status != "inactive")
        .order_by(StockQuote.trade_date.desc())
        .limit(1)
    )
    if latest_quote:
        return str(latest_quote)
    latest_bar = db.scalar(select(DailyKbar.trade_date).order_by(DailyKbar.trade_date.desc()).limit(1))
    if latest_bar:
        return str(latest_bar)
    return date.today().isoformat()


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


def _serialize_run_summaries_batched(db: Session, runs: list[RecommendationRun]) -> list[dict[str, Any]]:
    """批量序列化 Run 摘要，避免 N+1 查询。"""
    if not runs:
        return []
    run_ids = [run.run_id for run in runs]
    preset_ids = list({run.preset_id for run in runs if run.preset_id})

    # 批量加载 Preset
    preset_map: dict[str, Preset] = {}
    if preset_ids:
        preset_map = {p.preset_id: p for p in db.scalars(select(Preset).where(Preset.preset_id.in_(preset_ids))).all()}

    # 批量加载所有 run 对应的 Recommendation
    all_recs = db.scalars(select(Recommendation).where(Recommendation.run_id.in_(run_ids))).all()
    recs_by_run: dict[str, list[Recommendation]] = {}
    for rec in all_recs:
        recs_by_run.setdefault(rec.run_id, []).append(rec)

    # 批量加载所有 Outcome
    all_rec_ids = [rec.recommendation_id for rec in all_recs]
    outcome_map: dict[str, RecommendationOutcome] = {}
    if all_rec_ids:
        all_outcomes = db.scalars(
            select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_(all_rec_ids))
        ).all()
        for o in all_outcomes:
            outcome_map[o.recommendation_id] = o

    results = []
    for run in runs:
        preset = preset_map.get(run.preset_id) if run.preset_id else None
        run_recs = recs_by_run.get(run.run_id, [])
        run_outcomes = [
            outcome
            for rec in run_recs
            for outcome in [outcome_map.get(rec.recommendation_id)]
            if outcome is not None and is_trade_executed(outcome)
        ]
        success_count = sum(1 for outcome in run_outcomes if is_accurate_recommendation(outcome))
        hit_rate = round(success_count / len(run_outcomes), 3) if run_outcomes else 0.0
        results.append(_serialize_run_summary_from_cache(run, preset, hit_rate))
    return results


def _serialize_run_summary_from_cache(run: RecommendationRun, preset: Preset | None, hit_rate: float) -> dict[str, Any]:
    return _project_run_summary(
        run,
        preset,
        hit_rate,
        active_statuses=RUN_ACTIVE_STATUSES,
        heartbeat_stale_seconds=RUN_HEARTBEAT_STALE_SECONDS,
    )


def _serialize_run_summary(db: Session, run: RecommendationRun) -> dict[str, Any]:
    preset = db.get(Preset, run.preset_id) if run.preset_id else None
    outcome_ids = [rec.recommendation_id for rec in db.scalars(select(Recommendation).where(Recommendation.run_id == run.run_id)).all()]
    outcomes = (
        db.scalars(select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_(outcome_ids))).all()
        if outcome_ids
        else []
    )
    executed = [outcome for outcome in outcomes if is_trade_executed(outcome)]
    success_count = sum(1 for outcome in executed if is_accurate_recommendation(outcome))
    hit_rate = round(success_count / len(executed), 3) if executed else 0.0
    return _project_run_summary(
        run,
        preset,
        hit_rate,
        active_statuses=RUN_ACTIVE_STATUSES,
        heartbeat_stale_seconds=RUN_HEARTBEAT_STALE_SECONDS,
    )


def _serialize_recommendations_batched(db: Session, recs: list[Recommendation]) -> list[dict[str, Any]]:
    """批量序列化推荐，避免 N+1 WatchItem / Run 查询。"""
    if not recs:
        return []
    rec_ids = [rec.recommendation_id for rec in recs]
    watch_map: dict[str, WatchItem] = {}
    watches = db.scalars(select(WatchItem).where(WatchItem.source_recommendation_id.in_(rec_ids))).all()
    for w in watches:
        watch_map[w.source_recommendation_id] = w
    # 批量加载 run 信息（含 preset_name）
    run_ids = list({rec.run_id for rec in recs})
    run_map: dict[str, RecommendationRun] = {}
    preset_map: dict[str, str] = {}
    if run_ids:
        runs = db.scalars(select(RecommendationRun).where(RecommendationRun.run_id.in_(run_ids))).all()
        for r in runs:
            run_map[r.run_id] = r
        preset_ids = list({r.preset_id for r in runs if r.preset_id})
        if preset_ids:
            presets = db.scalars(select(Preset).where(Preset.preset_id.in_(preset_ids))).all()
            for p in presets:
                preset_map[p.preset_id] = p.name
    return [
        _serialize_recommendation_cached(
            rec, watch_map.get(rec.recommendation_id),
            run_map.get(rec.run_id),
            preset_map.get(run_map[rec.run_id].preset_id) if rec.run_id in run_map and run_map[rec.run_id].preset_id else None,
        )
        for rec in recs
    ]


def _serialize_recommendation_cached(
    rec: Recommendation, watch: WatchItem | None, run: RecommendationRun | None = None,
    preset_name: str | None = None,
) -> dict[str, Any]:
    return _project_recommendation(rec, watch, run, preset_name)


def _serialize_recommendation(db: Session, rec: Recommendation) -> dict[str, Any]:
    watch = db.scalar(select(WatchItem).where(WatchItem.source_recommendation_id == rec.recommendation_id))
    return _project_recommendation(rec, watch)


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


def _build_recommendation_card(
    entry: dict[str, Any],
    skill_reviews: list[dict[str, Any]],
    *,
    skill_map: dict[str, StrategySkill],
    selected_skills: list[dict[str, Any]],
    market_score: float,
    risk_template: str,
    calibrated_weights: dict[str, float] | None,
    snapshot: dict[str, Any],
    recent_codes: set[str] | None = None,
    same_day_codes: set[str] | None = None,
) -> dict[str, Any]:
    context = entry.get("context")
    stock = entry["stock"]
    rule_signals_by_skill = entry["rule_signals"]
    review_skill_ids = set(entry.get("review_skill_ids") or [])
    selected_skill_lookup = {str(item["skill_id"]): item for item in selected_skills}
    signals = []

    for review_result in skill_reviews:
        skill_id = str(review_result["skill_id"])
        review_data = review_result.get("review")
        hc = review_result.get("hallucination_check")
        rule_signal = rule_signals_by_skill.get(skill_id)
        is_agent_fallback = False

        if review_data is None and rule_signal is not None:
            is_agent_fallback = True
            prelim_score = float(entry.get("prelim_score") or rule_signal.get("score") or 50.0)
            fallback_review = {
                "stance": "support" if prelim_score >= 72 else "neutral_support" if prelim_score >= 60 else "neutral",
                "score_delta": round(max(-18.0, min(16.0, prelim_score - 50.0)), 2),
                "score": round(prelim_score, 2),
                "confidence": 0.35 if prelim_score >= 70 else 0.25,
                "supports": [f"规则预审分 {prelim_score:.1f}"],
                "risks": [str(review_result.get("error") or "Skill Agent 暂不可用，已降级为规则预审")],
                "summary": "Skill Agent 暂不可用，本次仅保留规则预审结果",
                "market_regime_judgment": "规则预审降级模式",
                "action": "观察" if prelim_score < 72 else "轻仓试错",
                "position_range": "0-5%" if prelim_score < 72 else "5-15%",
                "entry_style": "先看承接与分歧修复",
                "invalidation_conditions": [
                    "若规则预审未能转强，继续观察",
                    "若主线和承接没有改善，撤回降级推荐",
                ],
                "next_day_plan": [
                    "等待 Agent 恢复后重新复核",
                    "仅在主线继续强化时跟踪承接变化",
                ],
                "summary_is_warning": True,
                "runtime": "agent_skill_fallback_prelim",
            }
            review_data = fallback_review

        agent_review = {
            "stance": review_data.get("stance", "neutral"),
            "score_delta": review_data.get("score_delta", review_data.get("score", 50) - 50),
            "score": review_data.get("score", 50),
            "confidence": review_data.get("confidence", 0.5),
            "supports": review_data.get("supports") or review_data.get("evidence", []),
            "risks": review_data.get("risks", []),
            "summary": review_data.get("summary", ""),
            "market_regime_judgment": review_data.get("market_regime_judgment", ""),
            "action": review_data.get("action", "观察"),
            "position_range": review_data.get("position_range", "0-5%"),
            "entry_style": review_data.get("entry_style", ""),
            "invalidation_conditions": review_data.get("invalidation_conditions", []),
            "next_day_plan": review_data.get("next_day_plan", []),
            "runtime": review_data.get("runtime", "agent_skill_agno"),
        } if review_data else {
            "stance": "neutral",
            "score_delta": 0,
            "score": 50,
            "confidence": 0.3,
            "supports": [],
            "risks": [str(review_result.get("error") or "Skill Agent 未返回有效审查，已按中性降级处理")],
            "summary": str(review_result.get("error") or "Skill Agent 未返回有效审查，已按中性降级处理"),
            "action": "不做",
            "position_range": "0%",
            "entry_style": "当前不设买点",
            "invalidation_conditions": [str(review_result.get("error") or "等待数据恢复后再观察")],
            "next_day_plan": ["等待更强主线或 Agent 审查恢复后再评估"],
            "runtime_note": "agent_review_failed",
        }

        if rule_signal is None:
            skill_obj = skill_map.get(skill_id)
            if skill_obj is None:
                continue
            signal = agent_only_signal(skill_obj, stock, agent_review)
        else:
            signal = enrich_signal_with_agent_review(rule_signal, agent_review)

        if is_agent_fallback:
            signal["runtime"] = "strategy_yaml_rule_only_agent_unavailable"
            signal["agent_review_unavailable"] = {
                "reason": str(review_result.get("error") or "Skill Agent 暂不可用"),
                "runtime_note": "agent_review_unavailable",
            }

        if hc:
            signal["hallucination_flags"] = hc.get("flags", [])
            signal["hallucination_severity"] = hc.get("severity", "low")

        selected = selected_skill_lookup.get(skill_id)
        if selected is not None:
            signal["weight"] = selected["weight"]
        signals.append(signal)

    for selected in selected_skills:
        skill_id = str(selected["skill_id"])
        if skill_id in review_skill_ids:
            continue
        rule_signal = rule_signals_by_skill.get(skill_id)
        if not rule_signal:
            continue
        signal = dict(rule_signal)
        signal["weight"] = selected["weight"]
        signal["runtime"] = signal.get("runtime") or "strategy_yaml_rule_only"
        if not signal.get("hard_veto"):
            reason = ((entry.get("skill_gate_reasons") or {}).get(skill_id) or "规则门未进入 Agent 深审")
            signal["agent_review_skipped"] = {
                "reason": reason,
                "runtime_note": "skill_rule_gate_skipped",
            }
        signals.append(signal)

    valid_reviews = [r for r in skill_reviews if r.get("review") and not r.get("error")]
    if valid_reviews:
        scores_list = [r["review"]["score"] for r in valid_reviews]
        confidences_list = [r["review"]["confidence"] for r in valid_reviews]
        agent_review_score = sum(s * c for s, c in zip(scores_list, confidences_list)) / max(sum(confidences_list), 0.01)
    else:
        agent_review_score = 50.0

    review_log = {
        "agent_review_score": round(agent_review_score, 2),
        "logs": [
            {"agent_name": f"Skill Agent · {r.get('skill_name', 'Unknown')}", "output": r.get("review") or {}}
            for r in skill_reviews
            if r.get("review")
        ],
    }

    card = aggregate(
        stock=stock,
        signals=signals,
        market_score=market_score,
        agent_review_score=review_log["agent_review_score"],
        risk_template=risk_template,
        calibrated_weights=calibrated_weights if calibrated_weights else None,
        market_snapshot=snapshot,
    )
    feature_snapshot = context.to_feature_snapshot() if context is not None else dict(stock)
    feature_snapshot["component_scores"] = card.get("component_scores", {})
    feature_snapshot["data_authenticity"] = card.get("data_authenticity", 1.0)
    if card.get("sector_diversity_penalty"):
        feature_snapshot["sector_diversity_penalty"] = card["sector_diversity_penalty"]
    card["feature_snapshot"] = feature_snapshot
    card["stop_loss"] = card["feature_snapshot"].get("stop_loss")
    card["take_profit"] = card["feature_snapshot"].get("take_profit")
    card["market_snapshot"] = snapshot
    card["agent_logs"] = review_log["logs"]
    card["recent_repeat"] = stock["code"] in (recent_codes or set())
    card["same_day_repeat"] = stock["code"] in (same_day_codes or set())
    return card





def _serialize_outcome(outcome: RecommendationOutcome | None) -> dict[str, Any] | None:
    if outcome is None:
        return None
    normalized_label = normalized_outcome_label(outcome)
    return {
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


def _build_replay_summary(detail: dict[str, Any]) -> dict[str, Any]:
    outcome = detail.get("outcome") or {}
    review_note = detail.get("review_note") or {}
    feature_snapshot = detail.get("feature_snapshot") or {}
    score_line_meta = detail.get("score_line_meta") or feature_snapshot.get("score_line_meta") or {}
    signal_features = feature_snapshot if isinstance(feature_snapshot, dict) else {}

    recommend_time = (
        detail.get("recommended_at")
        or detail.get("trade_date")
        or ""
    )
    entry_time = outcome.get("entry_date") or None
    exit_time = outcome.get("exit_date") or None
    net_return_pct = outcome.get("executed_return")
    if net_return_pct is None:
        net_return_pct = outcome.get("close_return")

    trigger_conditions: list[str] = []
    for value in (
        detail.get("entry_rule"),
        detail.get("setup_type"),
        signal_features.get("entry_rule"),
        signal_features.get("setup_type"),
    ):
        text = str(value or "").strip()
        if text and text not in trigger_conditions:
            trigger_conditions.append(text)

    adjustment_reasons = score_line_meta.get("reasons")
    if isinstance(adjustment_reasons, list):
        for item in adjustment_reasons:
            text = str(item or "").strip()
            if text and text not in trigger_conditions:
                trigger_conditions.append(text)

    failure_reason = (
        review_note.get("failure_reason")
        or outcome.get("outcome_reason")
        or None
    )
    is_false_positive = bool(
        failure_reason
        or str(review_note.get("user_label") or "") == "false_positive"
        or (net_return_pct is not None and float(net_return_pct or 0) <= 0)
    )
    attribution = (
        classify_false_positive(
            reason=failure_reason or outcome.get("outcome_reason"),
            executed=bool(entry_time or outcome.get("entry_price") is not None),
            outcome_label=outcome.get("outcome_label"),
            exit_reason=outcome.get("exit_reason"),
            user_label=review_note.get("user_label"),
            net_return_pct=float(net_return_pct or 0) if net_return_pct is not None else None,
        )
        if is_false_positive
        else {"category": None, "category_label": None, "stage": None}
    )

    return {
        "recommend_time": recommend_time,
        "symbol": detail.get("code"),
        "name": detail.get("name"),
        "entry_time": entry_time,
        "entry_price": outcome.get("entry_price") if outcome.get("entry_price") is not None else detail.get("recommend_price"),
        "exit_time": exit_time,
        "exit_price": outcome.get("exit_price"),
        "net_return_pct": net_return_pct,
        "trigger_conditions": trigger_conditions,
        "false_positive_reason": failure_reason if is_false_positive else None,
        "false_positive_category": attribution["category"],
        "false_positive_category_label": attribution["category_label"],
        "false_positive_stage": attribution["stage"],
        "review_failure_reason": review_note.get("failure_reason"),
        "user_label": review_note.get("user_label"),
        "outcome_label": outcome.get("outcome_label"),
        "outcome_reason": outcome.get("outcome_reason"),
        "exit_reason": outcome.get("exit_reason"),
        "holding_days": outcome.get("holding_days"),
        "effective_score_line": score_line_meta.get("effective_score_line"),
        "final_score": detail.get("final_score"),
        "signal_features": {
            key: signal_features.get(key)
            for key in ("market_regime", "trend_status", "volume_ratio_5d", "turnover_rate", "concept_sector_name")
            if key in signal_features
        },
    }


def _build_replay_analysis_row(detail: dict[str, Any]) -> dict[str, Any]:
    replay = _build_replay_summary(detail)
    net_return = replay.get("net_return_pct")
    net_return_value = float(net_return) if net_return is not None else None
    outcome_label = str(replay.get("outcome_label") or "")
    user_label = str(replay.get("user_label") or "")
    false_positive_reason = str(
        replay.get("review_failure_reason")
        or replay.get("false_positive_reason")
        or replay.get("outcome_reason")
        or ""
    ).strip()
    is_false_positive = bool(
        false_positive_reason
        or user_label == "false_positive"
        or outcome_label in {"failed", "invalid"}
        or (net_return_value is not None and net_return_value <= 0)
    )
    attribution = (
        classify_false_positive(
            reason=false_positive_reason or replay.get("outcome_reason"),
            executed=net_return_value is not None,
            outcome_label=outcome_label,
            exit_reason=replay.get("exit_reason"),
            user_label=user_label,
            net_return_pct=net_return_value,
        )
        if is_false_positive
        else {"category": None, "category_label": None, "stage": None}
    )
    trigger_conditions = list(replay.get("trigger_conditions") or [])
    signal_features = replay.get("signal_features") if isinstance(replay.get("signal_features"), dict) else {}
    return {
        "recommendation_id": detail.get("recommendation_id"),
        "run_id": detail.get("run_id"),
        "trade_date": detail.get("trade_date"),
        "recommend_time": replay.get("recommend_time"),
        "symbol": replay.get("symbol"),
        "name": replay.get("name"),
        "entry_time": replay.get("entry_time"),
        "entry_price": replay.get("entry_price"),
        "exit_time": replay.get("exit_time"),
        "exit_price": replay.get("exit_price"),
        "net_return_pct": net_return_value,
        "holding_days": replay.get("holding_days"),
        "outcome_label": outcome_label or None,
        "user_label": user_label or None,
        "is_false_positive": is_false_positive,
        "false_positive_reason": false_positive_reason or None,
        "false_positive_category": attribution["category"],
        "false_positive_category_label": attribution["category_label"],
        "false_positive_stage": attribution["stage"],
        "review_failure_reason": replay.get("review_failure_reason"),
        "outcome_reason": replay.get("outcome_reason"),
        "exit_reason": replay.get("exit_reason"),
        "trigger_conditions": trigger_conditions,
        "trigger_condition_text": " | ".join(trigger_conditions),
        "effective_score_line": replay.get("effective_score_line"),
        "final_score": replay.get("final_score"),
        "market_regime": signal_features.get("market_regime"),
        "trend_status": signal_features.get("trend_status"),
        "concept_sector_name": signal_features.get("concept_sector_name"),
        "signal_features": signal_features,
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
    return {
        "total_count": len(rows),
        "false_positive_count": len(false_positive_rows),
        "false_positive_rate": false_positive_rate,
        "average_net_return": average_net_return,
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


def _build_timeline(db: Session, rec: Recommendation) -> list[dict[str, Any]]:
    windows = [1, 3, 5, 10]
    bars = db.scalars(
        select(DailyKbar).where(DailyKbar.code == rec.code, DailyKbar.trade_date >= rec.trade_date).order_by(DailyKbar.trade_date.asc()).limit(12)
    ).all()
    if len(bars) < 2:
        return []
    timeline = []
    entry_price = rec.recommend_price or bars[0].close
    for window in windows:
        if len(bars) <= window:
            continue
        target_bar = bars[window]
        close_return = ((target_bar.close - entry_price) / entry_price) * 100
        max_return = max(((bar.high - entry_price) / entry_price) * 100 for bar in bars[1 : window + 1])
        max_drawdown = min(((bar.low - entry_price) / entry_price) * 100 for bar in bars[1 : window + 1])
        timeline.append(
            {
                "label": f"T+{window}",
                "trade_date": target_bar.trade_date,
                "close_return": round(close_return, 2),
                "max_return": round(max_return, 2),
                "max_drawdown": round(max_drawdown, 2),
            }
        )
    return timeline


def _historical_recommendations(db: Session, code: str, exclude_recommendation_id: str) -> list[dict[str, Any]]:
    history = db.scalars(
        select(Recommendation)
        .where(Recommendation.code == code, Recommendation.recommendation_id != exclude_recommendation_id)
        .order_by(Recommendation.recommended_at.desc())
        .limit(8)
    ).all()
    return [
        {
            "recommendation_id": rec.recommendation_id,
            "run_id": rec.run_id,
            "trade_date": rec.trade_date,
            "final_score": rec.final_score,
            "bucket": rec.bucket or "",
            "status": rec.status,
        }
        for rec in history
    ]


def _merge_run_params(db: Session, preset: Preset | None, payload: dict[str, Any]) -> dict[str, Any]:
    preset_data = _serialize_preset(db, preset) if preset else {}
    skills = payload.get("skills") or preset_data.get("skills") or []
    # SkillSelection only accepts skill_id/weight/sort_order; strip extra fields
    skills = [
        {k: v for k, v in s.items() if k in ("skill_id", "weight", "sort_order")}
        for s in skills
        if isinstance(s, dict)
    ]
    if payload.get("single_skill_id"):
        single_weight = float(payload.get("single_skill_weight") or 1.0)
        skills = [{"skill_id": str(payload["single_skill_id"]), "weight": single_weight, "sort_order": 0}]
    skip_aux_refresh = bool(
        payload.get("skip_aux_refresh")
        if "skip_aux_refresh" in payload
        else preset_data.get("skip_aux_refresh", False)
    )
    params = {
        "preset_id": preset_data.get("preset_id"),
        "run_name": str(payload.get("run_name") or preset_data.get("name") or "临时运行"),
        "market_scope": str(payload.get("market_scope") or preset_data.get("market_scope") or "all"),
        "market_scope_value": payload.get("market_scope_value", preset_data.get("market_scope_value")),
        "risk_template": str(payload.get("risk_template") or preset_data.get("risk_template") or "balanced"),
        "score_line": float(payload.get("score_line") or preset_data.get("score_line") or 72.0),
        "output_count": int(payload.get("output_count") or preset_data.get("output_count") or 6),
        "data_source": normalize_data_source(payload.get("data_source") or preset_data.get("data_source") or DEFAULT_RECOMMENDATION_DATA_SOURCE),
        "skip_aux_refresh": skip_aux_refresh,
        "disable_agent_review": bool(payload.get("disable_agent_review", False)),
        "agent_review_limit": int(payload.get("agent_review_limit") or 0),
        "agent_review_concurrency": int(payload.get("agent_review_concurrency") or 0),
        "hide_recent_repeats": bool(
            payload.get("hide_recent_repeats")
            if "hide_recent_repeats" in payload
            else preset_data.get("hide_recent_repeats", True)
        ),
        "dedup_same_day": bool(
            payload.get("dedup_same_day")
            if "dedup_same_day" in payload
            else preset_data.get("dedup_same_day", True)
        ),
        "skills": skills,
    }
    for key in ("matrix_batch_id", "matrix_batch_name", "matrix_run", "trade_date_override"):
        if key in payload:
            params[key] = payload.get(key)
    return params


def _apply_market_scope_to_stocks(db: Session, stocks: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    scope = params["market_scope"]
    if scope == "industry" and params.get("market_scope_value"):
        return [item for item in stocks if item.get("industry") == params["market_scope_value"]]
    if scope == "watchlist":
        watched_codes = {
            row.code
            for row in db.scalars(select(WatchItem).where(WatchItem.watch_status == "watching")).all()
        }
        return [item for item in stocks if item["code"] in watched_codes]
    return stocks


def _select_skills_for_run(
    db: Session,
    skill_map: dict[str, StrategySkill],
    requested_skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in requested_skills:
        skill_id = str(item.get("skill_id"))
        registry = db.get(SkillRegistry, skill_id)
        skill = skill_map.get(skill_id)
        if registry is None or skill is None or not registry.enabled:
            continue
        errors = validate_skill(skill)
        if errors:
            continue
        selected.append(
            {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "weight": float(item.get("weight") or 1.0),
                "version_id": _skill_version_id(skill),
                "skill": skill,
            }
        )
    return sorted(selected, key=lambda row: row["skill_id"])


def _apply_market_scope(db: Session, features: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    scoped_codes = {item["code"] for item in _apply_market_scope_to_stocks(db, features, params)}
    return [item for item in features if item["code"] in scoped_codes]


def _recent_recommended_codes(db: Session, trade_date: str) -> set[str]:
    recent_date = (datetime.fromisoformat(trade_date) - timedelta(days=7)).date().isoformat()
    rows = db.scalars(
        select(Recommendation).where(
            Recommendation.trade_date >= recent_date,
            Recommendation.trade_date < trade_date,
        )
    ).all()
    return {row.code for row in rows}


def _same_day_recommended_codes(
    db: Session, trade_date: str, exclude_run_id: str | None = None
) -> set[str]:
    """同一交易日、已由其他 formal run 推荐过的 code 集合（跨 run 同日去重）。

    因果：多个 formal run 在同一交易日各自扫描全市场，常把同一只强势票重复推荐，
    造成「同一笔坏赌注当日被计 2~7 次亏损」的集中度/噪声问题（如 603268 在 2026-07-08
    被 7 个 run 各推一次、全部次日止损 -5.69%）。在最终卡片过滤阶段剔除当日已被其他
    formal run 推荐过的 code（本 run 自身推荐不剔除——单 run 内聚合已按 code 去重）。
    这是硬风险控制（不重复集中同一只票），与 hide_recent_repeats（跨日去重）正交。
    """
    stmt = (
        select(Recommendation.code)
        .join(RecommendationRun, Recommendation.run_id == RecommendationRun.run_id)
        .where(RecommendationRun.run_mode == "formal", Recommendation.trade_date == trade_date)
    )
    if exclude_run_id is not None:
        stmt = stmt.where(Recommendation.run_id != exclude_run_id)
    rows = db.scalars(stmt).all()
    return {row for row in rows}


def _same_day_industry_counts(
    db: Session, trade_date: str, exclude_run_id: str | None = None
) -> dict[str, int]:
    """同一交易日、已由其他 formal run 推荐过的行业组计数（跨 run 行业集中控制）。

    因果：_apply_sector_diversity 的 _MAX_PER_INDUSTRY=5 只在单 run 内生效，但同日多个
    formal run 各推 3-4 只半导体 → 跨 run 累积 16 只半导体（50% 集中），违反组合分散。
    返回当日其他 run 已推荐的 group→count，作为本 run _apply_sector_diversity 的基数。
    用 _industry_group（aggregator）归并关联行业（元器件/半导体/通信设备→科技硬件）。
    """
    from app.engine.aggregator import _industry_group

    stmt = (
        select(Recommendation.industry)
        .join(RecommendationRun, Recommendation.run_id == RecommendationRun.run_id)
        .where(RecommendationRun.run_mode == "formal", Recommendation.trade_date == trade_date)
    )
    if exclude_run_id is not None:
        stmt = stmt.where(Recommendation.run_id != exclude_run_id)
    rows = db.scalars(stmt).all()
    counts: dict[str, int] = {}
    for industry in rows:
        ind = str(industry or "")
        if not ind or ind == "unknown":
            continue
        group = _industry_group(ind)
        counts[group] = counts.get(group, 0) + 1
    return counts


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


def _agent_history_context(db: Session, skill_id: str, stock: dict[str, Any]) -> dict[str, Any]:
    same_stock_rows = db.scalars(
        select(Recommendation)
        .where(Recommendation.code == stock["code"])
        .order_by(Recommendation.recommended_at.desc())
        .limit(6)
    ).all()
    same_stock_history = []
    for rec in same_stock_rows:
        outcome = db.get(RecommendationOutcome, rec.recommendation_id)
        same_stock_history.append(
            {
                "trade_date": rec.trade_date,
                "bucket": rec.bucket or "",
                "final_score": rec.final_score,
                "setup_type": rec.setup_type,
                "outcome_label": normalized_outcome_label(outcome) if outcome else rec.status,
                "close_return": resolved_trade_return(outcome) if outcome else None,
                "max_drawdown": outcome.max_drawdown if outcome else None,
            }
        )

    matched_recs = []
    for rec in db.scalars(select(Recommendation).order_by(Recommendation.recommended_at.desc()).limit(300)).all():
        if any(signal.get("strategy_id") == skill_id for signal in _loads(rec.skill_breakdown_json, [])):
            matched_recs.append(rec)
    outcomes = [
        db.get(RecommendationOutcome, rec.recommendation_id)
        for rec in matched_recs
        if db.get(RecommendationOutcome, rec.recommendation_id) is not None
    ]
    labeled = [outcome for outcome in outcomes if is_outcome_labeled(outcome)]
    executed = [outcome for outcome in labeled if is_trade_executed(outcome)]
    success = [outcome for outcome in executed if is_accurate_recommendation(outcome)]
    return {
        "same_stock_history": same_stock_history,
        "skill_recent_performance": {
            "sample_count": len(matched_recs),
            "labeled_count": len(labeled),
            "triggered_count": len(executed),
            "success_count": len(success),
            "trigger_rate": round(len(executed) / len(labeled), 3) if labeled else 0.0,
            "hit_rate": round(len(success) / len(executed), 3) if executed else 0.0,
            "avg_close_return": round(sum(resolved_trade_return(outcome) for outcome in executed) / len(executed), 2) if executed else 0.0,
        },
    }


def _build_run_summary(
    cards: list[dict[str, Any]],
    saved: list[Recommendation],
    snapshot: dict[str, Any],
    pipeline_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    breadth = snapshot.get("breadth", {}) or {}
    pipeline = pipeline_audit or breadth.get("recommendation_pipeline") or {}
    score_line = pipeline.get("score_line") or {}
    filter_distributions = ((pipeline.get("filter_audit") or {}).get("distributions") or {}) if isinstance(pipeline, dict) else {}
    return {
        "top_codes": [rec.code for rec in saved[:5]],
        "avg_score": round(sum(rec.final_score for rec in saved) / len(saved), 2) if saved else 0.0,
        "data_source_mode": breadth.get("run_data_source_mode"),
        "data_source": breadth.get("data_source"),
        "quote_source": breadth.get("quote_source"),
        "kline_sources": breadth.get("kline_sources"),
        "agent_runtime": get_settings().agent_skill_runtime,
        "main_industries": snapshot.get("main_industries", []),
        "auxiliary_data": breadth.get("auxiliary_data_audit"),
        "pipeline": pipeline,
        "score_line": score_line,
        "score_distribution": _score_distribution(cards),
        "quality_distribution": _quality_distribution(cards),
        "candidate_score_distribution": filter_distributions.get("final_score_all") or _score_distribution(cards),
        "candidate_quality_distribution": filter_distributions.get("evidence_quality_all") or _quality_distribution(cards),
        "score_gap_distribution": filter_distributions.get("score_gap") or {},
        "coverage_heatmap": _coverage_heatmap(cards),
        "unified_quote_total": pipeline.get("unified_quote_total"),
        "base_filter_eligible_count": pipeline.get("base_filter_eligible_count"),
        "daily_kline_coverage_count": pipeline.get("daily_kline_coverage_count"),
        "filtered_kline_coverage_count": pipeline.get("filtered_kline_coverage_count"),
        "scoped_candidate_count": pipeline.get("scoped_candidate_count"),
        "prefilter_candidate_count": pipeline.get("prefilter_candidate_count"),
        "actual_scan_count": pipeline.get("actual_scan_count"),
        "skill_input_count": pipeline.get("skill_input_count"),
        "skill_agent_call_count": pipeline.get("skill_agent_call_count"),
        "skill_rule_candidate_count": pipeline.get("skill_rule_candidate_count"),
        "skill_gate_candidate_count": pipeline.get("skill_gate_candidate_count"),
        "demo_limited": pipeline.get("demo_limited", False),
        "scan_limited": pipeline.get("scan_limited", False),
        "skill_limited": pipeline.get("skill_limited", False),
        "skip_reasons": pipeline.get("skip_reasons", {}),
    }


def _persist_market(db: Session, snapshot: dict[str, Any]) -> None:
    existing = db.get(MarketSnapshot, snapshot["snapshot_id"])
    if existing is not None:
        return
    db.add(
        MarketSnapshot(
            snapshot_id=snapshot["snapshot_id"],
            trade_date=snapshot["trade_date"],
            sample_count=snapshot["sample_count"],
            up_count=snapshot["up_count"],
            down_count=snapshot["down_count"],
            limit_up_count=snapshot["limit_up_count"],
            limit_down_count=snapshot["limit_down_count"],
            main_industries_json=_dumps(snapshot["main_industries"]),
            breadth_json=_dumps(snapshot["breadth"]),
        )
    )


def _persist_stocks_and_bars(
    db: Session,
    stocks: list[dict[str, Any]],
    persist_bars: bool = True,
) -> None:
    seen_codes: set[str] = set()
    seen_bars: set[tuple[str, str]] = set()
    stock_rows: list[dict[str, Any]] = []
    bar_rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for stock in stocks:
        code = str(stock.get("code") or "").strip()
        if not code or code in seen_codes:
            continue
        seen_codes.add(code)
        stock_rows.append(
            {
                "code": code,
                "name": str(stock.get("name") or code),
                "market": stock.get("exchange") or stock.get("market") or "",
                "industry": stock.get("industry"),
                "is_st": bool(stock.get("is_st", False)),
                "updated_at": now,
            }
        )
        if not persist_bars:
            continue
        for bar in stock.get("_bars", []):
            bar_code = str(bar.get("code") or code).strip()
            trade_date = str(bar.get("trade_date") or "").strip()
            if not bar_code or not trade_date:
                continue
            bar_key = (bar_code, trade_date)
            if bar_key in seen_bars:
                continue
            seen_bars.add(bar_key)
            bar_rows.append(
                {
                    "code": bar_code,
                    "trade_date": trade_date,
                    "open": float(bar.get("open") or 0),
                    "high": float(bar.get("high") or 0),
                    "low": float(bar.get("low") or 0),
                    "close": float(bar.get("close") or 0),
                    "volume": float(bar.get("volume") or 0),
                    "amount": float(bar.get("amount") or 0),
                    "pct_chg": float(bar.get("pct_chg") or 0),
                    "turnover_rate": float(bar.get("turnover_rate") or 0),
                    "ma5": _float_or_none(bar.get("ma5")),
                    "ma10": _float_or_none(bar.get("ma10")),
                    "ma20": _float_or_none(bar.get("ma20")),
                    "ma60": _float_or_none(bar.get("ma60")),
                    "vol_ratio_5": _float_or_none(bar.get("vol_ratio_5")),
                }
            )

    _upsert_stock_rows(db, stock_rows)
    if persist_bars:
        _upsert_daily_bar_rows(db, bar_rows)


def _upsert_stock_rows(db: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    for chunk in _chunked(rows, UPSERT_BATCH_SIZE):
        stmt = mysql_insert(Stock).values(chunk)
        db.execute(
            stmt.on_duplicate_key_update(
                name=stmt.inserted.name,
                market=stmt.inserted.market,
                industry=stmt.inserted.industry,
                is_st=stmt.inserted.is_st,
                updated_at=stmt.inserted.updated_at,
            )
        )


def _upsert_daily_bar_rows(db: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    for chunk in _chunked(rows, UPSERT_BATCH_SIZE):
        stmt = mysql_insert(DailyKbar).values(chunk)
        db.execute(
            stmt.on_duplicate_key_update(
                open=stmt.inserted.open,
                high=stmt.inserted.high,
                low=stmt.inserted.low,
                close=stmt.inserted.close,
                volume=stmt.inserted.volume,
                amount=stmt.inserted.amount,
                pct_chg=stmt.inserted.pct_chg,
                turnover_rate=stmt.inserted.turnover_rate,
                ma5=stmt.inserted.ma5,
                ma10=stmt.inserted.ma10,
                ma20=stmt.inserted.ma20,
                ma60=stmt.inserted.ma60,
                vol_ratio_5=stmt.inserted.vol_ratio_5,
            )
        )


def _chunked(rows: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + chunk_size] for index in range(0, len(rows), chunk_size)]


def _float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_calibrated_agg_weights(db: Session) -> dict[str, float]:
    """Load calibrated aggregation weights from CalibrationConfig table.

    Keys match aggregator.py's 5-dimension system: skill_agent, strategy_rule,
    market, risk, evidence.
    """
    rows = db.scalars(select(CalibrationConfig)).all()
    agg_keys = {"skill_agent", "strategy_rule", "market", "risk", "evidence"}
    weights: dict[str, float] = {}
    for row in rows:
        if row.key in agg_keys:
            weights[row.key] = float(row.value)
    return weights


def _load_calibrated_dsa_weights(db: Session) -> dict[str, float]:
    """Load calibrated 基础短线策略 sub-score weights from CalibrationConfig table."""
    rows = db.scalars(select(CalibrationConfig)).all()
    weights: dict[str, float] = {}
    for row in rows:
        if row.key.startswith("dsa_w_"):
            weights[row.key] = float(row.value)
    return weights


def _dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False)


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value

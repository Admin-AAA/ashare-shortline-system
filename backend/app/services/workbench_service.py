import hashlib
import copy
import json
import logging
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import asyncio
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, select, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.agents.review_runner import run_skill_reviews
from app.agents.session_registry import AgentSessionRegistry
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.engine.agent_skill_runner import agent_only_signal, enrich_signal_with_agent_review
from app.engine.aggregator import (
    aggregate,
    apply_pool_rules,
    market_regime_weights,
    resolve_display_limit,
    resolve_score_line_context,
)
from app.engine.skill_loader import StrategySkill, load_skills, validate_skill
from app.engine.strategy_runner import run_strategy
from app.models.tables import (
    AgentDecisionLog,
    CalibrationConfig,
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyKbar,
    DragonTiger,
    MarketSnapshot,
    NewsAnnouncement,
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
    StockQuote,
)
from app.services.feature_builder import build_features, market_regime_score
from app.services.failure_attribution import classify_false_positive
from app.services.market_data import MarketDataError, get_market_universe
from app.services.market_pool import (
    get_market_snapshot_from_pool,
    get_market_universe_from_pool,
    refresh_capital_flows_for_codes,
    refresh_concept_sectors,
    refresh_dragon_tiger,
    refresh_news_for_codes,
)
from app.services.outcome_service import (
    invalidate_outcome_metrics_cache,
    is_accurate_recommendation,
    is_outcome_labeled,
    is_trade_executed,
    label_pending_outcomes as label_pending_outcomes_service,
    normalized_outcome_label,
    resolved_trade_return,
)
from app.services.prefilter_service import apply_prefilters
from app.services.research_context import build_research_contexts
from app.services.runs.progress import progress_pct as compute_progress_pct


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
DATA_SOURCE_LOCAL_POOL = "local_pool"
DATA_SOURCE_LOCAL_POOL_PREFERRED = "local_pool_preferred"
DATA_SOURCE_REALTIME = "realtime"
ALLOWED_DATA_SOURCES = {
    DATA_SOURCE_LOCAL_POOL,
    DATA_SOURCE_LOCAL_POOL_PREFERRED,
    DATA_SOURCE_REALTIME,
}
DEFAULT_PRESETS = [
    {
        "preset_id": "preset_formal_opening",
        "name": "正式盘前组合",
        "description": "盘前默认正式组合，强调主线、技术确认和风险平衡。",
        "run_mode": "formal",
        "data_source": DATA_SOURCE_LOCAL_POOL,
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 72.0,
        "output_count": 6,
        "hide_recent_repeats": True,
        "skill_groups": ["formal_opening"],
    },
    {
        "preset_id": "preset_formal_trend_core",
        "name": "趋势龙头组合",
        "description": "适合主线趋势票，偏重龙头和大局观。",
        "run_mode": "formal",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "aggressive",
        "score_line": 70.0,
        "output_count": 5,
        "hide_recent_repeats": True,
        "skill_groups": ["formal_trend_core"],
    },
    {
        "preset_id": "preset_experiment_dsa",
        "name": "DSA 单 Skill 实验",
        "description": "单独验证技术评分 Skill 的辨识度，不进入正式统计。",
        "run_mode": "experiment",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 68.0,
        "output_count": 6,
        "hide_recent_repeats": False,
        "skill_groups": ["experiment_single_factor"],
    },
]


def _default_data_source_for_run_mode(run_mode: Any) -> str:
    return DATA_SOURCE_LOCAL_POOL_PREFERRED if str(run_mode or "formal") == "formal" else DATA_SOURCE_LOCAL_POOL


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
    run_mode: str,
    skip_aux_refresh: bool,
    max_codes: int | None = None,
) -> int:
    if prefiltered_count <= 0:
        return 0
    bounded_max = max(1, int(max_codes or get_settings().auxiliary_prefetch_max_codes or 120))
    if skip_aux_refresh:
        return min(prefiltered_count, max(18, min_prefilter_count // 2), 48, bounded_max)
    if run_mode == "formal":
        return min(prefiltered_count, max(min_prefilter_count, bounded_max), bounded_max)
    return min(prefiltered_count, max(18, min_prefilter_count // 2), 48, bounded_max)


_bootstrap_cache_lock = threading.Lock()
_bootstrap_cache: dict[tuple[str, str], dict[str, Any]] = {}
_initial_state_cache_lock = threading.Lock()
_initial_state_cache: dict[tuple[str, str], dict[str, Any]] = {}
_progress_commit_state_lock = threading.Lock()
_progress_commit_state: dict[str, dict[str, Any]] = {}
_outcome_backfill_lock = threading.Lock()
_outcome_backfill_ts = 0.0
_run_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
_run_queue_lock = threading.Lock()
_run_queue_worker_started = False


def bootstrap_workbench_state(db: Session) -> dict[str, Any]:
    settings = get_settings()
    cache_key = (settings.database_url, str(settings.skills_dir.resolve()))
    now = time.monotonic()
    with _bootstrap_cache_lock:
        cached = _bootstrap_cache.get(cache_key)
        if cached and (now - float(cached["ts"])) < BOOTSTRAP_CACHE_TTL_SECONDS:
            return copy.deepcopy(cached["data"])

    _auto_backfill_outcomes(db)

    t0 = time.perf_counter()
    sync_skills(db)
    t1 = time.perf_counter()
    presets = list_presets(db)
    t2 = time.perf_counter()
    recent_runs = list_runs(db, limit=20)
    latest_formal = next((run for run in recent_runs if run["run_mode"] == "formal"), None)
    latest_experiment = next((run for run in recent_runs if run["run_mode"] == "experiment"), None)
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
            "formal": latest_formal,
            "experiment": latest_experiment,
        },
        "market_snapshot": market_snapshot,
        "skill_summary": {
            "formal_count": sum(1 for skill in skills if skill["mode"] == "formal"),
            "experiment_count": sum(1 for skill in skills if skill["mode"] == "experiment"),
            "disabled_count": sum(1 for skill in skills if skill["mode"] == "disabled"),
        },
    }
    with _bootstrap_cache_lock:
        _bootstrap_cache[cache_key] = {"ts": time.monotonic(), "data": copy.deepcopy(state)}
    return state


def invalidate_bootstrap_cache() -> None:
    with _bootstrap_cache_lock:
        _bootstrap_cache.clear()
    with _initial_state_cache_lock:
        _initial_state_cache.clear()


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
    now = time.monotonic()
    with _initial_state_cache_lock:
        cached = _initial_state_cache.get(cache_key)
        if cached and (now - float(cached["ts"])) < INITIAL_STATE_CACHE_TTL_SECONDS:
            return copy.deepcopy(cached["data"])

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
    with _initial_state_cache_lock:
        _initial_state_cache[cache_key] = {"ts": time.monotonic(), "data": copy.deepcopy(payload)}
    return payload


def enqueue_workbench_run(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    skills = sync_skills(db)
    skill_map = {skill.skill_id: skill for skill in skills}
    preset = db.get(Preset, payload["preset_id"]) if payload.get("preset_id") else None
    if payload.get("preset_id") and preset is None:
        raise ValueError("预设不存在，请刷新后重试")
    params = _merge_run_params(db, preset, payload)
    if params["run_mode"] not in {"formal", "experiment"}:
        raise ValueError("运行模式仅支持 formal 或 experiment")
    selected_skills = _select_skills_for_run(db, skill_map, params["run_mode"], params["skills"])
    if not selected_skills:
        raise ValueError("没有可运行的 Skill，请检查预设配置或 Skill 状态")

    run_id = _new_run_id()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run = RecommendationRun(
        run_id=run_id,
        preset_id=params.get("preset_id"),
        run_name=params["run_name"],
        run_mode=params["run_mode"],
        trade_date=_latest_trade_date(db),
        started_at=now,
        universe_count=0,
        recommended_count=0,
        market_scope=params["market_scope"],
        market_scope_value=params.get("market_scope_value"),
        risk_template=params["risk_template"],
        output_count=params["output_count"],
        selected_skills_json=_dumps(_selected_skill_rows(selected_skills)),
        params_json=_dumps(params),
        market_snapshot_json=_dumps({}),
        result_summary_json=_dumps({"agent_runtime": "queued"}),
        status="queued",
        heartbeat_at=now,
        progress_phase="queued",
        progress_current=0,
        progress_total=0,
        progress_message="等待后台工作线程接管",
    )
    db.add(run)
    db.commit()
    invalidate_bootstrap_cache()
    _enqueue_background_run(run_id, dict(payload))
    return {
        "run": _serialize_run_summary(db, run),
        "recommendations": [],
        "queued": True,
    }


_last_skill_sync_time: float = 0.0
_skill_sync_ttl: float = 5.0  # seconds — avoid redundant disk I/O on concurrent requests
_cached_skill_list: list[StrategySkill] | None = None
_cached_skill_cache_key: tuple[str, str] | None = None
_skill_sync_lock = threading.Lock()


def sync_skills(db: Session) -> list[StrategySkill]:
    global _last_skill_sync_time, _cached_skill_cache_key, _cached_skill_list
    settings = get_settings()
    cache_key = (settings.database_url, str(settings.skills_dir.resolve()))
    now = time.time()
    if _is_skill_cache_fresh(now, cache_key):
        return _cached_skill_list

    with _skill_sync_lock:
        now = time.time()
        if _is_skill_cache_fresh(now, cache_key):
            return _cached_skill_list

        skills = load_skills(settings.skills_dir)
        has_changes = False
        for skill in skills:
            registry = db.get(SkillRegistry, skill.skill_id)
            manifest_data = dict(skill.manifest)
            manifest_data["_skill_md_available"] = skill.agent_enabled
            manifest_data["_skill_md_hash"] = skill.skill_hash
            manifest_data["_skill_frontmatter"] = _json_safe(skill.skill_frontmatter)
            manifest_data["_skill_reference_files"] = _json_safe(skill.reference_files)
            manifest_data["_skill_runtime"] = "dual" if skill.contract is not None and skill.agent_enabled else "agent" if skill.agent_enabled else "rule"
            manifest_data["_strategy_backtest_adapter"] = _strategy_backtest_adapter_manifest(skill)
            manifest_json = json.dumps(manifest_data, ensure_ascii=False)
            default_mode = _default_skill_mode(skill)
            if registry is None:
                registry = SkillRegistry(
                    skill_id=skill.skill_id,
                    name=skill.name,
                    source_type=skill.manifest.get("source_type", "local"),
                    source_url=skill.manifest.get("source_url"),
                    installed_path=str(skill.path),
                    current_version=skill.version,
                    mode=default_mode,
                    enabled=default_mode != "disabled",
                    has_executable_contract=skill.contract is not None,
                    manifest_json=manifest_json,
                )
                db.add(registry)
                has_changes = True
            else:
                has_changes = _sync_registry_row(registry, skill, manifest_json, default_mode) or has_changes

            version_id = _skill_version_id(skill)
            if db.get(SkillVersion, version_id) is None:
                db.add(
                    SkillVersion(
                        id=version_id,
                        skill_id=skill.skill_id,
                        version=skill.version,
                        source_ref=skill.manifest.get("source_ref"),
                        installed_path=str(skill.path),
                        manifest_json=manifest_json,
                        contract_hash=skill.contract_hash,
                        status=registry.mode,
                    )
                )
                has_changes = True
            if skill.contract is not None and db.get(StrategyVersion, version_id) is None:
                db.add(
                    StrategyVersion(
                        version_id=version_id,
                        strategy_id=skill.skill_id,
                        name=skill.name,
                        enabled=registry.mode != "disabled",
                        skill_repo=skill.manifest.get("source_url"),
                        skill_ref=skill.manifest.get("source_ref"),
                        skill_path=str(skill.path),
                        yaml_path=str(skill.path / "strategy.yaml"),
                        yaml_hash=skill.contract_hash or "",
                        manifest_json=manifest_json,
                    )
                )
                has_changes = True
        if has_changes:
            db.commit()
        _ensure_default_presets(db, skills)
        _last_skill_sync_time = time.time()
        _cached_skill_cache_key = cache_key
        _cached_skill_list = skills
        return skills


def _is_skill_cache_fresh(now: float, cache_key: tuple[str, str]) -> bool:
    return (
        _cached_skill_list is not None
        and _cached_skill_cache_key == cache_key
        and (now - _last_skill_sync_time) < _skill_sync_ttl
    )


def _sync_registry_row(
    registry: SkillRegistry,
    skill: StrategySkill,
    manifest_json: str,
    default_mode: str,
) -> bool:
    changed = False
    updates = {
        "name": skill.name,
        "source_type": skill.manifest.get("source_type", registry.source_type),
        "source_url": skill.manifest.get("source_url", registry.source_url),
        "installed_path": str(skill.path),
        "current_version": skill.version,
        "has_executable_contract": skill.contract is not None,
        "manifest_json": manifest_json,
    }
    for field, value in updates.items():
        if getattr(registry, field) != value:
            setattr(registry, field, value)
            changed = True
    if not registry.mode:
        registry.mode = default_mode
        changed = True
    if _should_reset_external_to_experiment(registry):
        registry.mode = "experiment"
        changed = True
    enabled = registry.mode != "disabled"
    if registry.enabled != enabled:
        registry.enabled = enabled
        changed = True
    if changed:
        registry.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return changed


def _strategy_backtest_adapter_manifest(skill: StrategySkill) -> dict[str, Any] | None:
    adapter = ((skill.contract or {}).get("meta") or {}).get("backtest_adapter")
    if not isinstance(adapter, dict):
        return None
    actions = adapter.get("actions") or {}
    if not isinstance(actions, dict):
        actions = {}
    return {
        "default_action": str(adapter.get("default_action") or "run"),
        "actions": sorted(str(action) for action in actions if str(action).strip()),
    }


def list_skills(db: Session) -> list[dict[str, Any]]:
    sync_skills(db)
    preset_rows = db.execute(
        select(PresetSkill.skill_id, Preset.name).join(Preset, Preset.preset_id == PresetSkill.preset_id)
    ).all()
    preset_map: dict[str, list[str]] = {}
    for skill_id, preset_name in preset_rows:
        preset_map.setdefault(skill_id, []).append(preset_name)
    rows = db.scalars(select(SkillRegistry).order_by(SkillRegistry.skill_id.asc())).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        manifest = _loads(row.manifest_json, {})
        items.append(
            {
                "skill_id": row.skill_id,
                "name": row.name,
                "source_type": row.source_type,
                "source_url": row.source_url,
                "installed_path": row.installed_path,
                "current_version": row.current_version,
                "mode": row.mode,
                "enabled": row.enabled,
                "has_executable_contract": row.has_executable_contract,
                "has_agent_contract": bool(manifest.get("_skill_md_available")),
                "agent_runtime": manifest.get("_skill_runtime", "unknown"),
                "has_backtest_adapter": bool(manifest.get("_strategy_backtest_adapter")),
                "backtest_adapter_actions": (manifest.get("_strategy_backtest_adapter") or {}).get("actions", []),
                "reference_count": len(manifest.get("_skill_reference_files", [])),
                "manifest": manifest,
                "assigned_presets": preset_map.get(row.skill_id, []),
            }
        )
    return items


def get_skill_detail(db: Session, skill_id: str) -> dict[str, Any] | None:
    sync_skills(db)
    row = db.get(SkillRegistry, skill_id)
    if row is None:
        return None
    path = PathLike(row.installed_path)
    skill = load_skills(get_settings().skills_dir)
    skill_obj = next((item for item in skill if item.skill_id == skill_id), None)
    skill_md = ""
    errors: list[str] = []
    if path.joinpath("SKILL.md").exists():
        skill_md = path.joinpath("SKILL.md").read_text(encoding="utf-8")
    if skill_obj is not None:
        errors = validate_skill(skill_obj)
    performance_formal = _skill_performance(db, skill_id, "formal")
    performance_experiment = _skill_performance(db, skill_id, "experiment")
    assigned_presets = [
        {"preset_id": preset_id, "name": db.get(Preset, preset_id).name}
        for preset_id in {row.preset_id for row in db.scalars(select(PresetSkill).where(PresetSkill.skill_id == skill_id)).all()}
        if db.get(Preset, preset_id) is not None
    ]
    manifest = _loads(row.manifest_json, {})
    adapter_manifest = manifest.get("_strategy_backtest_adapter") or {}
    return {
        "skill_id": row.skill_id,
        "name": row.name,
        "mode": row.mode,
        "enabled": row.enabled,
        "current_version": row.current_version,
        "has_executable_contract": row.has_executable_contract,
        "has_agent_contract": bool(skill_md.strip()),
        "agent_runtime": manifest.get("_skill_runtime", "unknown"),
        "has_backtest_adapter": bool(adapter_manifest),
        "backtest_adapter_actions": adapter_manifest.get("actions", []),
        "reference_files": manifest.get("_skill_reference_files", []),
        "manifest": manifest,
        "skill_md": skill_md,
        "errors": errors,
        "assigned_presets": assigned_presets,
        "performance": {
            "formal": performance_formal,
            "experiment": performance_experiment,
        },
    }


def set_skill_mode(db: Session, skill_id: str, mode: str) -> dict[str, Any] | None:
    row = db.get(SkillRegistry, skill_id)
    if row is None:
        return None
    row.mode = mode
    row.enabled = mode != "disabled"
    compatibility = _loads(row.compatibility_json, {})
    compatibility["mode_set_by_user"] = True
    row.compatibility_json = _dumps(compatibility)
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    invalidate_bootstrap_cache()
    return {"skill_id": row.skill_id, "mode": row.mode, "enabled": row.enabled}


def _default_skill_mode(skill: StrategySkill) -> str:
    if not skill.enabled:
        return "disabled"
    source_type = str(skill.manifest.get("source_type") or "local")
    if source_type in {"github", "external"}:
        return "experiment"
    return "formal"


def _should_reset_external_to_experiment(registry: SkillRegistry) -> bool:
    if registry.source_type not in {"github", "external"}:
        return False
    if registry.mode != "formal":
        return False
    compatibility = _loads(registry.compatibility_json, {})
    return not bool(compatibility.get("mode_set_by_user"))


def backtest_skill(db: Session, skill_id: str) -> dict[str, Any] | None:
    detail = get_skill_detail(db, skill_id)
    if detail is None:
        return None
    performance = detail["performance"]["experiment"]
    if performance["sample_count"] == 0:
        performance = detail["performance"]["formal"]
    return {
        "skill_id": skill_id,
        "status": "completed",
        "sample_count": performance["sample_count"],
        "hit_rate": performance["hit_rate"],
        "avg_return": performance["avg_return"],
        "errors": detail["errors"],
    }


def install_skill(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload.get("source") or payload.get("url") or "").strip()
    if not source:
        raise ValueError("source is required")
    settings = get_settings()
    settings.skills_dir.mkdir(parents=True, exist_ok=True)
    if source.startswith("http://") or source.startswith("https://") or source.endswith(".git"):
        target_name = str(payload.get("skill_id") or Path(source.rstrip("/").removesuffix(".git")).name)
        target = settings.skills_dir / target_name
        if target.exists():
            shutil.rmtree(target)
        subprocess.run(["git", "clone", "--depth", "1", source, str(target)], check=True, capture_output=True, text=True)
    else:
        src = Path(source).expanduser().resolve()
        if not src.exists() or not src.is_dir():
            raise ValueError("local skill path does not exist")
        target_name = str(payload.get("skill_id") or src.name)
        target = settings.skills_dir / target_name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
    _write_install_manifest(target, target_name, source)
    sync_skills(db)
    skill = load_skills(settings.skills_dir)
    installed = next((item for item in skill if item.path == target), None)
    if installed is None:
        raise ValueError("skill install failed")
    detail = get_skill_detail(db, installed.skill_id)
    if detail is None:
        raise ValueError("skill registry failed")
    invalidate_bootstrap_cache()
    return detail


def list_presets(db: Session) -> list[dict[str, Any]]:
    sync_skills(db)
    presets = db.scalars(select(Preset).order_by(Preset.created_at.asc())).all()
    return _serialize_presets_batched(db, presets)


def create_preset(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(payload.get("preset_id") or _slugify(payload.get("name") or f"preset-{uuid.uuid4().hex[:6]}"))
    if db.get(Preset, preset_id) is not None:
        preset_id = f"{preset_id}-{uuid.uuid4().hex[:4]}"
    run_mode = str(payload.get("run_mode") or "formal")
    preset = Preset(
        preset_id=preset_id,
        name=str(payload.get("name") or "未命名预设"),
        description=payload.get("description"),
        run_mode=run_mode,
        market_scope=str(payload.get("market_scope") or "all"),
        market_scope_value=payload.get("market_scope_value"),
        risk_template=str(payload.get("risk_template") or "balanced"),
        score_line=float(payload.get("score_line") or 72.0),
        output_count=int(payload.get("output_count") or 6),
        bucket_limits_json="{}",
        data_source=_normalize_data_source(payload.get("data_source") or _default_data_source_for_run_mode(run_mode)),
        hide_recent_repeats=bool(payload.get("hide_recent_repeats", True)),
        status=str(payload.get("status") or "active"),
    )
    db.add(preset)
    db.flush()
    _replace_preset_skills(db, preset.preset_id, payload.get("skills") or [])
    db.commit()
    invalidate_bootstrap_cache()
    return _serialize_preset(db, preset)


def update_preset(db: Session, preset_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    preset = db.get(Preset, preset_id)
    if preset is None:
        return None
    for field in [
        "name",
        "description",
        "run_mode",
        "market_scope",
        "market_scope_value",
        "risk_template",
        "score_line",
        "data_source",
        "status",
    ]:
        if field in payload:
            value = _normalize_data_source(payload[field]) if field == "data_source" else payload[field]
            setattr(preset, field, value)
    if "output_count" in payload:
        preset.output_count = int(payload["output_count"])
    if "hide_recent_repeats" in payload:
        preset.hide_recent_repeats = bool(payload["hide_recent_repeats"])
    preset.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if "skills" in payload:
        _replace_preset_skills(db, preset.preset_id, payload["skills"] or [])
    db.commit()
    invalidate_bootstrap_cache()
    return _serialize_preset(db, preset)


def list_runs(db: Session, run_mode: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    stmt = select(RecommendationRun).order_by(RecommendationRun.started_at.desc()).limit(limit)
    if run_mode:
        stmt = (
            select(RecommendationRun)
            .where(RecommendationRun.run_mode == run_mode)
            .order_by(RecommendationRun.started_at.desc())
            .limit(limit)
        )
    runs = db.scalars(stmt).all()
    _auto_recover_stale_runs(db, runs)
    return _serialize_run_summaries_batched(db, runs)


def _auto_recover_stale_runs(db: Session, runs: list[RecommendationRun]) -> int:
    """自动将心跳超时的活跃运行标记为 interrupted。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recovered = 0
    for run in runs:
        if run.status not in RUN_ACTIVE_STATUSES:
            continue
        if run.heartbeat_at and (now - run.heartbeat_at).total_seconds() > RUN_HEARTBEAT_STALE_SECONDS:
            run.status = "interrupted"
            run.interrupted_at = now
            run.finished_at = now
            run.heartbeat_at = now
            run.error_message = f"心跳超时（>{RUN_HEARTBEAT_STALE_SECONDS}s），自动标记为中断"
            run.progress_phase = "interrupted"
            if not run.progress_message:
                run.progress_message = "心跳超时自动中断"
            recovered += 1
    if recovered:
        db.commit()
        invalidate_bootstrap_cache()
        logger.warning("auto-recovered %d stale runs", recovered)
    return recovered


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


def delete_preset(db: Session, preset_id: str) -> bool:
    preset = db.get(Preset, preset_id)
    if preset is None:
        logger.warning("delete_preset: preset_id=%s not found, treating as idempotent success", preset_id)
        invalidate_bootstrap_cache()
        return True
    db.execute(text("DELETE FROM preset_skills WHERE preset_id = :pid"), {"pid": preset_id})
    db.delete(preset)
    db.commit()
    invalidate_bootstrap_cache()
    return True


def list_run_recommendations(db: Session, run_id: str) -> list[dict[str, Any]]:
    recs = db.scalars(
        select(Recommendation).where(Recommendation.run_id == run_id).order_by(Recommendation.final_score.desc())
    ).all()
    return _serialize_recommendations_batched(db, recs)


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


def _enqueue_background_run(run_id: str, payload: dict[str, Any]) -> None:
    payload["wait_for_completion"] = True
    _run_queue.put((run_id, payload))
    _ensure_run_queue_worker()


def _ensure_run_queue_worker() -> None:
    global _run_queue_worker_started
    if _run_queue_worker_started:
        return
    with _run_queue_lock:
        if _run_queue_worker_started:
            return
        worker = threading.Thread(target=_run_queue_worker, name="ashare-run-worker", daemon=True)
        worker.start()
        _run_queue_worker_started = True


def _run_queue_worker() -> None:
    while True:
        run_id, payload = _run_queue.get()
        db = SessionLocal()
        try:
            _update_run_progress(
                db,
                run_id,
                phase="starting",
                current=0,
                total=0,
                message="后台任务已接管，开始准备运行",
                status="running",
            )
            asyncio.run(run_workbench(db, payload, existing_run_id=run_id))
        except Exception as exc:
            logger.exception("background run failed: %s", run_id)
            _mark_run_failed(db, run_id, exc)
        finally:
            db.close()
            _run_queue.task_done()


def _mark_run_failed(db: Session, run_id: str, exc: Exception) -> None:
    try:
        run = db.get(RecommendationRun, run_id)
        if run is None:
            return
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        run.heartbeat_at = run.finished_at
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = select(RecommendationRun).where(RecommendationRun.status.in_(tuple(RUN_ACTIVE_STATUSES)))
    if run_ids is not None:
        stmt = stmt.where(RecommendationRun.run_id.in_(run_ids))
    runs = db.scalars(stmt).all()
    recovered = 0
    for run in runs:
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
    """将当前阶段写入断点，commit 确保中断后可恢复。"""
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return
    run.checkpoint_phase = phase
    run.heartbeat_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
    run.run_mode = params["run_mode"]
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
    run.params_json = _dumps(params)
    run.market_snapshot_json = run.market_snapshot_json or _dumps({})
    run.result_summary_json = _dumps({"agent_runtime": "starting"})
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
    db.commit()
    invalidate_bootstrap_cache()
    _record_progress_commit(run)
    return run


async def run_workbench(db: Session, payload: dict[str, Any], existing_run_id: str | None = None) -> dict[str, Any]:
    run_id = existing_run_id or _new_run_id()
    is_resume = bool(existing_run_id)

    # 读取断点信息
    checkpoint_phase: str | None = None
    if is_resume:
        existing_run = db.get(RecommendationRun, run_id)
        if existing_run and existing_run.checkpoint_phase:
            checkpoint_phase = existing_run.checkpoint_phase
        logger.info("resume run %s from checkpoint %s", run_id, checkpoint_phase or "start")

    def _should_run(phase: str) -> bool:
        """断点之前的阶段跳过，从断点下一阶段开始执行。"""
        if not checkpoint_phase:
            return True
        return _phase_index(phase) > _phase_index(checkpoint_phase)

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
        preset = db.get(Preset, payload["preset_id"]) if payload.get("preset_id") else None
        if payload.get("preset_id") and preset is None:
            raise ValueError("预设不存在，请刷新后重试")
        params = _merge_run_params(db, preset, payload)
        if params["run_mode"] not in {"formal", "experiment"}:
            raise ValueError("运行模式仅支持 formal 或 experiment")
        selected_skills = _select_skills_for_run(db, skill_map, params["run_mode"], params["skills"])
        if not selected_skills:
            raise ValueError("没有可运行的 Skill，请检查预设配置或 Skill 状态")
        run = _persist_run_start(db, run_id, params, selected_skills)
        _save_checkpoint(db, run_id, "prepare")
    else:
        # 从已有 Run 重建状态
        run = existing_run
        skills = sync_skills(db)
        skill_map = {skill.skill_id: skill for skill in skills}
        params = _loads(run.params_json, {})
        selected_skill_rows = _loads(run.selected_skills_json, [])
        selected_skills = _select_skills_for_run(db, skill_map, params.get("run_mode") or run.run_mode, selected_skill_rows)
        if not selected_skills:
            raise ValueError("恢复任务时没有可运行的 Skill，请检查 Skill 状态后重新发起运行")
        preset = db.get(Preset, run.preset_id) if run.preset_id else None

    data_source = _normalize_data_source(params.get("data_source"))
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
        if data_source == DATA_SOURCE_REALTIME:
            stocks, snapshot = get_market_universe()
        elif data_source == DATA_SOURCE_LOCAL_POOL_PREFERRED:
            stocks, snapshot = get_market_universe_from_pool(db, refresh=True)
        else:
            stocks, snapshot = get_market_universe_from_pool(db, refresh=False)
    except Exception as exc:
        _mark_run_failed(db, run_id, exc)
        raise
    snapshot.setdefault("breadth", {})["run_data_source_mode"] = data_source
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
    else:
        logger.info("skip stock/bar persistence for local-pool run %s; mode=%s stocks=%d", run_id, data_source, len(stocks))
    scoped_stocks = _apply_market_scope_to_stocks(db, stocks, params)
    if not scoped_stocks:
        raise ValueError("当前市场范围内没有可运行的股票，请调整预设范围后重试")
    snapshot.setdefault("breadth", {})["scoped_candidate_count"] = len(scoped_stocks)
    min_prefilter_count = _resolve_prefilter_target_count(params, selected_skill_count=len(selected_skills))
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

    # ── 辅助数据刷新：概念板块 / 龙虎榜 / 资金流向 / 新闻公告 ──
    aux_audit: dict[str, Any] = {
        "concept_sectors": {"status": "skipped", "count": 0, "elapsed_ms": 0},
        "dragon_tiger": {"status": "skipped", "count": 0, "elapsed_ms": 0},
        "capital_flows": {"status": "skipped", "count": 0, "elapsed_ms": 0},
        "news": {"status": "skipped", "count": 0, "elapsed_ms": 0},
    }
    settings = get_settings()
    skip_aux_refresh = bool(params.get("skip_aux_refresh"))
    aux_prefetch_count = _resolve_aux_prefetch_count(
        len(prefiltered_stocks),
        min_prefilter_count,
        run_mode=str(params.get("run_mode") or "formal"),
        skip_aux_refresh=skip_aux_refresh,
        max_codes=settings.auxiliary_prefetch_max_codes,
    )
    aux_prefetch_codes = _select_aux_prefetch_codes(prefiltered_stocks, aux_prefetch_count)
    _update_run_progress(
        db,
        run_id,
        phase="auxiliary_data",
        current=0,
        total=4,
        message="刷新辅助数据：概念板块、龙虎榜、资金流向、新闻公告",
        status="running",
    )
    # 概念板块：全量刷新（数据量小，覆盖所有 A 股板块）
    if skip_aux_refresh:
        for key in aux_audit:
            aux_audit[key] = {"status": "skipped_fast_run", "count": 0, "elapsed_ms": 0}
    elif settings.enable_concept_sector_fetch:
        if _has_fresh_concept_sector_cache(db, snapshot["trade_date"], settings.concept_sector_cache_ttl_seconds):
            aux_audit["concept_sectors"] = {
                "status": "cached",
                "count": int(
                    db.scalar(
                        select(func.count()).select_from(ConceptSector).where(ConceptSector.trade_date == snapshot["trade_date"])
                    ) or 0
                ),
                "elapsed_ms": 0,
            }
        else:
            t0 = time.perf_counter()
            try:
                sectors = refresh_concept_sectors(db, trade_date=snapshot["trade_date"])
                aux_audit["concept_sectors"] = {
                    "status": "success",
                    "count": len(sectors),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                }
            except Exception as exc:
                db.rollback()
                aux_audit["concept_sectors"] = {
                    "status": "failed",
                    "count": 0,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "error": str(exc),
                }
    _update_run_progress(
        db,
        run_id,
        phase="auxiliary_data",
        current=1,
        total=4,
        message="概念板块数据检查完成",
        status="running",
    )
    # 龙虎榜：全量刷新（每日上榜股票总量小）
    if not skip_aux_refresh and settings.enable_dragon_tiger_fetch:
        if _has_fresh_dragon_tiger_cache(db, snapshot["trade_date"], settings.dragon_tiger_cache_ttl_seconds):
            aux_audit["dragon_tiger"] = {
                "status": "cached",
                "count": int(
                    db.scalar(
                        select(func.count()).select_from(DragonTiger).where(DragonTiger.trade_date == snapshot["trade_date"])
                    )
                    or 0
                ),
                "elapsed_ms": 0,
                "covered_trade_date": snapshot["trade_date"],
            }
        else:
            t0 = time.perf_counter()
            try:
                dt_rows = refresh_dragon_tiger(db, trade_date=snapshot["trade_date"])
                aux_audit["dragon_tiger"] = {
                    "status": "success",
                    "count": len(dt_rows),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "covered_trade_date": snapshot["trade_date"],
                }
            except Exception as exc:
                db.rollback()
                aux_audit["dragon_tiger"] = {
                    "status": "failed",
                    "count": 0,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "error": str(exc),
                }
    _update_run_progress(
        db,
        run_id,
        phase="auxiliary_data",
        current=2,
        total=4,
        message="龙虎榜数据检查完成",
        status="running",
    )
    # 资金流向 + 新闻：只补当前范围内、市值靠前股票的缺失缓存。
    if not skip_aux_refresh and settings.enable_capital_flow_fetch and aux_prefetch_codes:
        stale_capital_flow_codes = _stale_capital_flow_codes(
            db,
            aux_prefetch_codes,
            snapshot["trade_date"],
            settings.capital_flow_cache_ttl_seconds,
        )
        if stale_capital_flow_codes:
            t0 = time.perf_counter()
            try:
                cf_result = refresh_capital_flows_for_codes(db, stale_capital_flow_codes, trade_date=snapshot["trade_date"])
                aux_audit["capital_flows"] = {
                    "status": "success",
                    "count": len(cf_result),
                    "requested_count": len(stale_capital_flow_codes),
                    "checked_codes": aux_prefetch_codes,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                }
            except Exception as exc:
                db.rollback()
                aux_audit["capital_flows"] = {
                    "status": "failed",
                    "count": 0,
                    "requested_count": len(stale_capital_flow_codes),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "error": str(exc),
                }
        else:
            aux_audit["capital_flows"] = {
                "status": "cached",
                "count": len(aux_prefetch_codes),
                "requested_count": 0,
                "checked_codes": aux_prefetch_codes,
                "elapsed_ms": 0,
            }
    _update_run_progress(
        db,
        run_id,
        phase="auxiliary_data",
        current=3,
        total=4,
        message="资金流向数据检查完成",
        status="running",
    )
    if not skip_aux_refresh and settings.enable_news_fetch and aux_prefetch_codes:
        stale_news_codes = _stale_news_codes(
            db,
            aux_prefetch_codes,
            snapshot["trade_date"],
            settings.news_cache_ttl_seconds,
        )
        if stale_news_codes:
            t0 = time.perf_counter()
            try:
                news_result = refresh_news_for_codes(db, stale_news_codes, trade_date=snapshot["trade_date"])
                checked_codes = sorted(
                    {
                        *[code for code in aux_prefetch_codes if code not in set(stale_news_codes)],
                        *list(getattr(news_result, "checked_codes", [])),
                    }
                )
                aux_audit["news"] = {
                    "status": "success",
                    "count": len(news_result),
                    "requested_count": len(stale_news_codes),
                    "checked_codes": checked_codes,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                }
            except Exception as exc:
                db.rollback()
                aux_audit["news"] = {
                    "status": "failed",
                    "count": 0,
                    "requested_count": len(stale_news_codes),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "error": str(exc),
                }
        else:
            aux_audit["news"] = {
                "status": "cached",
                "count": len(aux_prefetch_codes),
                "requested_count": 0,
                "checked_codes": aux_prefetch_codes,
                "elapsed_ms": 0,
            }
    _update_run_progress(
        db,
        run_id,
        phase="auxiliary_data",
        current=4,
        total=4,
        message="辅助数据刷新完成",
        status="running",
    )
    snapshot.setdefault("breadth", {})["auxiliary_data_audit"] = aux_audit
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
    run.run_mode = params["run_mode"]
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
    run.market_snapshot_json = _dumps(snapshot)
    run.result_summary_json = _dumps({})
    run.status = "running"
    run.error_message = None
    run.heartbeat_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run.progress_phase = "feature_build"
    run.progress_current = len(research_contexts)
    run.progress_total = len(research_contexts)
    run.progress_message = f"特征构建完成，进入规则扫描：{len(research_contexts)} 只"
    run.interrupted_at = None
    db.flush()
    db.commit()  # Persist immediately so the run record survives failures
    invalidate_bootstrap_cache()

    agent_session_id = f"sess_{run_id}"
    all_skill_objs = [s["skill"] for s in selected_skills]

    try:
        AgentSessionRegistry.register(
            session_id=agent_session_id,
            session_factory=lambda: SessionLocal(),
            skills=all_skill_objs,
            snapshot=snapshot,
        )

        recent_codes = _recent_recommended_codes(db, snapshot["trade_date"])
        market_score = market_regime_score(snapshot)
        calibrated_weights = _load_calibrated_agg_weights(db)
        cards: list[dict[str, Any]] = []

        # ═══ 阶段一：策略规则全市场扫描（所有基础过滤合格股票，无 LLM） ═══
        settings = get_settings()
        agent_review_disabled = bool(params.get("disable_agent_review"))
        skill_review_limit = _resolve_skill_review_limit(params, settings, selected_skill_count=len(selected_skills))
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
        gate_result = _build_skill_rule_gate(prelim_entries, selected_skills, settings)
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
        skill_gate_audit = _finalize_skill_gate_audit(
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
        for cost in drain_api_costs():
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
            )
            if card.get("agent_logs"):
                candidate_success_count += 1
            if params["hide_recent_repeats"] and card["recent_repeat"]:
                continue
            cards.append(card)

        logger.info(
            "Agent review: %d/%d skill inputs got at least one successful review",
            candidate_success_count, len(candidate_results),
        )
        pipeline_audit["skill_review_success_count"] = candidate_success_count
        pipeline_audit["rule_only_fallback_count"] = 0
        scored_cards = list(cards)
        cards = apply_pool_rules(
            scored_cards,
            output_count=int(params["output_count"]),
            score_line=float(params["score_line"]),
            market_snapshot=snapshot,
            risk_template=params["risk_template"],
        )
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
        saved: list[Recommendation] = []
        for index, card in enumerate(cards, start=1):
            recommendation_id = f"rec_{snapshot['trade_date'].replace('-', '')}_{index:03d}_{uuid.uuid4().hex[:4]}"
            rec = Recommendation(
                recommendation_id=recommendation_id,
                run_id=run_id,
                trade_date=snapshot["trade_date"],
                code=card["code"],
                name=card["name"],
                industry=card.get("industry"),
                recommend_price=float(card.get("recommend_price") or 0),
                bucket="",
                final_score=float(card["final_score"]),
                consensus_score=float(card.get("consensus_score") or 0),
                setup_type=card.get("setup_type"),
                entry_rule=card.get("entry_rule"),
                invalid_rule=card.get("invalid_rule"),
                rationale=card.get("rationale"),
                risk_tags_json=_dumps(card.get("risk_flags", [])),
                info_tags_json=_dumps(card.get("info_tags", [])),
                recent_repeat=bool(card.get("recent_repeat")),
                feature_snapshot_json=_dumps(
                    {
                        **(card.get("feature_snapshot", {}) or {}),
                        "recommend_price": card.get("recommend_price"),
                        "stop_loss": card.get("stop_loss"),
                        "take_profit": card.get("take_profit"),
                        "market_discipline": card.get("market_discipline", {}),
                        "aggregation_weights": card.get("aggregation_weights", {}),
                        "raw_component_scores": card.get("raw_component_scores", {}),
                        "weighted_component_scores": card.get("weighted_component_scores", {}),
                        "score_breakdown": card.get("score_breakdown", {}),
                        "score_line_meta": card.get("score_line_meta", {}),
                        "execution_viability": card.get("execution_viability", {}),
                        "component_scale": card.get("component_scale", 1.0),
                        "consensus_boost": card.get("consensus_boost", 1.0),
                    }
                ),
                market_context_json=_dumps(card.get("market_snapshot", {})),
                skill_breakdown_json=_dumps(card.get("strategy_signals", [])),
                agent_summary_json=_dumps(
                    {
                        "mode": "auditable_skill_agents",
                        "logs": card.get("agent_logs", []),
                        "data_source": (card.get("market_snapshot", {}).get("breadth") or {}).get("data_source"),
                    }
                ),
            )
            db.add(rec)
            db.flush()
            for log in card.get("agent_logs", []):
                db.add(
                    AgentDecisionLog(
                        id=f"log_{uuid.uuid4().hex}",
                        recommendation_id=recommendation_id,
                        run_id=run_id,
                        agent_name=log["agent_name"],
                        output_json=_dumps(log["output"]),
                    )
                )
            saved.append(rec)
            _update_run_progress(
                db,
                run_id,
                phase="persist",
                current=index,
                total=len(cards),
                message=f"保存推荐 {index}/{len(cards)}",
                status="running",
                commit=False,
            )

        run.recommended_count = len(saved)
        run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        run.status = "completed"
        run.heartbeat_at = run.finished_at
        run.progress_phase = "completed"
        run.progress_current = len(saved)
        run.progress_total = len(saved)
        run.progress_message = "运行完成"
        run.filter_audit_json = _dumps(pipeline_audit.get("filter_audit") or {})
        run.result_summary_json = _dumps(_build_run_summary(cards, saved, snapshot, _strip_audit_rows(pipeline_audit)))
        db.commit()
        invalidate_bootstrap_cache()
        return {
            "run": _serialize_run_summary(db, run),
            "recommendations": [_serialize_recommendation(db, rec) for rec in saved],
        }
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        run.heartbeat_at = run.finished_at
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
    return {
        "run": _serialize_run_summary(db, run),
        "stage_summary": audit.get("stage_summary", []),
        "distributions": audit.get("distributions", {}),
        "reason_counts": audit.get("reason_counts", {}),
        "rows": audit.get("rows", []),
        "empty_reason": audit.get("empty_reason"),
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


def label_pending_outcomes(db: Session, horizon_days: int = 5, run_mode: str | None = None) -> dict[str, Any]:
    result = label_pending_outcomes_service(db, horizon_days=horizon_days, run_mode=run_mode)
    labeled = int(result.get("labeled") or 0)
    if labeled:
        invalidate_bootstrap_cache()
    return {"labeled": labeled}


def _serialize_preset(db: Session, preset: Preset) -> dict[str, Any]:
    skills = db.scalars(
        select(PresetSkill).where(PresetSkill.preset_id == preset.preset_id).order_by(PresetSkill.sort_order.asc())
    ).all()
    return {
        "preset_id": preset.preset_id,
        "name": preset.name,
        "description": preset.description,
        "run_mode": preset.run_mode,
        "market_scope": preset.market_scope,
        "market_scope_value": preset.market_scope_value,
        "risk_template": preset.risk_template,
        "score_line": preset.score_line,
        "output_count": preset.output_count,
        "data_source": preset.data_source,
        "hide_recent_repeats": preset.hide_recent_repeats,
        "status": preset.status,
        "skills": [
            {
                "skill_id": row.skill_id,
                "weight": row.weight,
                "sort_order": row.sort_order,
                "name": (registry.name if (registry := db.get(SkillRegistry, row.skill_id)) else row.skill_id),
            }
            for row in skills
        ],
    }


def _serialize_presets_batched(db: Session, presets: list[Preset]) -> list[dict[str, Any]]:
    if not presets:
        return []
    preset_ids = [preset.preset_id for preset in presets]
    skill_rows = db.scalars(
        select(PresetSkill)
        .where(PresetSkill.preset_id.in_(preset_ids))
        .order_by(PresetSkill.preset_id.asc(), PresetSkill.sort_order.asc())
    ).all()
    skill_ids = {row.skill_id for row in skill_rows}
    skill_name_map = {}
    if skill_ids:
        skill_name_map = {
            row.skill_id: row.name
            for row in db.scalars(select(SkillRegistry).where(SkillRegistry.skill_id.in_(skill_ids))).all()
        }
    skills_by_preset: dict[str, list[dict[str, Any]]] = {}
    for row in skill_rows:
        skills_by_preset.setdefault(row.preset_id, []).append(
            {
                "skill_id": row.skill_id,
                "weight": row.weight,
                "sort_order": row.sort_order,
                "name": skill_name_map.get(row.skill_id, row.skill_id),
            }
        )

    return [
        {
            "preset_id": preset.preset_id,
            "name": preset.name,
            "description": preset.description,
            "run_mode": preset.run_mode,
            "market_scope": preset.market_scope,
            "market_scope_value": preset.market_scope_value,
            "risk_template": preset.risk_template,
            "score_line": preset.score_line,
            "output_count": preset.output_count,
            "data_source": preset.data_source,
            "hide_recent_repeats": preset.hide_recent_repeats,
            "status": preset.status,
            "skills": skills_by_preset.get(preset.preset_id, []),
        }
        for preset in presets
    ]


def _new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _latest_trade_date(db: Session) -> str:
    latest = db.scalar(select(MarketSnapshot.trade_date).order_by(MarketSnapshot.trade_date.desc()).limit(1))
    if latest:
        return str(latest)
    latest_quote = db.scalar(select(StockQuote.trade_date).order_by(StockQuote.trade_date.desc()).limit(1))
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
            "mode": item["mode"],
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
    summary = _loads(run.result_summary_json, {})
    heartbeat_age_seconds = None
    if run.heartbeat_at:
        heartbeat_age_seconds = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - run.heartbeat_at).total_seconds()))
    progress_total = int(run.progress_total or 0)
    progress_current = int(run.progress_current or 0)
    progress_pct = compute_progress_pct(progress_current, progress_total)
    is_stale = (
        run.status in RUN_ACTIVE_STATUSES
        and heartbeat_age_seconds is not None
        and heartbeat_age_seconds > RUN_HEARTBEAT_STALE_SECONDS
    )
    return {
        "run_id": run.run_id,
        "run_name": run.run_name,
        "preset_id": run.preset_id,
        "preset_name": preset.name if preset else "临时运行",
        "run_mode": run.run_mode,
        "trade_date": run.trade_date,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "market_scope": run.market_scope,
        "market_scope_value": run.market_scope_value,
        "risk_template": run.risk_template,
        "score_line": run.score_line,
        "universe_count": run.universe_count,
        "recommended_count": run.recommended_count,
        "status": run.status,
        "error_message": run.error_message,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_stale_seconds": RUN_HEARTBEAT_STALE_SECONDS,
        "is_stale": is_stale,
        "progress_phase": run.progress_phase,
        "progress_current": progress_current,
        "progress_total": progress_total,
        "progress_pct": progress_pct,
        "progress_message": run.progress_message,
        "interrupted_at": run.interrupted_at.isoformat() if run.interrupted_at else None,
        "skill_count": len(_loads(run.selected_skills_json, [])),
        "hit_rate": hit_rate,
        "result_summary": summary,
        "checkpoint_phase": run.checkpoint_phase,
    }


def _serialize_run_summary(db: Session, run: RecommendationRun) -> dict[str, Any]:
    preset = db.get(Preset, run.preset_id) if run.preset_id else None
    summary = _loads(run.result_summary_json, {})
    outcome_ids = [rec.recommendation_id for rec in db.scalars(select(Recommendation).where(Recommendation.run_id == run.run_id)).all()]
    outcomes = (
        db.scalars(select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_(outcome_ids))).all()
        if outcome_ids
        else []
    )
    executed = [outcome for outcome in outcomes if is_trade_executed(outcome)]
    success_count = sum(1 for outcome in executed if is_accurate_recommendation(outcome))
    hit_rate = round(success_count / len(executed), 3) if executed else 0.0
    heartbeat_age_seconds = None
    if run.heartbeat_at:
        heartbeat_age_seconds = max(0, int((datetime.now(timezone.utc).replace(tzinfo=None) - run.heartbeat_at).total_seconds()))
    progress_total = int(run.progress_total or 0)
    progress_current = int(run.progress_current or 0)
    progress_pct = compute_progress_pct(progress_current, progress_total)
    is_stale = (
        run.status in RUN_ACTIVE_STATUSES
        and heartbeat_age_seconds is not None
        and heartbeat_age_seconds > RUN_HEARTBEAT_STALE_SECONDS
    )
    return {
        "run_id": run.run_id,
        "run_name": run.run_name,
        "preset_id": run.preset_id,
        "preset_name": preset.name if preset else "临时运行",
        "run_mode": run.run_mode,
        "trade_date": run.trade_date,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "market_scope": run.market_scope,
        "market_scope_value": run.market_scope_value,
        "risk_template": run.risk_template,
        "score_line": run.score_line,
        "universe_count": run.universe_count,
        "recommended_count": run.recommended_count,
        "status": run.status,
        "error_message": run.error_message,
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_stale_seconds": RUN_HEARTBEAT_STALE_SECONDS,
        "is_stale": is_stale,
        "progress_phase": run.progress_phase,
        "progress_current": progress_current,
        "progress_total": progress_total,
        "progress_pct": progress_pct,
        "progress_message": run.progress_message,
        "interrupted_at": run.interrupted_at.isoformat() if run.interrupted_at else None,
        "skill_count": len(_loads(run.selected_skills_json, [])),
        "hit_rate": hit_rate,
        "result_summary": summary,
        "checkpoint_phase": run.checkpoint_phase,
    }


def _serialize_recommendations_batched(db: Session, recs: list[Recommendation]) -> list[dict[str, Any]]:
    """批量序列化推荐，避免 N+1 WatchItem 查询。"""
    if not recs:
        return []
    rec_ids = [rec.recommendation_id for rec in recs]
    watch_map: dict[str, WatchItem] = {}
    watches = db.scalars(select(WatchItem).where(WatchItem.source_recommendation_id.in_(rec_ids))).all()
    for w in watches:
        watch_map[w.source_recommendation_id] = w
    return [_serialize_recommendation_cached(rec, watch_map.get(rec.recommendation_id)) for rec in recs]


def _serialize_recommendation_cached(rec: Recommendation, watch: WatchItem | None) -> dict[str, Any]:
    feature_snapshot = _loads(rec.feature_snapshot_json, {})
    market_context = _loads(rec.market_context_json, {})
    if "aggregation_weights" not in feature_snapshot:
        weights = market_regime_weights(market_context)
        feature_snapshot["aggregation_weights"] = weights
    return {
        "recommendation_id": rec.recommendation_id,
        "run_id": rec.run_id,
        "trade_date": rec.trade_date,
        "recommended_at": rec.recommended_at.isoformat(),
        "code": rec.code,
        "name": rec.name,
        "industry": rec.industry,
        "recommend_price": rec.recommend_price,
        "stop_loss": feature_snapshot.get("stop_loss"),
        "take_profit": feature_snapshot.get("take_profit"),
        "bucket": "",
        "final_score": rec.final_score,
        "consensus_score": rec.consensus_score,
        "setup_type": rec.setup_type,
        "entry_rule": rec.entry_rule,
        "invalid_rule": rec.invalid_rule,
        "rationale": rec.rationale,
        "risk_tags": _loads(rec.risk_tags_json, []),
        "info_tags": _loads(rec.info_tags_json, []),
        "feature_snapshot": feature_snapshot,
        "market_context": market_context,
        "skill_breakdown": _loads(rec.skill_breakdown_json, []),
        "agent_logs": (_loads(rec.agent_summary_json, {}) or {}).get("logs", []),
        "score_breakdown": feature_snapshot.get("score_breakdown", {}),
        "score_line_meta": feature_snapshot.get("score_line_meta", {}),
        "execution_viability": feature_snapshot.get("execution_viability", {}),
        "status": rec.status,
        "recent_repeat": rec.recent_repeat,
        "watch_status": watch.watch_status if watch else "none",
        "watch_note": watch.note if watch else None,
    }


def _serialize_recommendation(db: Session, rec: Recommendation) -> dict[str, Any]:
    watch = db.scalar(select(WatchItem).where(WatchItem.source_recommendation_id == rec.recommendation_id))
    feature_snapshot = _loads(rec.feature_snapshot_json, {})
    market_context = _loads(rec.market_context_json, {})
    if "aggregation_weights" not in feature_snapshot:
        weights = market_regime_weights(market_context)
        feature_snapshot["aggregation_weights"] = weights
    return {
        "recommendation_id": rec.recommendation_id,
        "run_id": rec.run_id,
        "trade_date": rec.trade_date,
        "recommended_at": rec.recommended_at.isoformat(),
        "code": rec.code,
        "name": rec.name,
        "industry": rec.industry,
        "recommend_price": rec.recommend_price,
        "stop_loss": feature_snapshot.get("stop_loss"),
        "take_profit": feature_snapshot.get("take_profit"),
        "bucket": "",
        "final_score": rec.final_score,
        "consensus_score": rec.consensus_score,
        "setup_type": rec.setup_type,
        "entry_rule": rec.entry_rule,
        "invalid_rule": rec.invalid_rule,
        "rationale": rec.rationale,
        "risk_tags": _loads(rec.risk_tags_json, []),
        "info_tags": _loads(rec.info_tags_json, []),
        "feature_snapshot": feature_snapshot,
        "market_context": market_context,
        "skill_breakdown": _loads(rec.skill_breakdown_json, []),
        "agent_logs": (_loads(rec.agent_summary_json, {}) or {}).get("logs", []),
        "score_breakdown": feature_snapshot.get("score_breakdown", {}),
        "score_line_meta": feature_snapshot.get("score_line_meta", {}),
        "execution_viability": feature_snapshot.get("execution_viability", {}),
        "status": rec.status,
        "recent_repeat": rec.recent_repeat,
        "watch_status": watch.watch_status if watch else "none",
        "watch_note": watch.note if watch else None,
    }


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


def _resolve_prefilter_target_count(params: dict[str, Any], selected_skill_count: int) -> int:
    score_line = float(params.get("score_line") or 72.0)
    base = 36 if str(params.get("run_mode") or "formal") == "formal" else 24
    if score_line < 70:
        base += 12
    elif score_line >= 78:
        base -= 6
    return max(base, selected_skill_count * 12, 24)


def _resolve_skill_review_limit(params: dict[str, Any], settings: Any, selected_skill_count: int = 1) -> int:
    if bool(params.get("disable_agent_review")):
        return 0
    skill_review_limit = int(params.get("agent_review_limit") or settings.market_skill_review_limit or 0)
    if skill_review_limit > 0:
        return skill_review_limit
    score_line = float(params.get("score_line") or 72.0)
    base = 14 if params.get("run_mode") == "experiment" else 28
    if score_line < 70:
        base += 10
    elif score_line >= 78:
        base -= 6
    base = max(base, selected_skill_count * 8)
    return min(max(base, 8), 60)


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
            signal["skill_mode"] = selected["mode"]
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
        signal["skill_mode"] = selected["mode"]
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
    card["feature_snapshot"] = feature_snapshot
    card["stop_loss"] = card["feature_snapshot"].get("stop_loss")
    card["take_profit"] = card["feature_snapshot"].get("take_profit")
    card["market_snapshot"] = snapshot
    card["agent_logs"] = review_log["logs"]
    card["recent_repeat"] = stock["code"] in (recent_codes or set())
    return card


def _build_skill_rule_gate(
    prelim_entries: list[dict[str, Any]],
    selected_skills: list[dict[str, Any]],
    settings: Any,
) -> dict[str, Any]:
    min_score = float(getattr(settings, "market_skill_rule_gate_min_score", 58.0) or 58.0)
    min_per_skill = max(0, int(getattr(settings, "market_skill_rule_gate_min_per_skill", 20) or 0))
    min_total = max(0, int(getattr(settings, "market_skill_rule_gate_min_total", 20) or 0))
    # 动态 max_per_skill：规则合格数的 min(10%, 30)，至少 20
    _config_max_per_skill = max(0, int(getattr(settings, "market_skill_rule_gate_max_per_skill", 0) or 0))
    max_total = max(min_total, int(getattr(settings, "market_skill_rule_gate_max_total", 120) or 0))
    selected_ids = [str(item["skill_id"]) for item in selected_skills]
    skill_count = len(selected_ids)
    by_code: dict[str, dict[str, Any]] = {}
    skill_rows: dict[str, list[dict[str, Any]]] = {skill_id: [] for skill_id in selected_ids}
    per_skill_audit: dict[str, dict[str, Any]] = {
        skill_id: {
            "scanned": len(prelim_entries),
            "rule_eligible": 0,
            "gate_pass": 0,
            "llm_reviewed": 0,
            "skipped_hard_veto": 0,
            "skipped_low_score": 0,
            "skipped_no_rule": 0,
            "high_quality_count": 0,
            "mid_quality_count": 0,
        }
        for skill_id in selected_ids
    }

    # Phase 1: Scan and classify
    for entry in prelim_entries:
        stock = entry["stock"]
        code = str(stock.get("code") or "")
        by_code[code] = {
            **entry,
            "review_skill_ids": [],
            "skill_gate_reasons": {},
            "skill_gate_scores": {},
            "skill_quality_tier": {},
        }
        for skill_id in selected_ids:
            signal = (entry.get("rule_signals") or {}).get(skill_id)
            if not signal:
                per_skill_audit[skill_id]["skipped_no_rule"] += 1
                continue
            if signal.get("hard_veto"):
                per_skill_audit[skill_id]["skipped_hard_veto"] += 1
                by_code[code]["skill_gate_reasons"][skill_id] = signal.get("veto_reason") or "规则硬否决"
                continue
            gate_score = _skill_gate_score(signal, entry)
            by_code[code]["skill_gate_scores"][skill_id] = gate_score
            if gate_score >= min_score:
                per_skill_audit[skill_id]["rule_eligible"] += 1
                # 质量分层
                if gate_score >= 80:
                    tier = "high"
                    per_skill_audit[skill_id]["high_quality_count"] += 1
                elif gate_score >= 70:
                    tier = "mid"
                    per_skill_audit[skill_id]["mid_quality_count"] += 1
                else:
                    tier = "standard"
                skill_rows[skill_id].append(
                    {
                        "code": code,
                        "score": gate_score,
                        "tier": tier,
                        "reason": _skill_gate_reason(signal, gate_score, min_score),
                    }
                )
            else:
                per_skill_audit[skill_id]["skipped_low_score"] += 1
                by_code[code]["skill_gate_reasons"][skill_id] = f"规则门分 {round(gate_score, 2)} 低于 {round(min_score, 2)}"

    # Phase 2: Dynamic per-skill cap + quality-tiered selection
    selected_pairs: list[dict[str, Any]] = []
    selected_pair_keys: set[tuple[str, str]] = set()

    for skill_id, rows in skill_rows.items():
        eligible = per_skill_audit[skill_id]["rule_eligible"]
        if not rows:
            continue
        # 动态 max_per_skill: max(20, min(eligible * 10%, 30))
        if _config_max_per_skill > 0:
            dynamic_max = max(min_per_skill, min(eligible, _config_max_per_skill))
        else:
            dynamic_max = max(min_per_skill, min(eligible, max(20, int(eligible * 0.1))))
        # 上限 30（避免 LLM 调用过多）
        dynamic_max = min(dynamic_max, 30)
        per_skill_audit[skill_id]["dynamic_max_per_skill"] = dynamic_max

        rows.sort(key=lambda row: float(row["score"]), reverse=True)

        # 质量分层深审：高分段（>=80）全部深审
        high_tier = [r for r in rows if r["tier"] == "high"]
        mid_tier = [r for r in rows if r["tier"] == "mid"]
        standard_tier = [r for r in rows if r["tier"] == "standard"]

        selected_for_skill: list[dict[str, Any]] = []
        # 高分全部入选
        for row in high_tier:
            if len(selected_for_skill) >= dynamic_max:
                break
            selected_for_skill.append(row)

        # 中分按剩余容量采样（至少选一半中分段）
        remaining = dynamic_max - len(selected_for_skill)
        if remaining > 0 and mid_tier:
            mid_sample = min(len(mid_tier), max(len(mid_tier) // 2, remaining))
            selected_for_skill.extend(mid_tier[:mid_sample])

        # 标准分段填满剩余容量
        remaining = dynamic_max - len(selected_for_skill)
        if remaining > 0 and standard_tier:
            selected_for_skill.extend(standard_tier[:remaining])

        # 实际入选数量可能不足 min_per_skill，从剩余高分填充
        if len(selected_for_skill) < min_per_skill:
            remaining_rows = [r for r in rows if r not in selected_for_skill]
            selected_for_skill.extend(remaining_rows[: min_per_skill - len(selected_for_skill)])

        for row in selected_for_skill:
            key = (row["code"], skill_id)
            if key in selected_pair_keys:
                continue
            selected_pair_keys.add(key)
            selected_pairs.append({**row, "skill_id": skill_id})
            per_skill_audit[skill_id]["gate_pass"] += 1

    # Phase 3: Global cap
    selected_pairs.sort(key=lambda row: float(row["score"]), reverse=True)
    if max_total and len(selected_pairs) > max_total:
        selected_pairs = selected_pairs[:max_total]
        selected_pair_keys = {(row["code"], row["skill_id"]) for row in selected_pairs}

    # Minimum total fallback
    if len(selected_pairs) < min_total:
        fallback_rows: list[dict[str, Any]] = []
        for skill_id, rows in skill_rows.items():
            for row in rows:
                key = (row["code"], skill_id)
                if key not in selected_pair_keys:
                    fallback_rows.append({**row, "skill_id": skill_id})
        fallback_rows.sort(key=lambda row: float(row["score"]), reverse=True)
        for row in fallback_rows[: max(0, min_total - len(selected_pairs))]:
            selected_pair_keys.add((row["code"], row["skill_id"]))
            selected_pairs.append(row)

    # Phase 4: Assign reviews
    for row in selected_pairs:
        entry = by_code.get(row["code"])
        if not entry:
            continue
        skill_id = row["skill_id"]
        if skill_id not in entry["review_skill_ids"]:
            entry["review_skill_ids"].append(skill_id)
            entry["skill_gate_reasons"][skill_id] = row["reason"]
            entry["skill_quality_tier"][skill_id] = row.get("tier", "standard")
            per_skill_audit[skill_id]["llm_reviewed"] += 1

    skill_candidates = [
        entry for entry in by_code.values() if entry.get("review_skill_ids")
    ]
    skill_candidates.sort(
        key=lambda entry: max((entry.get("skill_gate_scores") or {"_": entry.get("prelim_score", 0)}).values()),
        reverse=True,
    )
    non_skill_entries = [
        entry
        for entry in prelim_entries
        if str(entry["stock"].get("code") or "") not in {item["stock"].get("code") for item in skill_candidates}
    ]
    audit_rows = _build_skill_gate_rows(by_code, selected_ids, min_score)
    audit = {
        "mode": "rule_gate_dynamic_tiered",
        "min_score": min_score,
        "min_per_skill": min_per_skill,
        "min_total": min_total,
        "max_total": max_total,
        "quality_tiers": {"high": ">=80 全部深审", "mid": ">=70 按容量采样", "standard": ">=min_score 填充剩余"},
        "skill_rule_candidate_count": len({row["code"] for row in selected_pairs}),
        "skill_gate_candidate_count": len(skill_candidates),
        "skill_agent_call_count": len(selected_pairs),
        "per_skill": per_skill_audit,
        "rows": audit_rows,
        "reason_counts": dict(Counter(row["reason_key"] for row in audit_rows if not row.get("passed"))),
    }
    return {
        "skill_candidates": skill_candidates,
        "non_skill_entries": non_skill_entries,
        "audit": audit,
    }


def _build_skill_gate_rows(
    by_code: dict[str, dict[str, Any]],
    selected_ids: list[str],
    min_score: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code, entry in by_code.items():
        stock = entry.get("stock") or {}
        rule_signals = entry.get("rule_signals") or {}
        gate_scores = {sid: float(score) for sid, score in (entry.get("skill_gate_scores") or {}).items()}
        gate_reasons = dict(entry.get("skill_gate_reasons") or {})
        review_skill_ids = [str(item) for item in (entry.get("review_skill_ids") or [])]
        best_skill_id = None
        best_gate_score = None
        if gate_scores:
            best_skill_id, best_gate_score = max(gate_scores.items(), key=lambda item: float(item[1]))

        if review_skill_ids:
            reason_key = "gate_pass"
            reason = gate_reasons.get(review_skill_ids[0]) or "规则门通过，进入 Skill 深审"
            passed = True
        else:
            present_signals = [rule_signals.get(skill_id) for skill_id in selected_ids if rule_signals.get(skill_id)]
            hard_veto_reasons = [
                str(signal.get("veto_reason") or "规则硬否决")
                for signal in present_signals
                if signal and signal.get("hard_veto")
            ]
            if not present_signals:
                reason_key = "no_rule_signal"
                reason = "无可用规则信号"
            elif best_gate_score is not None and best_gate_score >= min_score:
                reason_key = "gate_capacity"
                reason = "规则门分合格，但未进入本次 Skill 深审容量"
            elif hard_veto_reasons and len(hard_veto_reasons) >= len(present_signals):
                reason_key = "hard_veto"
                reason = "；".join(sorted(set(hard_veto_reasons))[:3])
            elif best_skill_id and best_skill_id in gate_reasons:
                reason_key = "low_gate_score"
                reason = gate_reasons[best_skill_id]
            else:
                reason_key = "low_gate_score"
                reason = f"规则门分低于 {round(min_score, 2)}"
            passed = False

        rows.append(
            {
                "code": code,
                "name": stock.get("name"),
                "industry": stock.get("industry"),
                "stage": "skill_gate",
                "passed": passed,
                "reason_key": reason_key,
                "reason": reason,
                "prelim_score": float(entry.get("prelim_score") or 0),
                "best_skill_id": best_skill_id,
                "best_gate_score": best_gate_score,
                "gate_scores": gate_scores,
                "gate_reasons": gate_reasons,
                "review_skill_ids": review_skill_ids,
                "quality_tier": entry.get("skill_quality_tier") or {},
            }
        )
    return rows


def _skill_gate_score(signal: dict[str, Any], entry: dict[str, Any]) -> float:
    score = float(signal.get("score") or 0)
    if signal.get("setup_match"):
        score += 8.0
    if float(signal.get("score") or 0) >= 80:
        score += 6.0
    elif float(signal.get("score") or 0) >= 64:
        score += 3.0
    score += min(4.0, float(signal.get("setup_quality") or 50.0) / 25.0)
    score += min(2.0, float(entry.get("prelim_score") or 0) / 50.0)
    return round(score, 2)


def _skill_gate_reason(signal: dict[str, Any], gate_score: float, min_score: float) -> str:
    reasons = [f"规则门分 {round(gate_score, 2)} >= {round(min_score, 2)}"]
    if signal.get("setup_match"):
        reasons.append(f"买点: {signal.get('setup_match')}")
    signal_score = float(signal.get("score") or 0)
    if signal_score >= 80:
        reasons.append(f"高分信号: {signal_score}")
    elif signal_score >= 64:
        reasons.append(f"中等信号: {signal_score}")
    return "；".join(reasons)


def _finalize_skill_gate_audit(
    audit: dict[str, Any],
    skill_candidates: list[dict[str, Any]],
    explicit_review_limit_skipped: int = 0,
) -> dict[str, Any]:
    finalized = dict(audit or {})
    finalized["skill_rule_candidate_count"] = len(skill_candidates)
    finalized["skill_gate_candidate_count"] = len(skill_candidates)
    finalized["skill_agent_call_count"] = sum(len(entry.get("review_skill_ids") or []) for entry in skill_candidates)
    finalized["explicit_review_limit_skipped"] = explicit_review_limit_skipped
    per_skill = finalized.get("per_skill") or {}
    for stats in per_skill.values():
        stats["llm_reviewed"] = 0
    for entry in skill_candidates:
        for skill_id in entry.get("review_skill_ids") or []:
            if skill_id in per_skill:
                per_skill[skill_id]["llm_reviewed"] += 1
    selected_codes = {str((entry.get("stock") or {}).get("code") or "") for entry in skill_candidates}
    rows = list(finalized.get("rows") or [])
    if rows:
        reason_counts: Counter[str] = Counter()
        for row in rows:
            code = str(row.get("code") or "")
            review_selected = code in selected_codes
            row["review_selected"] = review_selected
            if row.get("passed") and not review_selected:
                row["review_limit_reason"] = "规则门通过，但被本次 Agent 深审上限截断"
                reason_counts["review_limit"] += 1
            elif not row.get("passed"):
                reason_counts[str(row.get("reason_key") or "filtered")] += 1
        finalized["rows"] = rows
        finalized["reason_counts"] = dict(reason_counts)
    return finalized


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
            "bucket": "",
            "status": rec.status,
        }
        for rec in history
    ]


def _merge_run_params(db: Session, preset: Preset | None, payload: dict[str, Any]) -> dict[str, Any]:
    preset_data = _serialize_preset(db, preset) if preset else {}
    skills = payload.get("skills") or preset_data.get("skills") or []
    run_mode = str(payload.get("run_mode") or preset_data.get("run_mode") or "formal")
    default_data_source = _default_data_source_for_run_mode(run_mode)
    if payload.get("single_skill_id"):
        single_weight = float(payload.get("single_skill_weight") or 1.0)
        skills = [{"skill_id": str(payload["single_skill_id"]), "weight": single_weight, "sort_order": 0}]
        run_mode = str(payload.get("run_mode") or "experiment")
        default_data_source = _default_data_source_for_run_mode(run_mode)
    skip_aux_refresh = bool(
        payload.get("skip_aux_refresh")
        if "skip_aux_refresh" in payload
        else run_mode == "experiment"
    )
    if run_mode == "formal":
        skip_aux_refresh = False
    params = {
        "preset_id": preset_data.get("preset_id"),
        "run_name": str(payload.get("run_name") or preset_data.get("name") or "临时运行"),
        "run_mode": run_mode,
        "market_scope": str(payload.get("market_scope") or preset_data.get("market_scope") or "all"),
        "market_scope_value": payload.get("market_scope_value", preset_data.get("market_scope_value")),
        "risk_template": str(payload.get("risk_template") or preset_data.get("risk_template") or "balanced"),
        "score_line": float(payload.get("score_line") or preset_data.get("score_line") or 72.0),
        "output_count": int(payload.get("output_count") or preset_data.get("output_count") or 6),
        "data_source": _normalize_data_source(payload.get("data_source") or preset_data.get("data_source") or default_data_source),
        "skip_aux_refresh": skip_aux_refresh,
        "disable_agent_review": bool(payload.get("disable_agent_review", False)),
        "agent_review_limit": int(payload.get("agent_review_limit") or 0),
        "agent_review_concurrency": int(payload.get("agent_review_concurrency") or 0),
        "hide_recent_repeats": bool(
            payload.get("hide_recent_repeats")
            if "hide_recent_repeats" in payload
            else preset_data.get("hide_recent_repeats", True)
        ),
        "skills": skills,
    }
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
    run_mode: str,
    requested_skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    allowed_modes = {"formal"} if run_mode == "formal" else {"formal", "experiment"}
    selected: list[dict[str, Any]] = []
    for item in requested_skills:
        skill_id = str(item.get("skill_id"))
        registry = db.get(SkillRegistry, skill_id)
        skill = skill_map.get(skill_id)
        if registry is None or skill is None or registry.mode == "disabled":
            continue
        if registry.mode not in allowed_modes:
            continue
        errors = validate_skill(skill)
        if errors:
            continue
        selected.append(
            {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "weight": float(item.get("weight") or 1.0),
                "mode": registry.mode,
                "version_id": _skill_version_id(skill),
                "skill": skill,
            }
        )
    return sorted(selected, key=lambda row: row["skill_id"])


def _normalize_data_source(value: Any) -> str:
    data_source = str(value or DATA_SOURCE_LOCAL_POOL)
    if data_source not in ALLOWED_DATA_SOURCES:
        raise ValueError("运行数据仅支持 local_pool、local_pool_preferred 或 realtime")
    return data_source


def _apply_market_scope(db: Session, features: list[dict[str, Any]], params: dict[str, Any]) -> list[dict[str, Any]]:
    scoped_codes = {item["code"] for item in _apply_market_scope_to_stocks(db, features, params)}
    return [item for item in features if item["code"] in scoped_codes]


def _has_fresh_concept_sector_cache(db: Session, trade_date: str, freshness_seconds: int) -> bool:
    cutoff = _freshness_cutoff(freshness_seconds)
    sector_count = db.scalar(
        select(func.count()).select_from(ConceptSector).where(
            ConceptSector.trade_date == trade_date,
            ConceptSector.created_at >= cutoff,
        )
    ) or 0
    return sector_count > 0


def _has_fresh_dragon_tiger_cache(db: Session, trade_date: str, freshness_seconds: int) -> bool:
    count = db.scalar(
        select(func.count()).select_from(DragonTiger).where(
            DragonTiger.trade_date == trade_date,
            DragonTiger.created_at >= _freshness_cutoff(freshness_seconds),
        )
    ) or 0
    return count > 0


def _stale_capital_flow_codes(
    db: Session,
    codes: list[str],
    trade_date: str,
    freshness_seconds: int,
) -> list[str]:
    if not codes:
        return []
    fresh_codes = {
        code
        for code, in db.execute(
            select(CapitalFlow.code).where(
                CapitalFlow.code.in_(codes),
                CapitalFlow.trade_date == trade_date,
                CapitalFlow.created_at >= _freshness_cutoff(freshness_seconds),
            ).distinct()
        ).all()
    }
    return [code for code in codes if code not in fresh_codes]


def _stale_news_codes(
    db: Session,
    codes: list[str],
    trade_date: str,
    freshness_seconds: int,
) -> list[str]:
    if not codes:
        return []
    latest_allowed = date.fromisoformat(trade_date)
    ttl_days = max(2, int((int(freshness_seconds or 0) + 86399) // 86400)) if freshness_seconds else 2
    earliest_allowed = (latest_allowed - timedelta(days=ttl_days)).isoformat()
    fresh_codes = {
        code
        for code, in db.execute(
            select(NewsAnnouncement.code).where(
                NewsAnnouncement.code.in_(codes),
                NewsAnnouncement.announce_date <= latest_allowed.isoformat(),
                NewsAnnouncement.announce_date >= earliest_allowed,
                NewsAnnouncement.created_at >= _freshness_cutoff(freshness_seconds),
            ).distinct()
        ).all()
    }
    return [code for code in codes if code not in fresh_codes]


def _freshness_cutoff(freshness_seconds: int) -> datetime:
    ttl = max(0, int(freshness_seconds or 0))
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=ttl)


def _recent_recommended_codes(db: Session, trade_date: str) -> set[str]:
    recent_date = (datetime.fromisoformat(trade_date) - timedelta(days=7)).date().isoformat()
    rows = db.scalars(
        select(Recommendation).where(
            Recommendation.trade_date >= recent_date,
            Recommendation.trade_date < trade_date,
        )
    ).all()
    return {row.code for row in rows}


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
                "bucket": "",
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


def _build_pipeline_audit(
    snapshot: dict[str, Any],
    params: dict[str, Any],
    total_contexts: int,
    prelim_count: int,
    skill_input_count: int,
    skill_review_limit: int,
    skill_review_concurrency: int,
    skill_demo_limited: bool,
    skill_agent_call_count: int = 0,
    skill_gate_audit: dict[str, Any] | None = None,
    prefilter_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    breadth = snapshot.get("breadth", {}) or {}
    config = breadth.get("candidate_limit_config") or {}
    gate = skill_gate_audit or {}
    prefilter = prefilter_audit or (breadth.get("prefilter_audit") or {})
    aux = breadth.get("auxiliary_data_audit") or {}
    score_line_context = resolve_score_line_context(
        float(params.get("score_line") or 72.0),
        market_snapshot=snapshot,
        risk_template=str(params.get("risk_template") or "balanced"),
    )
    display_limit = resolve_display_limit(int(params.get("output_count") or 0), snapshot)
    market_candidate_count = (
        int(config["market_candidate_count"])
        if "market_candidate_count" in config
        else int(get_settings().market_candidate_count)
    )
    lookahead_multiplier = (
        int(config["lookahead_multiplier"])
        if "lookahead_multiplier" in config
        else int(get_settings().market_candidate_lookahead_multiplier)
    )
    lookahead_count = (
        int(config["lookahead_count"])
        if "lookahead_count" in config
        else prelim_count
    )
    return {
        "unified_quote_total": int(breadth.get("unified_quote_total") or snapshot.get("sample_count") or total_contexts),
        "base_filter_eligible_count": int(breadth.get("base_filter_eligible_count") or total_contexts),
        "daily_kline_coverage_count": int(snapshot.get("sample_count") or total_contexts),
        "filtered_kline_coverage_count": total_contexts,
        "scoped_candidate_count": int(breadth.get("scoped_candidate_count") or total_contexts),
        "prefilter_candidate_count": int(prefilter.get("prefilter_candidate_count") or total_contexts),
        "prefilter": prefilter,
        "actual_scan_count": prelim_count,
        "skill_input_count": skill_input_count,
        "skill_agent_call_count": skill_agent_call_count,
        "skill_rule_candidate_count": int(gate.get("skill_rule_candidate_count") or skill_input_count),
        "skill_gate_candidate_count": int(gate.get("skill_gate_candidate_count") or skill_input_count),
        "skill_gate": gate,
        "skill_review_success_count": 0,
        "score_line": score_line_context,
        "output_count": int(params.get("output_count") or 0),
        "effective_output_count": display_limit,
        "market_candidate_count": market_candidate_count,
        "lookahead_multiplier": lookahead_multiplier,
        "lookahead_count": lookahead_count,
        "skill_review_limit": skill_review_limit,
        "skill_review_concurrency": skill_review_concurrency,
        "scan_limited": bool(config) and int(config.get("market_candidate_count") or 0) > 0,
        "skill_limited": skill_demo_limited,
        "demo_limited": skill_demo_limited or (bool(config) and int(config.get("market_candidate_count") or 0) > 0),
        "auxiliary_data": aux,
        "skip_reasons": {
            "kline": breadth.get("kline_skip_reasons") or {},
            "recent_repeat_filter_enabled": bool(params.get("hide_recent_repeats")),
            "prefilter": "先做公共可交易过滤，再按每个 Skill 的 market prefilter 缩小候选池",
            "pool_rules": "按 final_score 与动态 score_line 决定是否出票，展示上限不参与核心决策",
            "skill_rule_gate": "全市场先跑 strategy.yaml 规则；只有通过规则门的股票-Skill 组合进入真实 Skill Agent 深审",
            "rule_only_fallback": "已禁用 — 不再为凑数量补规则候补票",
        },
    }


def _strip_audit_rows(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_audit_rows(item)
            for key, item in value.items()
            if key != "rows"
        }
    if isinstance(value, list):
        return [_strip_audit_rows(item) for item in value]
    return value


def _build_filter_audit(
    *,
    prefilter_audit: dict[str, Any] | None,
    skill_gate_audit: dict[str, Any] | None,
    scored_cards: list[dict[str, Any]],
    recommended_cards: list[dict[str, Any]],
    pipeline: dict[str, Any],
) -> dict[str, Any]:
    rows_by_code: dict[str, dict[str, Any]] = {}

    for item in (prefilter_audit or {}).get("rows") or []:
        code = str(item.get("code") or "")
        if not code:
            continue
        passed = bool(item.get("passed"))
        common_pass = bool(item.get("common_pass", passed))
        row = {
            "code": code,
            "name": item.get("name"),
            "industry": item.get("industry"),
            "price": item.get("price"),
            "pct": item.get("pct"),
            "amount_yi": item.get("amount_yi"),
            "turnover_rate": item.get("turnover_rate"),
            "final_stage": "prefilter_pass" if passed else str(item.get("stage") or "prefilter"),
            "final_status": "active" if passed else "filtered",
            "final_reason": item.get("reason") or ("预过滤通过" if passed else "预过滤未通过"),
            "prefilter": item,
            "trace": [],
        }
        _append_trace_step(
            row,
            "prefilter_common",
            common_pass,
            item.get("common_reason") or ("公共可交易过滤通过" if common_pass else "公共可交易过滤未通过"),
            amount_yi=item.get("amount_yi"),
            turnover_rate=item.get("turnover_rate"),
            pct=item.get("pct"),
        )
        if common_pass:
            _append_trace_step(
                row,
                "prefilter_pass" if passed else "prefilter_skill",
                passed,
                item.get("reason") or ("技能预过滤命中" if passed else "未命中任何 Skill 市场预过滤"),
                skill_pass_ids=item.get("skill_pass_ids") or [],
                prefilter_source=item.get("prefilter_source"),
            )
        rows_by_code[code] = row

    for item in (skill_gate_audit or {}).get("rows") or []:
        code = str(item.get("code") or "")
        if not code:
            continue
        row = rows_by_code.setdefault(
            code,
            {
                "code": code,
                "name": item.get("name"),
                "industry": item.get("industry"),
                "final_stage": "rule_scan",
                "final_status": "active",
                "final_reason": "进入规则扫描",
            },
        )
        if not row.get("trace"):
            row["trace"] = []
            _append_trace_step(row, "prefilter_pass", True, "进入规则扫描")
        row["name"] = row.get("name") or item.get("name")
        row["industry"] = row.get("industry") or item.get("industry")
        row["rule_gate"] = item
        if not item.get("passed"):
            _append_trace_step(
                row,
                "skill_gate",
                False,
                item.get("reason") or "规则门未通过",
                best_skill_id=item.get("best_skill_id"),
                best_gate_score=item.get("best_gate_score"),
                prelim_score=item.get("prelim_score"),
            )
            row["final_stage"] = "skill_gate"
            row["final_status"] = "filtered"
            row["final_reason"] = item.get("reason") or "规则门未通过"
        elif not item.get("review_selected", True):
            _append_trace_step(
                row,
                "skill_gate",
                True,
                item.get("reason") or "规则门通过",
                best_skill_id=item.get("best_skill_id"),
                best_gate_score=item.get("best_gate_score"),
                prelim_score=item.get("prelim_score"),
            )
            _append_trace_step(
                row,
                "skill_review_limit",
                False,
                item.get("review_limit_reason") or "规则门通过，但未进入本次深审容量",
                review_skill_ids=item.get("review_skill_ids") or [],
            )
            row["final_stage"] = "skill_review_limit"
            row["final_status"] = "filtered"
            row["final_reason"] = item.get("review_limit_reason") or "规则门通过，但未进入本次深审容量"
        elif row.get("final_status") != "filtered":
            _append_trace_step(
                row,
                "skill_gate",
                True,
                item.get("reason") or "规则门通过，进入 Skill 深审",
                best_skill_id=item.get("best_skill_id"),
                best_gate_score=item.get("best_gate_score"),
                prelim_score=item.get("prelim_score"),
            )
            _append_trace_step(
                row,
                "skill_review",
                True,
                "进入 Skill 深审",
                review_skill_ids=item.get("review_skill_ids") or [],
            )
            row["final_stage"] = "skill_review"
            row["final_status"] = "active"
            row["final_reason"] = "进入 Skill 深审"

    recommended_codes = {str(card.get("code") or "") for card in recommended_cards}
    score_rows: list[dict[str, Any]] = []
    for card in scored_cards:
        code = str(card.get("code") or "")
        if not code:
            continue
        score_line_meta = card.get("score_line_meta") or {}
        execution_viability = card.get("execution_viability") or {}
        final_score = float(card.get("final_score") or 0)
        effective_line = float(score_line_meta.get("effective_score_line") or 0)
        passed = code in recommended_codes or bool(score_line_meta.get("passed"))
        score_gap = round(final_score - effective_line, 2) if effective_line else None
        if bool(score_line_meta.get("execution_blocked")):
            score_reason = str(score_line_meta.get("block_reason") or "入场不可成交，未纳入正式推荐")
        else:
            score_reason = "达到正式分数线" if passed else f"最终分 {round(final_score, 2)} 低于有效线 {round(effective_line, 2)}"
        score_row = {
            "code": code,
            "final_score": round(final_score, 2),
            "effective_score_line": round(effective_line, 2),
            "score_gap": score_gap,
            "passed": passed,
            "reason": score_reason,
            "evidence_quality_score": float(card.get("evidence_quality_score") or 0),
            "data_completeness_score": float(card.get("data_completeness_score") or 0),
            "score_breakdown": card.get("score_breakdown") or {},
            "score_line_meta": score_line_meta,
            "risk_tags": card.get("risk_flags") or card.get("risk_tags") or [],
            "info_tags": card.get("info_tags") or [],
            "setup_type": card.get("setup_type"),
            "data_authenticity": float(card.get("data_authenticity") or 0),
            "data_quality": card.get("data_quality") or {},
            "raw_component_scores": card.get("raw_component_scores") or (card.get("score_breakdown") or {}).get("raw_scores") or {},
            "weighted_component_scores": card.get("weighted_component_scores") or (card.get("score_breakdown") or {}).get("weighted_scores") or {},
            "aggregation_weights": card.get("aggregation_weights") or (card.get("score_breakdown") or {}).get("weights") or {},
            "component_scale": card.get("component_scale"),
            "consensus_boost": card.get("consensus_boost") or (card.get("score_breakdown") or {}).get("consensus_boost"),
            "consensus_tags": card.get("consensus_tags") or [],
            "quality_cap": (card.get("score_breakdown") or {}).get("quality_cap"),
            "market_regime": card.get("market_regime"),
            "market_discipline": card.get("market_discipline") or {},
            "execution_viability": execution_viability,
            "skill_disagreement": card.get("skill_disagreement") or {},
            "strategy_signal_count": len(card.get("strategy_signals") or []),
        }
        score_rows.append(score_row)
        row = rows_by_code.setdefault(
            code,
            {
                "code": code,
                "name": card.get("name"),
                "industry": card.get("industry"),
                "trace": [],
            },
        )
        if not row.get("trace"):
            _append_trace_step(row, "prefilter_pass", True, "进入聚合评分")
            _append_trace_step(row, "skill_review", True, "进入聚合评分")
        row["name"] = row.get("name") or card.get("name")
        row["industry"] = row.get("industry") or card.get("industry")
        row["score_line"] = score_row
        _append_trace_step(
            row,
            "score_line",
            passed,
            score_reason,
            final_score=round(final_score, 2),
            effective_score_line=round(effective_line, 2),
            score_gap=score_gap,
            evidence_adjustment=score_line_meta.get("evidence_adjustment"),
            execution_adjustment=score_line_meta.get("execution_adjustment"),
            execution_status=score_line_meta.get("execution_status"),
            adjustment_reasons=score_line_meta.get("reasons") or [],
        )
        if passed:
            row["final_stage"] = "recommended"
            row["final_status"] = "recommended"
            row["final_reason"] = score_reason
        else:
            row["final_stage"] = "score_line"
            row["final_status"] = "filtered"
            row["final_reason"] = score_reason

    rows = list(rows_by_code.values())
    for row in rows:
        row["trace_summary"] = _trace_summary(row.get("trace") or [])
    rows.sort(
        key=lambda row: (
            _filter_stage_order(str(row.get("final_stage") or "")),
            -float(((row.get("score_line") or {}).get("final_score") or 0)),
            str(row.get("code") or ""),
        )
    )

    reason_counts: dict[str, dict[str, int]] = {}
    for row in rows:
        stage = str(row.get("final_stage") or "unknown")
        reason = str(row.get("final_reason") or "-")
        reason_counts.setdefault(stage, {})
        reason_counts[stage][reason] = reason_counts[stage].get(reason, 0) + 1

    distributions = _build_filter_distributions(
        rows=rows,
        scored_cards=scored_cards,
        recommended_cards=recommended_cards,
        score_rows=score_rows,
    )

    stage_summary = _build_filter_stage_summary(
        pipeline=pipeline,
        prefilter_audit=prefilter_audit or {},
        skill_gate_audit=skill_gate_audit or {},
        scored_count=len(scored_cards),
        recommended_count=len(recommended_cards),
        reason_counts=reason_counts,
    )
    return {
        "stage_summary": stage_summary,
        "distributions": distributions,
        "reason_counts": reason_counts,
        "rows": rows,
    }


def _filter_stage_order(stage: str) -> int:
    order = {
        "prefilter_common": 10,
        "prefilter_skill": 20,
        "skill_gate": 30,
        "skill_review_limit": 40,
        "score_line": 50,
        "recommended": 60,
        "prefilter_pass": 70,
        "skill_review": 80,
    }
    return order.get(stage, 99)


def _append_trace_step(row: dict[str, Any], stage: str, passed: bool, reason: Any, **detail: Any) -> None:
    trace = row.setdefault("trace", [])
    step: dict[str, Any] = {
        "stage": stage,
        "label": _filter_stage_label(stage),
        "passed": bool(passed),
        "reason": str(reason or ("通过" if passed else "未通过")),
    }
    for key, value in detail.items():
        if value is None or value == "":
            continue
        step[key] = value
    trace.append(step)


def _filter_stage_label(stage: str) -> str:
    labels = {
        "prefilter_common": "公共预过滤",
        "prefilter_skill": "Skill 预过滤",
        "prefilter_pass": "预过滤通过",
        "skill_gate": "规则门",
        "skill_review_limit": "深审容量",
        "skill_review": "Skill 深审",
        "score_line": "分数线",
        "recommended": "正式推荐",
    }
    return labels.get(stage, stage)


def _trace_summary(trace: list[dict[str, Any]]) -> str:
    if not trace:
        return ""
    fragments = []
    for item in trace:
        label = item.get("label") or _filter_stage_label(str(item.get("stage") or ""))
        marker = "过" if item.get("passed") else "止"
        fragments.append(f"{label}:{marker}")
        if item.get("passed") is False:
            break
    return " -> ".join(fragments)


def _upgrade_filter_audit(audit: dict[str, Any]) -> dict[str, Any]:
    """补齐旧 Run 的逐票路径和完整分布，避免只能重跑后才能审计。"""
    if not isinstance(audit, dict):
        return {}
    rows = audit.get("rows") or []
    if not isinstance(rows, list):
        return audit
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get("trace"):
            row["trace"] = _trace_from_audit_row(row)
        row["trace_summary"] = row.get("trace_summary") or _trace_summary(row.get("trace") or [])

    distributions = audit.get("distributions") or {}
    if rows and not {"final_stage", "data_completeness_all", "score_line_adjustment_reason"}.issubset(set(distributions.keys())):
        scored_cards: list[dict[str, Any]] = []
        recommended_cards: list[dict[str, Any]] = []
        score_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("score_line"), dict):
                continue
            score_row = dict(row.get("score_line") or {})
            card = _score_card_from_audit_row(row, score_row)
            score_rows.append(score_row)
            scored_cards.append(card)
            if row.get("final_status") == "recommended" or score_row.get("passed"):
                recommended_cards.append(card)
        upgraded_distributions = _build_filter_distributions(
            rows=[row for row in rows if isinstance(row, dict)],
            scored_cards=scored_cards,
            recommended_cards=recommended_cards,
            score_rows=score_rows,
        )
        audit["distributions"] = {**distributions, **upgraded_distributions}
    return audit


def _trace_from_audit_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    traced: dict[str, Any] = {"trace": []}
    prefilter = row.get("prefilter") if isinstance(row.get("prefilter"), dict) else {}
    rule_gate = row.get("rule_gate") if isinstance(row.get("rule_gate"), dict) else {}
    score_line = row.get("score_line") if isinstance(row.get("score_line"), dict) else {}

    if prefilter:
        common_pass = bool(prefilter.get("common_pass", row.get("final_stage") != "prefilter_common"))
        _append_trace_step(
            traced,
            "prefilter_common",
            common_pass,
            prefilter.get("common_reason") or ("公共可交易过滤通过" if common_pass else row.get("final_reason")),
            amount_yi=prefilter.get("amount_yi"),
            turnover_rate=prefilter.get("turnover_rate"),
            pct=prefilter.get("pct"),
        )
        if common_pass:
            prefilter_pass = bool(prefilter.get("passed", row.get("final_stage") not in {"prefilter_skill", "prefilter_common"}))
            _append_trace_step(
                traced,
                "prefilter_pass" if prefilter_pass else "prefilter_skill",
                prefilter_pass,
                prefilter.get("reason") or ("技能预过滤命中" if prefilter_pass else row.get("final_reason")),
                skill_pass_ids=prefilter.get("skill_pass_ids") or [],
                prefilter_source=prefilter.get("prefilter_source"),
            )

    if rule_gate:
        gate_pass = bool(rule_gate.get("passed"))
        _append_trace_step(
            traced,
            "skill_gate",
            gate_pass,
            rule_gate.get("reason") or ("规则门通过" if gate_pass else row.get("final_reason")),
            best_skill_id=rule_gate.get("best_skill_id"),
            best_gate_score=rule_gate.get("best_gate_score"),
            prelim_score=rule_gate.get("prelim_score"),
        )
        if gate_pass and rule_gate.get("review_selected") is False:
            _append_trace_step(
                traced,
                "skill_review_limit",
                False,
                rule_gate.get("review_limit_reason") or row.get("final_reason") or "规则门通过，但未进入本次深审容量",
                review_skill_ids=rule_gate.get("review_skill_ids") or [],
            )
        elif gate_pass:
            _append_trace_step(
                traced,
                "skill_review",
                True,
                "进入 Skill 深审",
                review_skill_ids=rule_gate.get("review_skill_ids") or [],
            )

    if score_line:
        final_score = score_line.get("final_score")
        effective_line = score_line.get("effective_score_line")
        try:
            gap = round(float(final_score or 0) - float(effective_line or 0), 2) if effective_line is not None else score_line.get("score_gap")
        except (TypeError, ValueError):
            gap = score_line.get("score_gap")
        meta = score_line.get("score_line_meta") or {}
        _append_trace_step(
            traced,
            "score_line",
            bool(score_line.get("passed") or row.get("final_status") == "recommended"),
            score_line.get("reason") or row.get("final_reason") or "分数线判断",
            final_score=final_score,
            effective_score_line=effective_line,
            score_gap=gap,
            evidence_adjustment=meta.get("evidence_adjustment") if isinstance(meta, dict) else None,
            adjustment_reasons=(meta.get("reasons") if isinstance(meta, dict) else None) or [],
        )

    if not traced["trace"]:
        _append_trace_step(
            traced,
            str(row.get("final_stage") or "unknown"),
            row.get("final_status") != "filtered",
            row.get("final_reason") or "无路径明细",
        )
    return traced["trace"]


def _score_card_from_audit_row(row: dict[str, Any], score_row: dict[str, Any]) -> dict[str, Any]:
    breakdown = score_row.get("score_breakdown") if isinstance(score_row.get("score_breakdown"), dict) else {}
    raw_scores = score_row.get("raw_component_scores") or breakdown.get("raw_scores") or {}
    weighted_scores = score_row.get("weighted_component_scores") or breakdown.get("weighted_scores") or {}
    weights = score_row.get("aggregation_weights") or breakdown.get("weights") or {}
    return {
        **score_row,
        "code": row.get("code"),
        "name": row.get("name"),
        "industry": row.get("industry"),
        "final_score": score_row.get("final_score"),
        "evidence_quality_score": score_row.get("evidence_quality_score"),
        "data_completeness_score": score_row.get("data_completeness_score"),
        "data_authenticity": score_row.get("data_authenticity"),
        "data_quality": score_row.get("data_quality") or {},
        "score_breakdown": breakdown,
        "raw_component_scores": raw_scores,
        "weighted_component_scores": weighted_scores,
        "aggregation_weights": weights,
        "risk_flags": score_row.get("risk_tags") or [],
        "info_tags": score_row.get("info_tags") or [],
        "score_line_meta": score_row.get("score_line_meta") or {},
        "execution_viability": score_row.get("execution_viability") or {},
        "market_regime": score_row.get("market_regime"),
        "setup_type": score_row.get("setup_type"),
    }


def _build_filter_stage_summary(
    *,
    pipeline: dict[str, Any],
    prefilter_audit: dict[str, Any],
    skill_gate_audit: dict[str, Any],
    scored_count: int,
    recommended_count: int,
    reason_counts: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    unified = int(pipeline.get("unified_quote_total") or 0)
    base = int(pipeline.get("base_filter_eligible_count") or 0)
    scoped = int(pipeline.get("scoped_candidate_count") or prefilter_audit.get("input_count") or 0)
    prefilter_pass = int(pipeline.get("prefilter_candidate_count") or prefilter_audit.get("prefilter_candidate_count") or 0)
    actual_scan = int(pipeline.get("actual_scan_count") or prefilter_pass)
    skill_input = int(pipeline.get("skill_input_count") or 0)
    reviewed_success = int(pipeline.get("skill_review_success_count") or 0)
    return [
        {
            "stage": "market_universe",
            "label": "全市场 -> 基础合格",
            "input_count": unified,
            "pass_count": base,
            "filtered_count": max(0, unified - base),
            "reason_counts": pipeline.get("skip_reasons", {}).get("base_filter", {}),
        },
        {
            "stage": "scope_recall",
            "label": "基础合格 -> 范围候选",
            "input_count": base,
            "pass_count": scoped,
            "filtered_count": max(0, base - scoped),
            "reason_counts": {},
        },
        {
            "stage": "prefilter",
            "label": "范围候选 -> 预过滤",
            "input_count": int(prefilter_audit.get("input_count") or scoped),
            "pass_count": prefilter_pass,
            "filtered_count": max(0, int(prefilter_audit.get("input_count") or scoped) - prefilter_pass),
            "reason_counts": _merge_reason_counts(
                reason_counts.get("prefilter_common") or {},
                reason_counts.get("prefilter_skill") or {},
            ),
        },
        {
            "stage": "skill_gate",
            "label": "规则扫描 -> Skill 深审",
            "input_count": actual_scan,
            "pass_count": skill_input,
            "filtered_count": max(0, actual_scan - skill_input),
            "reason_counts": skill_gate_audit.get("reason_counts") or reason_counts.get("skill_gate") or {},
        },
        {
            "stage": "skill_review",
            "label": "Skill 深审 -> 可聚合",
            "input_count": skill_input,
            "pass_count": scored_count,
            "filtered_count": max(0, skill_input - scored_count),
            "reason_counts": {"agent_review_failed": max(0, skill_input - reviewed_success)} if skill_input > reviewed_success else {},
        },
        {
            "stage": "score_line",
            "label": "最终分 -> 正式出票",
            "input_count": scored_count,
            "pass_count": recommended_count,
            "filtered_count": max(0, scored_count - recommended_count),
            "reason_counts": reason_counts.get("score_line") or {},
        },
    ]


def _build_filter_distributions(
    *,
    rows: list[dict[str, Any]],
    scored_cards: list[dict[str, Any]],
    recommended_cards: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    recommended_codes = {str(card.get("code") or "") for card in recommended_cards}
    filtered_scored_cards = [card for card in scored_cards if str(card.get("code") or "") not in recommended_codes]
    rule_gate_rows = [row.get("rule_gate") or {} for row in rows if row.get("rule_gate")]

    distributions: dict[str, dict[str, int]] = {
        "final_stage": _categorical_distribution([row.get("final_stage") for row in rows]),
        "final_status": _categorical_distribution([row.get("final_status") for row in rows]),
        "industry": _categorical_distribution([row.get("industry") for row in rows], limit=12),
        "prefilter_stage": _categorical_distribution([(row.get("prefilter") or {}).get("stage") for row in rows if row.get("prefilter")]),
        "prefilter_source": _categorical_distribution([(row.get("prefilter") or {}).get("prefilter_source") for row in rows if row.get("prefilter")]),
        "skill_gate_result": _categorical_distribution(_skill_gate_result(row) for row in rule_gate_rows),
        "skill_gate_reason": _categorical_distribution(_skill_gate_reason_bucket(row) for row in rule_gate_rows),
        "score_line_result": _categorical_distribution(["通过" if row.get("passed") else "未通过" for row in score_rows]),
        "execution_status": _categorical_distribution(
            ((row.get("execution_viability") or {}).get("status") or "unknown") for row in score_rows
        ),
        "score_line_adjustment_reason": _list_distribution(
            (row.get("score_line_meta") or {}).get("reasons") or ["无附加证据惩罚"] for row in score_rows
        ),
        "setup_type": _categorical_distribution([row.get("setup_type") or "未分类" for row in score_rows]),
        "market_regime": _categorical_distribution([row.get("market_regime") for row in score_rows], limit=10),
        "prelim_score": _numeric_distribution(
            _numeric_values(rule_gate_rows, "prelim_score"),
            buckets=[0, 50, 60, 70, 80, 90],
        ),
        "best_gate_score": _numeric_distribution(
            _numeric_values(rule_gate_rows, "best_gate_score"),
            buckets=[0, 58, 70, 80, 90],
        ),
        "final_score_all": _score_distribution(scored_cards),
        "final_score_filtered": _score_distribution(filtered_scored_cards),
        "final_score_recommended": _score_distribution(recommended_cards),
        "effective_score_line": _numeric_distribution(
            _numeric_values(score_rows, "effective_score_line"),
            buckets=[0, 60, 70, 75, 80, 85, 90],
        ),
        "score_gap": _score_gap_distribution(score_rows),
        "evidence_quality_all": _quality_distribution(scored_cards),
        "data_completeness_all": _numeric_distribution(
            _numeric_values(score_rows, "data_completeness_score"),
            buckets=[0, 50, 65, 75, 90, 100],
        ),
        "data_authenticity_all": _numeric_distribution(
            _numeric_values(score_rows, "data_authenticity"),
            buckets=[0, 0.5, 0.75, 0.9, 1.0],
        ),
        "quality_cap": _numeric_distribution(
            _numeric_values(score_rows, "quality_cap"),
            buckets=[0, 60, 70, 80, 90, 100],
        ),
        "consensus_boost": _numeric_distribution(
            _numeric_values(score_rows, "consensus_boost"),
            buckets=[0, 0.8, 0.95, 1.0, 1.05, 1.15],
        ),
        "score_line_adjustment": _numeric_distribution(
            [float((row.get("score_line_meta") or {}).get("evidence_adjustment") or 0) for row in score_rows],
            buckets=[0, 2, 4, 6, 8, 10, 14],
        ),
        "execution_adjustment": _numeric_distribution(
            [float((row.get("score_line_meta") or {}).get("execution_adjustment") or 0) for row in score_rows],
            buckets=[0, 2, 4, 6, 8, 10, 14],
        ),
        "risk_tag_count": _count_distribution(len(row.get("risk_tags") or []) for row in score_rows),
        "info_tag_count": _count_distribution(len(row.get("info_tags") or []) for row in score_rows),
        "risk_tags": _list_distribution(row.get("risk_tags") or [] for row in score_rows),
        "info_tags": _list_distribution(row.get("info_tags") or [] for row in score_rows),
        "missing_fields": _list_distribution((row.get("data_quality") or {}).get("missing_fields") or [] for row in score_rows),
        "data_source_breakdown": _source_breakdown_distribution(score_rows),
        "pct": _numeric_distribution(_numeric_values(rows, "pct"), buckets=[-20, -10, -5, 0, 3, 7, 10, 20]),
        "amount_yi": _numeric_distribution(_numeric_values(rows, "amount_yi"), buckets=[0, 1.5, 3, 5, 10, 20, 50]),
        "turnover_rate": _numeric_distribution(_numeric_values(rows, "turnover_rate"), buckets=[0, 1, 3, 6, 10, 12, 20]),
    }

    for component in ("skill_agent", "strategy_rule", "market", "risk", "evidence"):
        distributions[f"raw_{component}"] = _component_distribution(
            score_rows,
            "raw_component_scores",
            component,
            buckets=[0, 20, 40, 60, 80, 100],
        )
        distributions[f"weighted_{component}"] = _component_distribution(
            score_rows,
            "weighted_component_scores",
            component,
            buckets=[0, 5, 10, 20, 35, 50],
        )
    return {key: value for key, value in distributions.items() if value}


def _skill_gate_result(row: dict[str, Any]) -> str:
    if not row.get("passed"):
        return "规则门未通过"
    if row.get("review_selected") is False:
        return "通过但容量截断"
    return "进入深审"


def _skill_gate_reason_bucket(row: dict[str, Any]) -> str:
    if row.get("review_selected") is False:
        return "review_limit"
    return str(row.get("reason_key") or ("gate_pass" if row.get("passed") else "filtered"))


def _categorical_distribution(values: Any, limit: int = 12) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for value in values:
        if value is None or value == "":
            continue
        counter[str(value)] += 1
    if not counter:
        return {}
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if limit and len(items) > limit:
        head = items[:limit]
        tail_count = sum(count for _, count in items[limit:])
        items = head + [("其他", tail_count)]
    return {key: int(count) for key, count in items if count > 0}


def _list_distribution(values: Any, limit: int = 12) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                if item is None or item == "":
                    continue
                counter[str(item)] += 1
        elif value is not None and value != "":
            counter[str(value)] += 1
    if not counter:
        return {}
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if limit and len(items) > limit:
        head = items[:limit]
        tail_count = sum(count for _, count in items[limit:])
        items = head + [("其他", tail_count)]
    return {key: int(count) for key, count in items if count > 0}


def _count_distribution(values: Any) -> dict[str, int]:
    counter: Counter[str] = Counter(str(int(value or 0)) for value in values)
    return {key: int(counter[key]) for key in sorted(counter, key=lambda item: int(item))}


def _numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _component_distribution(
    rows: list[dict[str, Any]],
    source_key: str,
    component: str,
    buckets: list[float],
) -> dict[str, int]:
    values: list[float] = []
    for row in rows:
        scores = row.get(source_key) or {}
        value = scores.get(component) if isinstance(scores, dict) else None
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return _numeric_distribution(values, buckets) if values else {}


def _source_breakdown_distribution(rows: list[dict[str, Any]], limit: int = 12) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        source_breakdown = (row.get("data_quality") or {}).get("source_breakdown") or {}
        if not isinstance(source_breakdown, dict):
            continue
        for source, count in source_breakdown.items():
            try:
                counter[str(source)] += int(float(count or 0))
            except (TypeError, ValueError):
                continue
    if not counter:
        return {}
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if limit and len(items) > limit:
        head = items[:limit]
        tail_count = sum(count for _, count in items[limit:])
        items = head + [("其他", tail_count)]
    return {key: int(count) for key, count in items if count > 0}


def _numeric_distribution(values: list[float], buckets: list[float]) -> dict[str, int]:
    if not buckets or not values:
        return {}
    ordered = sorted(float(item) for item in buckets)
    result: dict[str, int] = {}
    for index, lower in enumerate(ordered):
        if index + 1 < len(ordered):
            upper = ordered[index + 1]
            label = f"{lower:g}-{upper:g}"
            result[label] = sum(1 for value in values if lower <= value < upper)
        else:
            label = f"{lower:g}+"
            result[label] = sum(1 for value in values if value >= lower)
    return result


def _merge_reason_counts(*items: dict[str, int]) -> dict[str, int]:
    merged: Counter[str] = Counter()
    for item in items:
        merged.update({str(key): int(value or 0) for key, value in item.items()})
    return dict(merged)


def _score_gap_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {">=5": 0, "0-5": 0, "-5-0": 0, "-10--5": 0, "<-10": 0, "unknown": 0}
    for row in rows:
        gap = row.get("score_gap")
        if gap is None:
            buckets["unknown"] += 1
        elif float(gap) >= 5:
            buckets[">=5"] += 1
        elif float(gap) >= 0:
            buckets["0-5"] += 1
        elif float(gap) >= -5:
            buckets["-5-0"] += 1
        elif float(gap) >= -10:
            buckets["-10--5"] += 1
        else:
            buckets["<-10"] += 1
    return buckets


def _fallback_filter_audit(summary: dict[str, Any], pipeline: dict[str, Any], recommended_count: int) -> dict[str, Any]:
    stage_summary = _build_filter_stage_summary(
        pipeline=pipeline,
        prefilter_audit=pipeline.get("prefilter") or {},
        skill_gate_audit=pipeline.get("skill_gate") or {},
        scored_count=int(pipeline.get("skill_input_count") or 0),
        recommended_count=int(recommended_count or len(summary.get("top_codes") or [])),
        reason_counts={},
    )
    return {
        "stage_summary": stage_summary,
        "distributions": {
            "final_score_recommended": summary.get("score_distribution") or {},
            "evidence_quality_recommended": summary.get("quality_distribution") or {},
        },
        "reason_counts": {},
        "rows": [],
        "empty_reason": "该 Run 创建于候选级过滤审计上线前，请重新运行以查看逐票过滤路径。",
    }


def _score_distribution(cards: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {"80+": 0, "70-79": 0, "60-69": 0, "50-59": 0, "<50": 0}
    for card in cards:
        score = float(card.get("final_score") or 0)
        if score >= 80:
            buckets["80+"] += 1
        elif score >= 70:
            buckets["70-79"] += 1
        elif score >= 60:
            buckets["60-69"] += 1
        elif score >= 50:
            buckets["50-59"] += 1
        else:
            buckets["<50"] += 1
    return buckets


def _quality_distribution(cards: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {"80+": 0, "65-79": 0, "50-64": 0, "<50": 0}
    for card in cards:
        score = float(card.get("evidence_quality_score") or 0)
        if score >= 80:
            buckets["80+"] += 1
        elif score >= 65:
            buckets["65-79"] += 1
        elif score >= 50:
            buckets["50-64"] += 1
        else:
            buckets["<50"] += 1
    return buckets


def _coverage_heatmap(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    total = max(1, len(cards))
    for card in cards:
        for field in set((card.get("data_quality") or {}).get("missing_fields") or []):
            counts[field] = counts.get(field, 0) + 1
    return [
        {
            "field": field,
            "missing_count": count,
            "missing_rate": round(count / total, 3),
        }
        for field, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


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


def _replace_preset_skills(db: Session, preset_id: str, skills: list[dict[str, Any]]) -> None:
    existing = db.scalars(select(PresetSkill).where(PresetSkill.preset_id == preset_id)).all()
    for row in existing:
        db.delete(row)
    for index, item in enumerate(skills):
        db.add(
            PresetSkill(
                id=f"{preset_id}:{item['skill_id']}",
                preset_id=preset_id,
                skill_id=str(item["skill_id"]),
                weight=float(item.get("weight") or 1.0),
                sort_order=int(item.get("sort_order") or index),
            )
        )


def _write_install_manifest(target: Path, skill_id: str, source: str) -> None:
    manifest_path = target / "manifest.json"
    manifest = _loads(manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None, {})
    if not isinstance(manifest, dict):
        manifest = {}
    manifest.setdefault("name", skill_id)
    manifest["id"] = skill_id
    manifest["enabled"] = manifest.get("enabled", True)
    manifest["source_type"] = "github" if source.startswith(("http://", "https://")) else "local"
    manifest["source_url"] = source
    manifest_path.write_text(json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")


def _ensure_default_presets(db: Session, skills: list[StrategySkill]) -> None:
    has_changes = False
    for item in DEFAULT_PRESETS:
        preset = db.get(Preset, item["preset_id"])
        configured_data_source = _normalize_data_source(item.get("data_source") or _default_data_source_for_run_mode(item["run_mode"]))
        if preset is None:
            preset = Preset(
                preset_id=item["preset_id"],
                name=item["name"],
                description=item["description"],
                run_mode=item["run_mode"],
                market_scope=item["market_scope"],
                market_scope_value=item["market_scope_value"],
                risk_template=item["risk_template"],
                score_line=item["score_line"],
                output_count=item["output_count"],
                bucket_limits_json="{}",
                data_source=configured_data_source,
                hide_recent_repeats=item["hide_recent_repeats"],
                status="active",
            )
            db.add(preset)
            db.flush()
            desired_skills = _default_preset_skill_rows(item, skills)
            _replace_preset_skills(db, item["preset_id"], desired_skills)
            has_changes = True
            continue

        if not str(preset.data_source or "").strip():
            preset.data_source = configured_data_source
            has_changes = True
        if float(preset.score_line or 0) <= 0:
            preset.score_line = float(item["score_line"])
            has_changes = True
    if has_changes:
        db.commit()


def _default_preset_skill_rows(preset_config: dict[str, Any], skills: list[StrategySkill]) -> list[dict[str, Any]]:
    groups = {str(group) for group in preset_config.get("skill_groups", []) if str(group).strip()}
    if not groups:
        return []
    rows: list[dict[str, Any]] = []
    for skill in skills:
        meta = (skill.contract or {}).get("meta") or {}
        group_config = meta.get("default_preset_groups") or {}
        if not isinstance(group_config, dict):
            continue
        for group in groups:
            if group not in group_config:
                continue
            spec = group_config[group]
            if isinstance(spec, dict):
                weight = float(spec.get("weight") or 1.0)
                sort_order = int(spec.get("sort_order") or len(rows))
            elif isinstance(spec, (int, float)):
                weight = float(spec)
                sort_order = len(rows)
            elif spec is True:
                weight = float((skill.contract or {}).get("weight") or 1.0)
                sort_order = len(rows)
            else:
                continue
            rows.append({"skill_id": skill.skill_id, "weight": weight, "sort_order": sort_order})
            break
    return sorted(rows, key=lambda row: (int(row.get("sort_order") or 0), str(row.get("skill_id") or "")))


def _skill_performance(db: Session, skill_id: str, run_mode: str) -> dict[str, Any]:
    run_ids = [
        run.run_id for run in db.scalars(select(RecommendationRun).where(RecommendationRun.run_mode == run_mode)).all()
    ]
    if not run_ids:
        return {"sample_count": 0, "triggered_count": 0, "trigger_rate": 0.0, "hit_rate": 0.0, "avg_return": 0.0, "avg_score": 0.0, "failure_reasons": []}
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id.in_(run_ids))).all()
    matched: list[tuple[Recommendation, dict[str, Any]]] = []
    for rec in recs:
        for signal in _loads(rec.skill_breakdown_json, []):
            if signal.get("strategy_id") == skill_id:
                matched.append((rec, signal))
    if not matched:
        return {"sample_count": 0, "triggered_count": 0, "trigger_rate": 0.0, "hit_rate": 0.0, "avg_return": 0.0, "avg_score": 0.0, "failure_reasons": []}
    outcomes = {
        item.recommendation_id: item
        for item in db.scalars(
            select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_([rec.recommendation_id for rec, _ in matched]))
        ).all()
    }
    success_count = 0
    total_return = 0.0
    total_score = 0.0
    failures: dict[str, int] = {}
    triggered_count = 0
    for rec, signal in matched:
        total_score += float(signal.get("score", 0))
        outcome = outcomes.get(rec.recommendation_id)
        if outcome and is_trade_executed(outcome):
            triggered_count += 1
            total_return += resolved_trade_return(outcome)
            if is_accurate_recommendation(outcome):
                success_count += 1
            else:
                key = outcome.outcome_reason or "未达标"
                failures[key] = failures.get(key, 0) + 1
    sample_count = len(matched)
    return {
        "sample_count": sample_count,
        "triggered_count": triggered_count,
        "trigger_rate": round(triggered_count / sample_count, 3) if sample_count else 0.0,
        "hit_rate": round(success_count / triggered_count, 3) if triggered_count else 0.0,
        "avg_return": round(total_return / max(1, triggered_count), 2) if triggered_count else 0.0,
        "avg_score": round(total_score / sample_count, 2) if sample_count else 0.0,
        "failure_reasons": sorted(
            [{"reason": key, "count": count} for key, count in failures.items()],
            key=lambda item: item["count"],
            reverse=True,
        )[:5],
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
                "exchange": stock.get("exchange"),
                "industry": stock.get("industry"),
                "is_st": bool(stock.get("is_st", False)),
                "list_status": str(stock.get("list_status") or "listed"),
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
                exchange=stmt.inserted.exchange,
                industry=stmt.inserted.industry,
                is_st=stmt.inserted.is_st,
                list_status=stmt.inserted.list_status,
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


def _skill_version_id(skill: StrategySkill) -> str:
    contract_part = skill.contract_hash[:8] if skill.contract_hash else "none"
    skill_part = skill.skill_hash[:8] if skill.skill_hash else "noskill"
    return f"{skill.skill_id}:{skill.version}:{contract_part}:{skill_part}"


def _slugify(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()[:12]


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
    """Load calibrated DSA sub-score weights from CalibrationConfig table."""
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


class PathLike(str):
    def joinpath(self, *parts: str):
        from pathlib import Path

        return Path(self).joinpath(*parts)


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

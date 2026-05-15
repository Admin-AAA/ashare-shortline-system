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
from datetime import date, datetime, timedelta
from pathlib import Path
import asyncio
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.agents.review_runner import run_skill_reviews
from app.agents.session_registry import AgentSessionRegistry
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.engine.agent_skill_runner import agent_only_signal, enrich_signal_with_agent_review
from app.engine.aggregator import aggregate, apply_pool_rules, market_regime_weights
from app.engine.skill_loader import StrategySkill, load_skills, validate_skill
from app.engine.strategy_runner import run_strategy
from app.models.tables import (
    AgentDecisionLog,
    CalibrationConfig,
    CapitalFlow,
    ConceptSector,
    ConceptSectorStock,
    DailyBar,
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
)
from app.services.feature_builder import build_features, market_regime_score
from app.services.market_data import MarketDataError, get_market_universe
from app.services.market_pool import (
    get_market_snapshot_from_pool,
    get_market_universe_from_pool,
    refresh_capital_flows_for_codes,
    refresh_concept_sectors,
    refresh_dragon_tiger,
    refresh_news_for_codes,
)
from app.services.research_context import build_research_contexts


DEFAULT_BUCKET_LIMITS = {"容量核心": 3, "高弹观察": 2}
BOOTSTRAP_CACHE_TTL_SECONDS = 30.0
RUN_HEARTBEAT_STALE_SECONDS = 90
RUN_ACTIVE_STATUSES = {"queued", "running"}
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
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "output_count": 6,
        "bucket_limits": {"容量核心": 3, "高弹观察": 2},
        "hide_recent_repeats": True,
        "skills": [
            ("chen-xiaoqun-skill", 1.05),
            ("chao-gu-yang-jia-skill", 1.0),
            ("daily-stock-analysis-skill", 1.15),
        ],
    },
    {
        "preset_id": "preset_formal_trend_core",
        "name": "趋势龙头组合",
        "description": "适合主线趋势票，偏重龙头和大局观。",
        "run_mode": "formal",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "aggressive",
        "output_count": 5,
        "bucket_limits": {"容量核心": 3, "高弹观察": 2},
        "hide_recent_repeats": True,
        "skills": [
            ("chen-xiaoqun-skill", 1.15),
            ("chao-gu-yang-jia-skill", 1.05),
            ("daily-stock-analysis-skill", 0.95),
        ],
    },
    {
        "preset_id": "preset_experiment_dsa",
        "name": "DSA 单 Skill 实验",
        "description": "单独验证技术评分 Skill 的辨识度，不进入正式统计。",
        "run_mode": "experiment",
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "output_count": 6,
        "bucket_limits": {"容量核心": 3, "高弹观察": 3},
        "hide_recent_repeats": False,
        "skills": [
            ("daily-stock-analysis-skill", 1.0),
        ],
    },
]


_bootstrap_cache_lock = threading.Lock()
_bootstrap_cache: dict[tuple[str, str], dict[str, Any]] = {}
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
    now = datetime.utcnow()
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
        registry.updated_at = datetime.utcnow()
    return changed


def list_skills(db: Session) -> list[dict[str, Any]]:
    sync_skills(db)
    preset_rows = db.scalars(select(PresetSkill)).all()
    preset_map: dict[str, list[str]] = {}
    for row in preset_rows:
        preset = db.get(Preset, row.preset_id)
        if preset is None:
            continue
        preset_map.setdefault(row.skill_id, []).append(preset.name)
    rows = db.scalars(select(SkillRegistry).order_by(SkillRegistry.skill_id.asc())).all()
    return [
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
            "has_agent_contract": bool(_loads(row.manifest_json, {}).get("_skill_md_available")),
            "agent_runtime": _loads(row.manifest_json, {}).get("_skill_runtime", "unknown"),
            "reference_count": len(_loads(row.manifest_json, {}).get("_skill_reference_files", [])),
            "manifest": _loads(row.manifest_json, {}),
            "assigned_presets": preset_map.get(row.skill_id, []),
        }
        for row in rows
    ]


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
    return {
        "skill_id": row.skill_id,
        "name": row.name,
        "mode": row.mode,
        "enabled": row.enabled,
        "current_version": row.current_version,
        "has_executable_contract": row.has_executable_contract,
        "has_agent_contract": bool(skill_md.strip()),
        "agent_runtime": _loads(row.manifest_json, {}).get("_skill_runtime", "unknown"),
        "reference_files": _loads(row.manifest_json, {}).get("_skill_reference_files", []),
        "manifest": _loads(row.manifest_json, {}),
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
    row.updated_at = datetime.utcnow()
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
    return [_serialize_preset(db, preset) for preset in presets]


def create_preset(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(payload.get("preset_id") or _slugify(payload.get("name") or f"preset-{uuid.uuid4().hex[:6]}"))
    if db.get(Preset, preset_id) is not None:
        preset_id = f"{preset_id}-{uuid.uuid4().hex[:4]}"
    preset = Preset(
        preset_id=preset_id,
        name=str(payload.get("name") or "未命名预设"),
        description=payload.get("description"),
        run_mode=str(payload.get("run_mode") or "formal"),
        market_scope=str(payload.get("market_scope") or "all"),
        market_scope_value=payload.get("market_scope_value"),
        risk_template=str(payload.get("risk_template") or "balanced"),
        output_count=int(payload.get("output_count") or 6),
        bucket_limits_json=_dumps(payload.get("bucket_limits") or DEFAULT_BUCKET_LIMITS),
        data_source=_normalize_data_source(payload.get("data_source")),
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
        "data_source",
        "status",
    ]:
        if field in payload:
            value = _normalize_data_source(payload[field]) if field == "data_source" else payload[field]
            setattr(preset, field, value)
    if "output_count" in payload:
        preset.output_count = int(payload["output_count"])
    if "bucket_limits" in payload:
        preset.bucket_limits_json = _dumps(payload["bucket_limits"] or DEFAULT_BUCKET_LIMITS)
    if "hide_recent_repeats" in payload:
        preset.hide_recent_repeats = bool(payload["hide_recent_repeats"])
    preset.updated_at = datetime.utcnow()
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
    return [_serialize_run_summary(db, run) for run in runs]


def get_run_detail(db: Session, run_id: str) -> dict[str, Any] | None:
    run = db.get(RecommendationRun, run_id)
    if run is None:
        return None
    data = _serialize_run_summary(db, run)
    data["params"] = _loads(run.params_json, {})
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
    return [_serialize_recommendation(db, rec) for rec in recs]


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
        run.finished_at = datetime.utcnow()
        run.heartbeat_at = run.finished_at
        run.progress_phase = "failed"
        run.progress_message = str(exc)
        db.commit()
        invalidate_bootstrap_cache()
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
    now = datetime.utcnow()
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
        db.commit()
        invalidate_bootstrap_cache()
    else:
        db.flush()
    return run


def recover_interrupted_runs(db: Session) -> int:
    now = datetime.utcnow()
    runs = db.scalars(select(RecommendationRun).where(RecommendationRun.status.in_(tuple(RUN_ACTIVE_STATUSES)))).all()
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
        logger.warning("marked %d stale recommendation runs as interrupted on startup", recovered)
    return recovered


async def run_workbench(db: Session, payload: dict[str, Any], existing_run_id: str | None = None) -> dict[str, Any]:
    run_id = existing_run_id or _new_run_id()
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

    data_source = _normalize_data_source(params.get("data_source"))
    _update_run_progress(
        db,
        existing_run_id,
        phase="market_data",
        current=0,
        total=0,
        message="拉取并校验市场股票池",
        status="running",
    )
    if data_source == DATA_SOURCE_REALTIME:
        stocks, snapshot = get_market_universe()
    elif data_source == DATA_SOURCE_LOCAL_POOL_PREFERRED:
        stocks, snapshot = get_market_universe_from_pool(db, refresh=True)
    else:
        stocks, snapshot = get_market_universe_from_pool(db, refresh=False)
    snapshot.setdefault("breadth", {})["run_data_source_mode"] = data_source
    _update_run_progress(
        db,
        existing_run_id,
        phase="market_data",
        current=len(stocks),
        total=len(stocks),
        message=f"股票池已就绪，共 {len(stocks)} 只",
        status="running",
    )
    _persist_market(db, snapshot)
    _persist_stocks_and_bars(db, stocks)
    scoped_stocks = _apply_market_scope_to_stocks(db, stocks, params)
    if not scoped_stocks:
        raise ValueError("当前市场范围内没有可运行的股票，请调整预设范围后重试")
    snapshot.setdefault("breadth", {})["scoped_candidate_count"] = len(scoped_stocks)
    _update_run_progress(
        db,
        existing_run_id,
        phase="scope_filter",
        current=len(scoped_stocks),
        total=len(stocks),
        message=f"市场范围过滤完成，候选 {len(scoped_stocks)} 只",
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
    aux_prefetch_count = min(len(scoped_stocks), max(int(params.get("output_count") or 0) * 4, 12), 36)
    aux_prefetch_codes = [
        stock["code"]
        for stock in sorted(scoped_stocks, key=lambda item: float(item.get("free_mv_yi") or 0), reverse=True)[:aux_prefetch_count]
    ]
    _update_run_progress(
        db,
        existing_run_id,
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
                sectors = refresh_concept_sectors(db)
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
        existing_run_id,
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
                    ) or 0
                ),
                "elapsed_ms": 0,
            }
        else:
            t0 = time.perf_counter()
            try:
                dt_rows = refresh_dragon_tiger(db)
                aux_audit["dragon_tiger"] = {
                    "status": "success",
                    "count": len(dt_rows),
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
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
        existing_run_id,
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
                cf_result = refresh_capital_flows_for_codes(db, stale_capital_flow_codes)
                aux_audit["capital_flows"] = {
                    "status": "success",
                    "count": len(cf_result),
                    "requested_count": len(stale_capital_flow_codes),
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
                "elapsed_ms": 0,
            }
    _update_run_progress(
        db,
        existing_run_id,
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
                news_result = refresh_news_for_codes(db, stale_news_codes)
                aux_audit["news"] = {
                    "status": "success",
                    "count": len(news_result),
                    "requested_count": len(stale_news_codes),
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
                "elapsed_ms": 0,
            }
    _update_run_progress(
        db,
        existing_run_id,
        phase="auxiliary_data",
        current=4,
        total=4,
        message="辅助数据刷新完成",
        status="running",
    )
    snapshot.setdefault("breadth", {})["auxiliary_data_audit"] = aux_audit

    _update_run_progress(
        db,
        existing_run_id,
        phase="feature_build",
        current=0,
        total=len(scoped_stocks),
        message=f"构建 {len(scoped_stocks)} 只股票的特征与研究上下文",
        status="running",
    )
    features = build_features(scoped_stocks, snapshot, _load_calibrated_dsa_weights(db), db=db)
    research_contexts = build_research_contexts(features, snapshot)

    run = db.get(RecommendationRun, run_id) if existing_run_id else None
    if run is None:
        run = RecommendationRun(run_id=run_id)
        db.add(run)
    run.preset_id = params.get("preset_id")
    run.run_name = params["run_name"]
    run.run_mode = params["run_mode"]
    run.trade_date = snapshot["trade_date"]
    if not run.started_at:
        run.started_at = datetime.utcnow()
    run.finished_at = None
    run.universe_count = len(research_contexts)
    run.recommended_count = 0
    run.market_scope = params["market_scope"]
    run.market_scope_value = params.get("market_scope_value")
    run.risk_template = params["risk_template"]
    run.output_count = params["output_count"]
    run.selected_skills_json = _dumps(_selected_skill_rows(selected_skills))
    run.params_json = _dumps(params)
    run.market_snapshot_json = _dumps(snapshot)
    run.result_summary_json = _dumps({})
    run.status = "running"
    run.error_message = None
    run.heartbeat_at = datetime.utcnow()
    run.progress_phase = "feature_build"
    run.progress_current = len(research_contexts)
    run.progress_total = len(research_contexts)
    run.progress_message = f"特征构建完成，进入规则扫描：{len(research_contexts)} 只"
    run.interrupted_at = None
    db.flush()
    db.commit()  # Persist immediately so the run record survives failures
    invalidate_bootstrap_cache()

    agent_session_id = f"sess_{run_id[:12]}"
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
        skill_review_limit = int(params.get("agent_review_limit") or settings.market_skill_review_limit or 0)
        if skill_review_limit <= 0:
            default_multiple = 3 if params["run_mode"] == "experiment" else 8
            default_min = 8 if params["run_mode"] == "experiment" else 30
            skill_review_limit = min(max(int(params.get("output_count") or 0) * default_multiple, default_min), 60)
        skill_review_concurrency = max(1, int(settings.market_skill_review_concurrency or 1))
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
            rule_signals_by_skill: dict[str, Any] = {}
            for selected in selected_skills:
                skill_obj = selected["skill"]
                signal = run_strategy(skill_obj, stock, snapshot["main_industries"]) if skill_obj.executable else None
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
        skill_agent_call_count = sum(len(entry.get("review_skill_ids") or []) for entry in skill_candidates)

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
        )
        snapshot.setdefault("breadth", {})["recommendation_pipeline"] = pipeline_audit
        _update_run_progress(
            db,
            run_id,
            phase="skill_gate",
            current=len(skill_candidates),
            total=len(prelim_entries),
            message=f"规则门完成，进入 Skill 深审 {len(skill_candidates)} 只",
            status="running",
        )

        # ═══ 阶段二：Agent / Skill 深度审查（显式扫描集） ═══
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
            current=0,
            total=review_total,
            message=f"Skill 深审启动：{review_total} 只，最多并发 {skill_review_concurrency}",
            status="running",
        )
        if skill_candidates:
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
        def _build_card(entry, skill_reviews):
            context = entry["context"]
            stock = entry["stock"]
            rule_signals_by_skill = entry["rule_signals"]
            review_skill_ids = set(entry.get("review_skill_ids") or [])
            signals = []
            for review_result in skill_reviews:
                skill_id = review_result["skill_id"]
                review_data = review_result.get("review")
                hc = review_result.get("hallucination_check")
                rule_signal = rule_signals_by_skill.get(skill_id)

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
                    signal = agent_only_signal(skill_map[skill_id], stock, agent_review)
                else:
                    signal = enrich_signal_with_agent_review(rule_signal, agent_review)

                if hc:
                    signal["hallucination_flags"] = hc.get("flags", [])
                    signal["hallucination_severity"] = hc.get("severity", "low")

                for selected in selected_skills:
                    if selected["skill_id"] == skill_id:
                        signal["weight"] = selected["weight"]
                        signal["skill_mode"] = selected["mode"]
                        break
                signals.append(signal)

            for selected in selected_skills:
                skill_id = selected["skill_id"]
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
                    for r in skill_reviews if r.get("review")
                ],
            }

            card = aggregate(
                stock=stock,
                signals=signals,
                market_score=market_score,
                agent_review_score=review_log["agent_review_score"],
                risk_template=params["risk_template"],
                calibrated_weights=calibrated_weights if calibrated_weights else None,
                market_snapshot=snapshot,
            )
            card["feature_snapshot"] = context.to_feature_snapshot()
            card["feature_snapshot"]["component_scores"] = card.get("component_scores", {})
            card["feature_snapshot"]["data_authenticity"] = card.get("data_authenticity", 1.0)
            card["stop_loss"] = card["feature_snapshot"].get("stop_loss")
            card["take_profit"] = card["feature_snapshot"].get("take_profit")
            card["market_snapshot"] = snapshot
            card["agent_logs"] = review_log["logs"]
            card["recent_repeat"] = stock["code"] in recent_codes
            return card

        # 处理 Skill 输入集（有 Agent 审查）— 这些是主力推荐
        candidate_success_count = 0
        for entry in candidate_results:
            skill_reviews = entry.get("skill_reviews") or []
            card = _build_card(entry, skill_reviews)
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

        # 非 Skill 输入集仅作为补充（输出不足时填充），并在摘要中明确标注。
        if len(cards) < params["output_count"]:
            logger.info(
                "Skill-reviewed pool yielded %d cards (need %d), adding explicit rule-only fallbacks",
                len(cards), params["output_count"],
            )
            # 为剩余非候选股票构造卡片
            for entry in non_skill_entries:
                card = _build_card(entry, [])
                if card.get("final_score"):
                    discount = 0.85
                    card["final_score"] *= 0.85  # 无 Agent 审查重度降权
                    card["final_score"] = round(float(card["final_score"]), 2)
                    card["bucket"] = _bucket_for_final_score(card["final_score"], card.get("bucket"))
                    card["component_scores"] = {
                        key: round(float(value) * discount, 2)
                        for key, value in (card.get("component_scores") or {}).items()
                    }
                    card["component_scale"] = round(float(card.get("component_scale") or 1.0) * discount, 4)
                    discipline = card.get("market_discipline")
                    if isinstance(discipline, dict):
                        discipline["action"] = "规则补充观察"
                        discipline["max_position_pct"] = min(float(discipline.get("max_position_pct") or 0), 15.0)
                        discipline.setdefault("risk_flags", []).append("规则补充票，等待深审或盘中确认")
                        card["risk_flags"] = list(dict.fromkeys((card.get("risk_flags") or []) + discipline["risk_flags"]))
                    if card.get("feature_snapshot"):
                        card["feature_snapshot"]["component_scores"] = card.get("component_scores", {})
                        card["feature_snapshot"]["market_discipline"] = card.get("market_discipline", {})
                        card["feature_snapshot"]["component_scale"] = card.get("component_scale", 1.0)
                card["agent_logs"] = []
                if params["hide_recent_repeats"] and card["recent_repeat"]:
                    continue
                cards.append(card)
                if len(cards) >= params["output_count"] * 2:
                    break

        cards = apply_pool_rules(cards, params["bucket_limits"], params["output_count"])
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
                bucket=card["bucket"],
                final_score=float(card["final_score"]),
                consensus_score=float(card.get("consensus_score") or 0),
                setup_type=card.get("setup_type"),
                entry_rule=card.get("entry_rule"),
                invalid_rule=card.get("invalid_rule"),
                rationale=card.get("rationale"),
                risk_tags_json=_dumps(card.get("risk_flags", [])),
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
        run.finished_at = datetime.utcnow()
        run.status = "completed"
        run.heartbeat_at = run.finished_at
        run.progress_phase = "completed"
        run.progress_current = len(saved)
        run.progress_total = len(saved)
        run.progress_message = "运行完成"
        run.result_summary_json = _dumps(_build_run_summary(cards, saved, snapshot, pipeline_audit))
        db.commit()
        invalidate_bootstrap_cache()
        return {
            "run": _serialize_run_summary(db, run),
            "recommendations": [_serialize_recommendation(db, rec) for rec in saved],
        }
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = datetime.utcnow()
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
    return data


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
        item.updated_at = datetime.utcnow()
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
        note.updated_at = datetime.utcnow()
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
    outcome.outcome_label = str(payload.get("outcome_label") or "pending")
    outcome.outcome_reason = payload.get("outcome_reason")
    outcome.horizon_days = int(payload.get("horizon_days") or outcome.horizon_days or 5)
    outcome.label_source = "manual"
    outcome.labeled_at = datetime.utcnow()
    rec.status = outcome.outcome_label
    db.commit()
    invalidate_bootstrap_cache()
    return _serialize_outcome(outcome)


def label_pending_outcomes(db: Session, horizon_days: int = 5, run_mode: str | None = None) -> dict[str, Any]:
    stmt = select(Recommendation).where(Recommendation.status == "pending")
    if run_mode:
        run_ids = [
            run.run_id
            for run in db.scalars(select(RecommendationRun).where(RecommendationRun.run_mode == run_mode)).all()
        ]
        if not run_ids:
            return {"labeled": 0}
        stmt = stmt.where(Recommendation.run_id.in_(run_ids))
    recs = db.scalars(stmt).all()
    labeled = 0
    for rec in recs:
        bars = db.scalars(
            select(DailyBar)
            .where(DailyBar.code == rec.code, DailyBar.trade_date >= rec.trade_date)
            .order_by(DailyBar.trade_date.asc())
            .limit(horizon_days + 1)
        ).all()
        if len(bars) < 2:
            continue
        entry_price = rec.recommend_price or bars[0].close
        returns = [((bar.high - entry_price) / entry_price) * 100 for bar in bars[1:]]
        close_return = ((bars[-1].close - entry_price) / entry_price) * 100
        low_returns = [((bar.low - entry_price) / entry_price) * 100 for bar in bars[1:]]
        max_return = max(returns) if returns else 0
        min_return = min(low_returns) if low_returns else 0
        hit_target = max_return >= 5
        hit_invalid = min_return <= -4
        label = "success" if hit_target and not hit_invalid else "failed"
        outcome = db.get(RecommendationOutcome, rec.recommendation_id)
        if outcome is None:
            outcome = RecommendationOutcome(recommendation_id=rec.recommendation_id)
            db.add(outcome)
        outcome.horizon_days = horizon_days
        outcome.entry_triggered = True
        outcome.entry_date = bars[1].trade_date
        outcome.entry_price = entry_price
        outcome.max_return = round(max_return, 2)
        outcome.min_return = round(min_return, 2)
        outcome.close_return = round(close_return, 2)
        outcome.max_drawdown = round(min_return, 2)
        outcome.hit_target = hit_target
        outcome.hit_invalid = hit_invalid
        outcome.outcome_label = label
        outcome.outcome_reason = "T+5 达标" if label == "success" else "未达成功标准或回撤过大"
        outcome.label_source = "auto"
        outcome.labeled_at = datetime.utcnow()
        rec.status = label
        labeled += 1
    db.commit()
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
        "output_count": preset.output_count,
        "bucket_limits": _loads(preset.bucket_limits_json, DEFAULT_BUCKET_LIMITS),
        "data_source": preset.data_source,
        "hide_recent_repeats": preset.hide_recent_repeats,
        "status": preset.status,
        "skills": [
            {
                "skill_id": row.skill_id,
                "weight": row.weight,
                "sort_order": row.sort_order,
                "name": db.get(SkillRegistry, row.skill_id).name if db.get(SkillRegistry, row.skill_id) else row.skill_id,
            }
            for row in skills
        ],
    }


def _new_run_id() -> str:
    return f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _latest_trade_date(db: Session) -> str:
    latest = db.scalar(select(MarketSnapshot.trade_date).order_by(MarketSnapshot.created_at.desc()).limit(1))
    return latest or date.today().isoformat()


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


def _serialize_run_summary(db: Session, run: RecommendationRun) -> dict[str, Any]:
    preset = db.get(Preset, run.preset_id) if run.preset_id else None
    summary = _loads(run.result_summary_json, {})
    outcome_ids = [rec.recommendation_id for rec in db.scalars(select(Recommendation).where(Recommendation.run_id == run.run_id)).all()]
    outcomes = (
        db.scalars(select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_(outcome_ids))).all()
        if outcome_ids
        else []
    )
    success_count = sum(1 for outcome in outcomes if outcome.outcome_label == "success")
    hit_rate = round(success_count / len(outcomes), 3) if outcomes else 0.0
    heartbeat_age_seconds = None
    if run.heartbeat_at:
        heartbeat_age_seconds = max(0, int((datetime.utcnow() - run.heartbeat_at).total_seconds()))
    progress_total = int(run.progress_total or 0)
    progress_current = int(run.progress_current or 0)
    progress_pct = round(min(1.0, progress_current / progress_total), 4) if progress_total > 0 else None
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
        "output_count": run.output_count,
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
        "bucket": rec.bucket,
        "final_score": rec.final_score,
        "consensus_score": rec.consensus_score,
        "setup_type": rec.setup_type,
        "entry_rule": rec.entry_rule,
        "invalid_rule": rec.invalid_rule,
        "rationale": rec.rationale,
        "risk_tags": _loads(rec.risk_tags_json, []),
        "feature_snapshot": feature_snapshot,
        "market_context": market_context,
        "skill_breakdown": _loads(rec.skill_breakdown_json, []),
        "agent_logs": (_loads(rec.agent_summary_json, {}) or {}).get("logs", []),
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
    }
    return {
        "skill_candidates": skill_candidates,
        "non_skill_entries": non_skill_entries,
        "audit": audit,
    }


def _skill_gate_score(signal: dict[str, Any], entry: dict[str, Any]) -> float:
    score = float(signal.get("score") or 0)
    if signal.get("setup_match"):
        score += 8.0
    if signal.get("bucket_preference") == "容量核心":
        score += 6.0
    elif signal.get("bucket_preference") == "高弹观察":
        score += 3.0
    score += min(4.0, float(signal.get("setup_quality") or 50.0) / 25.0)
    score += min(2.0, float(entry.get("prelim_score") or 0) / 50.0)
    return round(score, 2)


def _skill_gate_reason(signal: dict[str, Any], gate_score: float, min_score: float) -> str:
    reasons = [f"规则门分 {round(gate_score, 2)} >= {round(min_score, 2)}"]
    if signal.get("setup_match"):
        reasons.append(f"买点: {signal.get('setup_match')}")
    bucket = signal.get("bucket_preference")
    if bucket in {"容量核心", "高弹观察"}:
        reasons.append(f"分层: {bucket}")
    return "；".join(reasons)


def _bucket_for_final_score(score: float, current_bucket: str | None = None) -> str:
    if score >= 80:
        computed = "容量核心"
    elif score >= 64:
        computed = "高弹观察"
    elif score >= 50:
        computed = "关注"
    elif score <= 28:
        computed = "明确回避"
    else:
        computed = "证据不足"
    if current_bucket == "证据不足" and computed in {"容量核心", "高弹观察", "关注"}:
        return "证据不足"
    return computed


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
    return finalized


def _serialize_outcome(outcome: RecommendationOutcome | None) -> dict[str, Any] | None:
    if outcome is None:
        return None
    return {
        "horizon_days": outcome.horizon_days,
        "entry_triggered": outcome.entry_triggered,
        "entry_date": outcome.entry_date,
        "entry_price": outcome.entry_price,
        "max_return": outcome.max_return,
        "min_return": outcome.min_return,
        "close_return": outcome.close_return,
        "max_drawdown": outcome.max_drawdown,
        "hit_target": outcome.hit_target,
        "hit_invalid": outcome.hit_invalid,
        "outcome_label": outcome.outcome_label,
        "outcome_reason": outcome.outcome_reason,
        "label_source": outcome.label_source,
        "labeled_at": outcome.labeled_at.isoformat() if outcome.labeled_at else None,
    }


def _build_timeline(db: Session, rec: Recommendation) -> list[dict[str, Any]]:
    windows = [1, 3, 5, 10]
    bars = db.scalars(
        select(DailyBar).where(DailyBar.code == rec.code, DailyBar.trade_date >= rec.trade_date).order_by(DailyBar.trade_date.asc()).limit(12)
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
            "bucket": rec.bucket,
            "status": rec.status,
        }
        for rec in history
    ]


def _merge_run_params(db: Session, preset: Preset | None, payload: dict[str, Any]) -> dict[str, Any]:
    preset_data = _serialize_preset(db, preset) if preset else {}
    skills = payload.get("skills") or preset_data.get("skills") or []
    run_mode = str(payload.get("run_mode") or preset_data.get("run_mode") or "formal")
    if payload.get("single_skill_id"):
        single_weight = float(payload.get("single_skill_weight") or 1.0)
        skills = [{"skill_id": str(payload["single_skill_id"]), "weight": single_weight, "sort_order": 0}]
        run_mode = str(payload.get("run_mode") or "experiment")
    params = {
        "preset_id": preset_data.get("preset_id"),
        "run_name": str(payload.get("run_name") or preset_data.get("name") or "临时运行"),
        "run_mode": run_mode,
        "market_scope": str(payload.get("market_scope") or preset_data.get("market_scope") or "all"),
        "market_scope_value": payload.get("market_scope_value", preset_data.get("market_scope_value")),
        "risk_template": str(payload.get("risk_template") or preset_data.get("risk_template") or "balanced"),
        "output_count": int(payload.get("output_count") or preset_data.get("output_count") or 6),
        "bucket_limits": payload.get("bucket_limits") or preset_data.get("bucket_limits") or DEFAULT_BUCKET_LIMITS,
        "data_source": _normalize_data_source(payload.get("data_source") or preset_data.get("data_source")),
        "skip_aux_refresh": bool(
            payload.get("skip_aux_refresh")
            if "skip_aux_refresh" in payload
            else run_mode == "experiment"
        ),
        "agent_review_limit": int(payload.get("agent_review_limit") or 0),
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
    sector_count = db.scalar(
        select(func.count()).select_from(ConceptSector).where(
            ConceptSector.trade_date == trade_date,
        )
    ) or 0
    return sector_count > 0


def _has_fresh_dragon_tiger_cache(db: Session, trade_date: str, freshness_seconds: int) -> bool:
    count = db.scalar(
        select(func.count()).select_from(DragonTiger).where(
            DragonTiger.trade_date == trade_date,
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
    fresh_codes = {
        code
        for code, in db.execute(
            select(NewsAnnouncement.code).where(
                NewsAnnouncement.code.in_(codes),
                NewsAnnouncement.announce_date == trade_date,
            ).distinct()
        ).all()
    }
    return [code for code in codes if code not in fresh_codes]


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
                "bucket": rec.bucket,
                "final_score": rec.final_score,
                "setup_type": rec.setup_type,
                "outcome_label": outcome.outcome_label if outcome else rec.status,
                "close_return": outcome.close_return if outcome else None,
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
    labeled = [outcome for outcome in outcomes if outcome and outcome.outcome_label != "pending"]
    success = [outcome for outcome in labeled if outcome and outcome.outcome_label == "success"]
    return {
        "same_stock_history": same_stock_history,
        "skill_recent_performance": {
            "sample_count": len(matched_recs),
            "labeled_count": len(labeled),
            "success_count": len(success),
            "hit_rate": round(len(success) / len(labeled), 3) if labeled else 0.0,
            "avg_close_return": round(sum(outcome.close_return for outcome in labeled) / len(labeled), 2) if labeled else 0.0,
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
) -> dict[str, Any]:
    breadth = snapshot.get("breadth", {}) or {}
    config = breadth.get("candidate_limit_config") or {}
    gate = skill_gate_audit or {}
    aux = breadth.get("auxiliary_data_audit") or {}
    return {
        "unified_quote_total": int(breadth.get("unified_quote_total") or snapshot.get("sample_count") or total_contexts),
        "base_filter_eligible_count": int(breadth.get("base_filter_eligible_count") or total_contexts),
        "daily_kline_coverage_count": int(snapshot.get("sample_count") or total_contexts),
        "filtered_kline_coverage_count": total_contexts,
        "actual_scan_count": prelim_count,
        "skill_input_count": skill_input_count,
        "skill_agent_call_count": skill_agent_call_count,
        "skill_rule_candidate_count": int(gate.get("skill_rule_candidate_count") or skill_input_count),
        "skill_gate_candidate_count": int(gate.get("skill_gate_candidate_count") or skill_input_count),
        "skill_gate": gate,
        "skill_review_success_count": 0,
        "output_count": int(params.get("output_count") or 0),
        "market_candidate_count": int(config.get("market_candidate_count") or get_settings().market_candidate_count),
        "lookahead_multiplier": int(config.get("lookahead_multiplier") or get_settings().market_candidate_lookahead_multiplier),
        "lookahead_count": int(config.get("lookahead_count") or prelim_count),
        "skill_review_limit": skill_review_limit,
        "skill_review_concurrency": skill_review_concurrency,
        "scan_limited": bool(config) and int(config.get("market_candidate_count") or 0) > 0,
        "skill_limited": skill_demo_limited,
        "demo_limited": skill_demo_limited or (bool(config) and int(config.get("market_candidate_count") or 0) > 0),
        "auxiliary_data": aux,
        "skip_reasons": {
            "kline": breadth.get("kline_skip_reasons") or {},
            "recent_repeat_filter_enabled": bool(params.get("hide_recent_repeats")),
            "pool_rules": "按 bucket_limits 与 output_count 截取最终展示，不压缩 Skill 输入范围",
            "skill_rule_gate": "全市场先跑 strategy.yaml 规则；只有通过规则门的股票-Skill 组合进入真实 Skill Agent 深审",
            "rule_only_fallback": "仅当 Skill 审查后卡片不足 output_count 时使用",
        },
    }


def _build_run_summary(
    cards: list[dict[str, Any]],
    saved: list[Recommendation],
    snapshot: dict[str, Any],
    pipeline_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    buckets: dict[str, int] = {}
    for card in cards:
        buckets[card["bucket"]] = buckets.get(card["bucket"], 0) + 1
    breadth = snapshot.get("breadth", {}) or {}
    pipeline = pipeline_audit or breadth.get("recommendation_pipeline") or {}
    return {
        "bucket_counts": buckets,
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
        "unified_quote_total": pipeline.get("unified_quote_total"),
        "base_filter_eligible_count": pipeline.get("base_filter_eligible_count"),
        "daily_kline_coverage_count": pipeline.get("daily_kline_coverage_count"),
        "filtered_kline_coverage_count": pipeline.get("filtered_kline_coverage_count"),
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
    skill_ids = {skill.skill_id for skill in skills}
    has_changes = False
    for item in DEFAULT_PRESETS:
        preset = db.get(Preset, item["preset_id"])
        if preset is not None:
            continue

        preset = Preset(
            preset_id=item["preset_id"],
            name=item["name"],
            description=item["description"],
            run_mode=item["run_mode"],
            market_scope=item["market_scope"],
            market_scope_value=item["market_scope_value"],
            risk_template=item["risk_template"],
            output_count=item["output_count"],
            bucket_limits_json=_dumps(item["bucket_limits"]),
            data_source=DATA_SOURCE_LOCAL_POOL,
            hide_recent_repeats=item["hide_recent_repeats"],
            status="active",
        )
        db.add(preset)
        db.flush()
        desired_skills = [
            {"skill_id": skill_id, "weight": weight, "sort_order": index}
            for index, (skill_id, weight) in enumerate(item["skills"])
            if skill_id in skill_ids
        ]
        _replace_preset_skills(db, item["preset_id"], desired_skills)
        has_changes = True
    if has_changes:
        db.commit()


def _skill_performance(db: Session, skill_id: str, run_mode: str) -> dict[str, Any]:
    run_ids = [
        run.run_id for run in db.scalars(select(RecommendationRun).where(RecommendationRun.run_mode == run_mode)).all()
    ]
    if not run_ids:
        return {"sample_count": 0, "hit_rate": 0.0, "avg_return": 0.0, "avg_score": 0.0, "failure_reasons": []}
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id.in_(run_ids))).all()
    matched: list[tuple[Recommendation, dict[str, Any]]] = []
    for rec in recs:
        for signal in _loads(rec.skill_breakdown_json, []):
            if signal.get("strategy_id") == skill_id:
                matched.append((rec, signal))
    if not matched:
        return {"sample_count": 0, "hit_rate": 0.0, "avg_return": 0.0, "avg_score": 0.0, "failure_reasons": []}
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
    for rec, signal in matched:
        total_score += float(signal.get("score", 0))
        outcome = outcomes.get(rec.recommendation_id)
        if outcome:
            total_return += outcome.close_return
            if outcome.outcome_label == "success":
                success_count += 1
            else:
                key = outcome.outcome_reason or "未达标"
                failures[key] = failures.get(key, 0) + 1
    sample_count = len(matched)
    return {
        "sample_count": sample_count,
        "hit_rate": round(success_count / sample_count, 3) if sample_count else 0.0,
        "avg_return": round(total_return / max(1, len(outcomes)), 2) if outcomes else 0.0,
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


def _persist_stocks_and_bars(db: Session, stocks: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for stock in stocks:
        code = stock["code"]
        if code in seen:
            continue
        seen.add(code)
        existing = db.get(Stock, code)
        if existing is None:
            db.add(
                Stock(
                    code=code,
                    name=stock["name"],
                    exchange=stock.get("exchange"),
                    industry=stock.get("industry"),
                    is_st=bool(stock.get("is_st", False)),
                )
            )
        else:
            existing.name = stock["name"]
            existing.exchange = stock.get("exchange")
            existing.industry = stock.get("industry")
            existing.is_st = bool(stock.get("is_st", False))
            existing.updated_at = datetime.utcnow()
        for bar in stock.get("_bars", []):
            exists = db.scalar(select(DailyBar).where(DailyBar.code == bar["code"], DailyBar.trade_date == bar["trade_date"]))
            if exists is None:
                db.add(DailyBar(**bar))
            else:
                exists.open = float(bar["open"])
                exists.high = float(bar["high"])
                exists.low = float(bar["low"])
                exists.close = float(bar["close"])
                exists.volume = float(bar.get("volume") or 0)
                exists.amount = float(bar.get("amount") or 0)
                exists.pct_chg = float(bar.get("pct_chg") or 0)
                exists.turnover_rate = float(bar.get("turnover_rate") or 0)


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

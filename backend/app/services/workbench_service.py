import hashlib
import json
import shutil
import subprocess
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
import asyncio
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.review_runner import run_skill_reviews
from app.agents.session_registry import AgentSessionRegistry
from app.core.config import get_settings
from app.engine.agent_skill_runner import agent_only_signal, enrich_signal_with_agent_review
from app.engine.aggregator import aggregate, apply_pool_rules
from app.engine.skill_loader import StrategySkill, load_skills, validate_skill
from app.engine.strategy_runner import run_strategy
from app.models.tables import (
    AgentDecisionLog,
    CalibrationConfig,
    DailyBar,
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
)
from app.services.feature_builder import build_features, market_regime_score
from app.services.market_data import MarketDataError, get_market_universe
from app.services.market_pool import get_market_snapshot_from_pool, get_market_universe_from_pool
from app.services.research_context import build_research_contexts


DEFAULT_BUCKET_LIMITS = {"容量核心": 3, "高弹观察": 2}
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


def bootstrap_workbench_state(db: Session) -> dict[str, Any]:
    sync_skills(db)
    presets = list_presets(db)
    recent_runs = list_runs(db, limit=20)
    latest_formal = next((run for run in recent_runs if run["run_mode"] == "formal"), None)
    latest_experiment = next((run for run in recent_runs if run["run_mode"] == "experiment"), None)
    market_snapshot = get_market_snapshot_from_pool(db)
    skills = list_skills(db)
    return {
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
    return data


def list_run_recommendations(db: Session, run_id: str) -> list[dict[str, Any]]:
    recs = db.scalars(
        select(Recommendation).where(Recommendation.run_id == run_id).order_by(Recommendation.final_score.desc())
    ).all()
    return [_serialize_recommendation(db, rec) for rec in recs]


def run_workbench(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
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
    if data_source == DATA_SOURCE_REALTIME:
        stocks, snapshot = get_market_universe()
    elif data_source == DATA_SOURCE_LOCAL_POOL_PREFERRED:
        stocks, snapshot = get_market_universe_from_pool(db, refresh=True)
    else:
        stocks, snapshot = get_market_universe_from_pool(db, refresh=False)
    snapshot.setdefault("breadth", {})["run_data_source_mode"] = data_source
    _persist_market(db, snapshot)
    _persist_stocks_and_bars(db, stocks)
    features = _apply_market_scope(db, build_features(stocks, snapshot, _load_calibrated_dsa_weights(db)), params)
    research_contexts = build_research_contexts(features, snapshot)

    run_id = f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    run = RecommendationRun(
        run_id=run_id,
        preset_id=params.get("preset_id"),
        run_name=params["run_name"],
        run_mode=params["run_mode"],
        trade_date=snapshot["trade_date"],
        universe_count=len(research_contexts),
        market_scope=params["market_scope"],
        market_scope_value=params.get("market_scope_value"),
        risk_template=params["risk_template"],
        output_count=params["output_count"],
        selected_skills_json=_dumps(
            [
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
        ),
        params_json=_dumps(params),
        market_snapshot_json=_dumps(snapshot),
        status="running",
    )
    db.add(run)
    db.flush()
    db.commit()  # Persist immediately so the run record survives failures

    agent_session_id = f"sess_{run_id[:12]}"
    all_skill_objs = [s["skill"] for s in selected_skills]
    AgentSessionRegistry.register(
        session_id=agent_session_id,
        db=db,
        skills=all_skill_objs,
        snapshot=snapshot,
    )

    try:
        recent_codes = _recent_recommended_codes(db, snapshot["trade_date"])
        market_score = market_regime_score(snapshot)
        cards: list[dict[str, Any]] = []

        for context in research_contexts:
            stock = context.to_skill_input()

            # 收集规则信号
            rule_signals_by_skill = {}
            for selected in selected_skills:
                skill_obj = selected["skill"]
                signal = run_strategy(skill_obj, stock, snapshot["main_industries"]) if skill_obj.executable else None
                rule_signals_by_skill[skill_obj.skill_id] = signal

            # 并发 Agno Agent Skill 审查
            skill_reviews = asyncio.run(run_skill_reviews(
                stock=stock,
                skills=[s["skill"] for s in selected_skills],
                snapshot=snapshot,
                rule_signals_by_skill=rule_signals_by_skill,
                session_id=agent_session_id,
            ))

            # 合并 Agent 审查结果到各 Skill 信号
            signals = []
            for review_result in skill_reviews:
                skill_id = review_result["skill_id"]
                review_data = review_result.get("review")
                hc = review_result.get("hallucination_check")
                rule_signal = rule_signals_by_skill.get(skill_id)

                agent_review = {
                    "stance": review_data.get("stance", "neutral"),
                    "score_delta": review_data.get("score", 50) - 50,
                    "confidence": review_data.get("confidence", 0.5),
                    "supports": review_data.get("evidence", []),
                    "risks": review_data.get("risks", []),
                    "summary": review_data.get("summary", ""),
                } if review_data else {
                    "stance": "neutral",
                    "score_delta": 0,
                    "confidence": 0.3,
                    "supports": [],
                    "risks": [],
                    "summary": "",
                }

                if rule_signal is None:
                    signal = agent_only_signal(skill_map[skill_id], stock, agent_review)
                else:
                    signal = enrich_signal_with_agent_review(rule_signal, agent_review)

                if hc:
                    signal["hallucination_flags"] = hc.get("flags", [])
                    signal["hallucination_severity"] = hc.get("severity", "low")

                # 设置 weight 和 skill_mode
                for selected in selected_skills:
                    if selected["skill_id"] == skill_id:
                        signal["weight"] = selected["weight"]
                        signal["skill_mode"] = selected["mode"]
                        break

                signals.append(signal)

            # 计算 agent_review_score
            valid_reviews = [r for r in skill_reviews if r.get("review") and not r.get("error")]
            if valid_reviews:
                scores = [r["review"]["score"] for r in valid_reviews]
                confidences = [r["review"]["confidence"] for r in valid_reviews]
                agent_review_score = sum(s * c for s, c in zip(scores, confidences)) / max(sum(confidences), 0.01)
            else:
                agent_review_score = 50.0

            review = {
                "agent_review_score": round(agent_review_score, 2),
                "logs": [
                    {
                        "agent_name": f"Skill Agent · {r.get('skill_name', 'Unknown')}",
                        "output": r.get("review") or {},
                    }
                    for r in skill_reviews
                    if r.get("review")
                ],
            }

            calibrated_weights = _load_calibrated_agg_weights(db)
            card = aggregate(
                stock=stock,
                signals=signals,
                market_score=market_score,
                agent_review_score=review["agent_review_score"],
                risk_template=params["risk_template"],
                calibrated_weights=calibrated_weights if calibrated_weights else None,
            )
            card["feature_snapshot"] = context.to_feature_snapshot()
            card["feature_snapshot"]["component_scores"] = card.get("component_scores", {})
            card["feature_snapshot"]["data_authenticity"] = card.get("data_authenticity", 1.0)
            card["market_snapshot"] = snapshot
            card["agent_logs"] = review["logs"]
            card["recent_repeat"] = stock["code"] in recent_codes
            if params["hide_recent_repeats"] and card["recent_repeat"]:
                continue
            cards.append(card)

        cards = apply_pool_rules(cards, params["bucket_limits"], params["output_count"])
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
                feature_snapshot_json=_dumps(card.get("feature_snapshot", {})),
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

        run.recommended_count = len(saved)
        run.finished_at = datetime.utcnow()
        run.status = "completed"
        run.result_summary_json = _dumps(_build_run_summary(cards, saved, snapshot))
        db.commit()
        return {
            "run": _serialize_run_summary(db, run),
            "recommendations": [_serialize_recommendation(db, rec) for rec in saved],
        }
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = datetime.utcnow()
        db.commit()
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
        "skill_count": len(_loads(run.selected_skills_json, [])),
        "hit_rate": hit_rate,
        "result_summary": summary,
    }


def _serialize_recommendation(db: Session, rec: Recommendation) -> dict[str, Any]:
    watch = db.scalar(select(WatchItem).where(WatchItem.source_recommendation_id == rec.recommendation_id))
    return {
        "recommendation_id": rec.recommendation_id,
        "run_id": rec.run_id,
        "trade_date": rec.trade_date,
        "recommended_at": rec.recommended_at.isoformat(),
        "code": rec.code,
        "name": rec.name,
        "industry": rec.industry,
        "recommend_price": rec.recommend_price,
        "bucket": rec.bucket,
        "final_score": rec.final_score,
        "consensus_score": rec.consensus_score,
        "setup_type": rec.setup_type,
        "entry_rule": rec.entry_rule,
        "invalid_rule": rec.invalid_rule,
        "rationale": rec.rationale,
        "risk_tags": _loads(rec.risk_tags_json, []),
        "feature_snapshot": _loads(rec.feature_snapshot_json, {}),
        "market_context": _loads(rec.market_context_json, {}),
        "skill_breakdown": _loads(rec.skill_breakdown_json, []),
        "status": rec.status,
        "recent_repeat": rec.recent_repeat,
        "watch_status": watch.watch_status if watch else "none",
        "watch_note": watch.note if watch else None,
    }


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
        "hide_recent_repeats": bool(
            payload.get("hide_recent_repeats")
            if "hide_recent_repeats" in payload
            else preset_data.get("hide_recent_repeats", True)
        ),
        "skills": skills,
    }
    return params


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
    scope = params["market_scope"]
    if scope == "industry" and params.get("market_scope_value"):
        return [item for item in features if item.get("industry") == params["market_scope_value"]]
    if scope == "watchlist":
        watched_codes = {
            row.code
            for row in db.scalars(select(WatchItem).where(WatchItem.watch_status == "watching")).all()
        }
        return [item for item in features if item["code"] in watched_codes]
    return features


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


def _build_run_summary(cards: list[dict[str, Any]], saved: list[Recommendation], snapshot: dict[str, Any]) -> dict[str, Any]:
    buckets: dict[str, int] = {}
    for card in cards:
        buckets[card["bucket"]] = buckets.get(card["bucket"], 0) + 1
    breadth = snapshot.get("breadth", {}) or {}
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
        if preset is None:
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
            has_changes = True
            preset_skills = [
                {"skill_id": skill_id, "weight": weight, "sort_order": index}
                for index, (skill_id, weight) in enumerate(item["skills"])
                if skill_id in skill_ids
            ]
            _replace_preset_skills(db, preset.preset_id, preset_skills)
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
    for stock in stocks:
        existing = db.get(Stock, stock["code"])
        if existing is None:
            db.add(
                Stock(
                    code=stock["code"],
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
    """Load calibrated aggregation weights from CalibrationConfig table."""
    rows = db.scalars(select(CalibrationConfig)).all()
    weights: dict[str, float] = {}
    prefix_map = {
        "agg_w_strategy": "strategy",
        "agg_w_market": "market",
        "agg_w_setup": "setup",
        "agg_w_risk": "risk",
        "agg_w_consensus": "consensus",
        "agg_w_agent": "agent",
        "agg_w_evidence": "evidence",
    }
    for row in rows:
        if row.key in prefix_map:
            weights[prefix_map[row.key]] = float(row.value)
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

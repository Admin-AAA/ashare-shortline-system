import json
import threading
import time
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    Preset,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    ReviewNote,
    SkillRegistry,
    WatchItem,
)

import logging
logger = logging.getLogger(__name__)

VALID_RUN_MODES = {"formal", "experiment"}

# 短时缓存：stats 数据变更频率低（仅在创建新 run 后变化），5 秒 TTL 足够
_stats_cache: dict[str, tuple[float, Any]] = {}
_stats_cache_lock = threading.Lock()
_STATS_CACHE_TTL = 5.0


def _cached(key: str, factory):
    now = time.time()
    with _stats_cache_lock:
        entry = _stats_cache.get(key)
        if entry is not None and (now - entry[0]) < _STATS_CACHE_TTL:
            return entry[1]
    result = factory()
    with _stats_cache_lock:
        _stats_cache[key] = (time.time(), result)
    return result


def get_overview_stats(db: Session, mode: str = "formal") -> dict[str, Any]:
    return _cached(f"overview:{mode}", lambda: _get_overview_stats(db, mode))


def _get_overview_stats(db: Session, mode: str) -> dict[str, Any]:
    runs = _runs_for_mode(db, mode)
    run_ids = [run.run_id for run in runs]
    if not run_ids:
        return _empty_overview(mode)

    rec_rows = db.execute(
        select(Recommendation.recommendation_id, Recommendation.run_id, Recommendation.bucket, Recommendation.final_score)
        .where(Recommendation.run_id.in_(run_ids))
    ).all()

    rec_ids = [row.recommendation_id for row in rec_rows]
    outcome_map: dict[str, Any] = {}
    if rec_ids:
        outcome_rows = db.execute(
            select(RecommendationOutcome.recommendation_id, RecommendationOutcome.outcome_label, RecommendationOutcome.close_return, RecommendationOutcome.max_return, RecommendationOutcome.max_drawdown)
            .where(RecommendationOutcome.recommendation_id.in_(rec_ids))
        ).all()
        outcome_map = {row.recommendation_id: row for row in outcome_rows}

    labeled = [row for row in outcome_rows if row.outcome_label != "pending"]
    success_count = sum(1 for row in labeled if row.outcome_label == "success")
    failed_count = sum(1 for row in labeled if row.outcome_label == "failed")
    reviewed_count = _reviewed_count(db, rec_ids)
    watched_count = _watched_count(db, run_ids)
    bucket_counts = Counter(row.bucket for row in rec_rows)

    return {
        "mode": mode,
        "run_count": len(runs),
        "recommendation_count": len(rec_rows),
        "labeled_count": len(labeled),
        "success_count": success_count,
        "failed_count": failed_count,
        "pending_count": max(0, len(rec_rows) - len(labeled)),
        "watch_count": watched_count,
        "reviewed_count": reviewed_count,
        "hit_rate": round(success_count / len(labeled), 3) if labeled else 0.0,
        "avg_return": _mean([row.close_return for row in labeled]),
        "avg_max_return": _mean([row.max_return for row in labeled]),
        "avg_drawdown": _mean([row.max_drawdown for row in labeled]),
        "avg_score": _mean([row.final_score for row in rec_rows]),
        "latest_run_at": runs[0].finished_at.isoformat() if runs and runs[0].finished_at else None,
        "top_buckets": [{"bucket": bucket, "count": count} for bucket, count in bucket_counts.most_common(5)],
    }


def get_skill_stats(db: Session, mode: str = "formal") -> list[dict[str, Any]]:
    return _cached(f"skill:{mode}", lambda: _get_skill_stats(db, mode))


def _get_skill_stats(db: Session, mode: str) -> list[dict[str, Any]]:
    t0 = time.perf_counter()
    runs = _runs_for_mode(db, mode)
    run_ids = [run.run_id for run in runs]
    if not run_ids:
        return []

    # 从 runs 获取 regime（避免从每条 rec 加载巨大的 market_context_json）
    run_regime_map: dict[str, str] = {}
    for run in runs:
        snapshot = _loads(run.market_snapshot_json, {})
        run_regime_map[run.run_id] = str((snapshot.get("breadth") or {}).get("regime") or "未知")

    t1 = time.perf_counter()
    rec_rows = db.execute(
        select(
            Recommendation.recommendation_id,
            Recommendation.run_id,
            Recommendation.industry,
            Recommendation.final_score,
            Recommendation.skill_breakdown_json,
            Recommendation.setup_type,
            Recommendation.recommended_at,
        ).where(Recommendation.run_id.in_(run_ids))
    ).all()
    t2 = time.perf_counter()

    rec_ids = [row.recommendation_id for row in rec_rows]
    outcome_map: dict[str, Any] = {}
    if rec_ids:
        outcome_rows = db.execute(
            select(RecommendationOutcome.recommendation_id, RecommendationOutcome.outcome_label, RecommendationOutcome.close_return)
            .where(RecommendationOutcome.recommendation_id.in_(rec_ids))
        ).all()
        outcome_map = {row.recommendation_id: row for row in outcome_rows}
    t3 = time.perf_counter()

    registry = {row.skill_id: row for row in db.scalars(select(SkillRegistry)).all()}
    t4 = time.perf_counter()

    buckets: dict[str, dict[str, Any]] = {}
    for rec in rec_rows:
        regime = run_regime_map.get(rec.run_id, "未知")
        outcome = outcome_map.get(rec.recommendation_id)
        has_outcome = outcome is not None and outcome.outcome_label != "pending"
        final_score = float(rec.final_score or 0)
        industry = str(rec.industry or "未分类")
        setup_type = str(rec.setup_type or "")
        last_seen = rec.recommended_at.isoformat() if isinstance(rec.recommended_at, datetime) else str(rec.recommended_at)
        for signal in _loads(rec.skill_breakdown_json, []):
            skill_id = str(signal.get("strategy_id") or "unknown")
            reg = registry.get(skill_id)
            row = buckets.setdefault(
                skill_id,
                {
                    "skill_id": skill_id,
                    "name": reg.name if reg else skill_id,
                    "current_mode": reg.mode if reg else "unknown",
                    "sample_count": 0,
                    "labeled_count": 0,
                    "success_count": 0,
                    "avg_signal_score": 0.0,
                    "avg_final_score": 0.0,
                    "avg_return": 0.0,
                    "runs": set(),
                    "last_seen_at": None,
                    "market_regimes": Counter(),
                    "industries": Counter(),
                    "setups": Counter(),
                },
            )
            row["sample_count"] += 1
            row["avg_signal_score"] += float(signal.get("score") or 0)
            row["avg_final_score"] += final_score
            row["runs"].add(rec.run_id)
            row["last_seen_at"] = last_seen
            row["market_regimes"][regime] += 1
            row["industries"][industry] += 1
            row["setups"][str(signal.get("setup_match") or setup_type or "未命中买点")] += 1

            if has_outcome:
                row["labeled_count"] += 1
                row["avg_return"] += float(outcome.close_return or 0)
                if outcome.outcome_label == "success":
                    row["success_count"] += 1

    t5 = time.perf_counter()
    result: list[dict[str, Any]] = []
    for row in buckets.values():
        sample_count = max(1, int(row["sample_count"]))
        labeled_count = int(row["labeled_count"])
        result.append(
            {
                "skill_id": row["skill_id"],
                "name": row["name"],
                "current_mode": row["current_mode"],
                "sample_count": row["sample_count"],
                "run_count": len(row["runs"]),
                "labeled_count": labeled_count,
                "success_count": row["success_count"],
                "hit_rate": round(row["success_count"] / labeled_count, 3) if labeled_count else 0.0,
                "avg_signal_score": round(row["avg_signal_score"] / sample_count, 2),
                "avg_final_score": round(row["avg_final_score"] / sample_count, 2),
                "avg_return": round(row["avg_return"] / labeled_count, 2) if labeled_count else 0.0,
                "last_seen_at": row["last_seen_at"],
                "top_market_regimes": _counter_rows(row["market_regimes"]),
                "top_industries": _counter_rows(row["industries"]),
                "top_setups": _counter_rows(row["setups"]),
            }
        )
    t6 = time.perf_counter()
    logger.info(
        "get_skill_stats timing: runs=%.0fms recs=%.0fms outcomes=%.0fms registry=%.0fms loop=%.0fms build=%.0fms total=%.0fms rec_count=%d",
        (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000, (t4 - t3) * 1000,
        (t5 - t4) * 1000, (t6 - t5) * 1000, (t6 - t0) * 1000, len(rec_rows),
    )
    return sorted(result, key=lambda item: (item["sample_count"], item["hit_rate"], item["avg_signal_score"]), reverse=True)


def get_preset_stats(db: Session, mode: str = "formal") -> list[dict[str, Any]]:
    return _cached(f"preset:{mode}", lambda: _get_preset_stats(db, mode))


def _get_preset_stats(db: Session, mode: str) -> list[dict[str, Any]]:
    runs = _runs_for_mode(db, mode)
    if not runs:
        return []

    run_ids = [run.run_id for run in runs]
    rec_rows = db.execute(
        select(Recommendation.recommendation_id, Recommendation.run_id, Recommendation.final_score)
        .where(Recommendation.run_id.in_(run_ids))
    ).all()

    rec_map: dict[str, list[Any]] = {}
    for row in rec_rows:
        rec_map.setdefault(row.run_id, []).append(row)

    rec_ids = [row.recommendation_id for row in rec_rows]
    outcome_map: dict[str, Any] = {}
    if rec_ids:
        outcome_rows = db.execute(
            select(RecommendationOutcome.recommendation_id, RecommendationOutcome.outcome_label, RecommendationOutcome.close_return)
            .where(RecommendationOutcome.recommendation_id.in_(rec_ids))
        ).all()
        outcome_map = {row.recommendation_id: row for row in outcome_rows}

    buckets: dict[str, dict[str, Any]] = {}
    for run in runs:
        group_id = run.preset_id or "__adhoc__"
        preset = db.get(Preset, run.preset_id) if run.preset_id else None
        row = buckets.setdefault(
            group_id,
            {
                "preset_id": run.preset_id,
                "preset_name": preset.name if preset else "临时运行",
                "run_count": 0,
                "recommendation_count": 0,
                "labeled_count": 0,
                "success_count": 0,
                "avg_return": 0.0,
                "avg_score": 0.0,
                "last_run_at": None,
                "skill_count": 0,
            },
        )
        row["run_count"] += 1
        finished_at = run.finished_at.isoformat() if run.finished_at else None
        if finished_at and (row["last_run_at"] is None or finished_at > row["last_run_at"]):
            row["last_run_at"] = finished_at
        row["skill_count"] = max(row["skill_count"], len(_loads(run.selected_skills_json, [])))

        run_recs = rec_map.get(run.run_id, [])
        row["recommendation_count"] += len(run_recs)
        row["avg_score"] += sum(float(rec.final_score or 0) for rec in run_recs)
        for rec in run_recs:
            outcome = outcome_map.get(rec.recommendation_id)
            if outcome and outcome.outcome_label != "pending":
                row["labeled_count"] += 1
                row["avg_return"] += float(outcome.close_return or 0)
                if outcome.outcome_label == "success":
                    row["success_count"] += 1

    result: list[dict[str, Any]] = []
    for row in buckets.values():
        recommendation_count = max(1, int(row["recommendation_count"]))
        labeled_count = int(row["labeled_count"])
        result.append(
            {
                "preset_id": row["preset_id"],
                "preset_name": row["preset_name"],
                "run_count": row["run_count"],
                "recommendation_count": row["recommendation_count"],
                "skill_count": row["skill_count"],
                "labeled_count": labeled_count,
                "success_count": row["success_count"],
                "hit_rate": round(row["success_count"] / labeled_count, 3) if labeled_count else 0.0,
                "avg_return": round(row["avg_return"] / labeled_count, 2) if labeled_count else 0.0,
                "avg_score": round(row["avg_score"] / recommendation_count, 2),
                "last_run_at": row["last_run_at"],
            }
        )
    return sorted(result, key=lambda item: (item["run_count"], item["hit_rate"], item["recommendation_count"]), reverse=True)


def _empty_overview(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "run_count": 0,
        "recommendation_count": 0,
        "labeled_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "pending_count": 0,
        "watch_count": 0,
        "reviewed_count": 0,
        "hit_rate": 0.0,
        "avg_return": 0.0,
        "avg_max_return": 0.0,
        "avg_drawdown": 0.0,
        "avg_score": 0.0,
        "latest_run_at": None,
        "top_buckets": [],
    }


def _runs_for_mode(db: Session, mode: str) -> list[RecommendationRun]:
    if mode not in VALID_RUN_MODES:
        raise ValueError("mode must be formal or experiment")
    return db.scalars(
        select(RecommendationRun).where(RecommendationRun.run_mode == mode).order_by(RecommendationRun.started_at.desc())
    ).all()


def _reviewed_count(db: Session, rec_ids: list[str]) -> int:
    if not rec_ids:
        return 0
    return db.scalar(
        select(func.count()).select_from(ReviewNote).where(ReviewNote.recommendation_id.in_(rec_ids))
    ) or 0


def _watched_count(db: Session, run_ids: list[str]) -> int:
    if not run_ids:
        return 0
    return len(
        db.scalars(
            select(WatchItem).where(WatchItem.source_run_id.in_(run_ids), WatchItem.watch_status == "watching")
        ).all()
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(5)]


def _loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return [] if default is None else default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return [] if default is None else default

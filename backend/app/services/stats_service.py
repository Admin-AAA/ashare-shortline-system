import json
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Preset, Recommendation, RecommendationOutcome, RecommendationRun, ReviewNote, SkillRegistry, WatchItem


VALID_RUN_MODES = {"formal", "experiment"}


def get_overview_stats(db: Session, mode: str = "formal") -> dict[str, Any]:
    runs = _runs_for_mode(db, mode)
    recs = _recommendations_for_runs(db, runs)
    outcomes = _outcomes_for_recommendations(db, recs)
    labeled = [item for item in outcomes.values() if item.outcome_label != "pending"]
    success_count = sum(1 for item in labeled if item.outcome_label == "success")
    failed_count = sum(1 for item in labeled if item.outcome_label == "failed")
    reviewed_count = _reviewed_count(db, recs)
    watched_count = _watched_count(db, runs)
    bucket_counts = Counter(rec.bucket for rec in recs)

    return {
        "mode": mode,
        "run_count": len(runs),
        "recommendation_count": len(recs),
        "labeled_count": len(labeled),
        "success_count": success_count,
        "failed_count": failed_count,
        "pending_count": max(0, len(recs) - len(labeled)),
        "watch_count": watched_count,
        "reviewed_count": reviewed_count,
        "hit_rate": round(success_count / len(labeled), 3) if labeled else 0.0,
        "avg_return": _mean([item.close_return for item in labeled]),
        "avg_max_return": _mean([item.max_return for item in labeled]),
        "avg_drawdown": _mean([item.max_drawdown for item in labeled]),
        "avg_score": _mean([rec.final_score for rec in recs]),
        "latest_run_at": runs[0].finished_at.isoformat() if runs and runs[0].finished_at else None,
        "top_buckets": [{"bucket": bucket, "count": count} for bucket, count in bucket_counts.most_common(5)],
    }


def get_skill_stats(db: Session, mode: str = "formal") -> list[dict[str, Any]]:
    runs = _runs_for_mode(db, mode)
    recs = _recommendations_for_runs(db, runs)
    outcomes = _outcomes_for_recommendations(db, recs)
    registry = {row.skill_id: row for row in db.scalars(select(SkillRegistry)).all()}
    buckets: dict[str, dict[str, Any]] = {}

    for rec in recs:
        for signal in _loads(rec.skill_breakdown_json, []):
            skill_id = str(signal.get("strategy_id") or "unknown")
            row = buckets.setdefault(
                skill_id,
                {
                    "skill_id": skill_id,
                    "name": registry.get(skill_id).name if registry.get(skill_id) else skill_id,
                    "current_mode": registry.get(skill_id).mode if registry.get(skill_id) else "unknown",
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
            row["avg_final_score"] += float(rec.final_score or 0)
            row["runs"].add(rec.run_id)
            row["last_seen_at"] = rec.recommended_at.isoformat()
            market_context = _loads(rec.market_context_json, {})
            row["market_regimes"][str((market_context.get("breadth") or {}).get("regime") or "未知")] += 1
            row["industries"][str(rec.industry or "未分类")] += 1
            row["setups"][str(signal.get("setup_match") or rec.setup_type or "未命中买点")] += 1

            outcome = outcomes.get(rec.recommendation_id)
            if outcome and outcome.outcome_label != "pending":
                row["labeled_count"] += 1
                row["avg_return"] += float(outcome.close_return or 0)
                if outcome.outcome_label == "success":
                    row["success_count"] += 1

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
    return sorted(result, key=lambda item: (item["sample_count"], item["hit_rate"], item["avg_signal_score"]), reverse=True)


def get_preset_stats(db: Session, mode: str = "formal") -> list[dict[str, Any]]:
    runs = _runs_for_mode(db, mode)
    if not runs:
        return []

    recs = _recommendations_for_runs(db, runs)
    rec_map: dict[str, list[Recommendation]] = {}
    for rec in recs:
        rec_map.setdefault(rec.run_id, []).append(rec)
    outcomes = _outcomes_for_recommendations(db, recs)

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
        row["last_run_at"] = run.finished_at.isoformat() if run.finished_at else row["last_run_at"]
        row["skill_count"] = max(row["skill_count"], len(_loads(run.selected_skills_json, [])))

        run_recs = rec_map.get(run.run_id, [])
        row["recommendation_count"] += len(run_recs)
        row["avg_score"] += sum(float(rec.final_score or 0) for rec in run_recs)
        for rec in run_recs:
            outcome = outcomes.get(rec.recommendation_id)
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


def _runs_for_mode(db: Session, mode: str) -> list[RecommendationRun]:
    if mode not in VALID_RUN_MODES:
        raise ValueError("mode must be formal or experiment")
    return db.scalars(
        select(RecommendationRun).where(RecommendationRun.run_mode == mode).order_by(RecommendationRun.started_at.desc())
    ).all()


def _recommendations_for_runs(db: Session, runs: list[RecommendationRun]) -> list[Recommendation]:
    run_ids = [run.run_id for run in runs]
    if not run_ids:
        return []
    return db.scalars(
        select(Recommendation).where(Recommendation.run_id.in_(run_ids)).order_by(Recommendation.recommended_at.desc())
    ).all()


def _outcomes_for_recommendations(
    db: Session,
    recommendations: list[Recommendation],
) -> dict[str, RecommendationOutcome]:
    recommendation_ids = [rec.recommendation_id for rec in recommendations]
    if not recommendation_ids:
        return {}
    return {
        row.recommendation_id: row
        for row in db.scalars(
            select(RecommendationOutcome).where(RecommendationOutcome.recommendation_id.in_(recommendation_ids))
        ).all()
    }


def _reviewed_count(db: Session, recommendations: list[Recommendation]) -> int:
    recommendation_ids = [rec.recommendation_id for rec in recommendations]
    if not recommendation_ids:
        return 0
    return len(
        db.scalars(select(ReviewNote).where(ReviewNote.recommendation_id.in_(recommendation_ids))).all()
    )


def _watched_count(db: Session, runs: list[RecommendationRun]) -> int:
    run_ids = [run.run_id for run in runs]
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

import json
import threading
import time
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, load_only

from app.models.tables import (
    Preset,
    Recommendation,
    RecommendationOutcome,
    RecommendationRun,
    ReviewNote,
    SkillRegistry,
    WatchItem,
)
from app.services.outcome_service import (
    is_accurate_recommendation,
    is_trade_executed,
    metrics_by_strategy,
    overview_metrics,
    resolved_trade_return,
)
from app.services.outcome_service import is_outcome_labeled, normalized_outcome_label
from app.services.official_sample_scope import official_sample_conditions

import logging
logger = logging.getLogger(__name__)

VALID_RUN_MODES = {"formal", "experiment", "backtest"}

# 短时缓存：stats 数据变更频率低（仅在创建新 run 后变化），5 秒 TTL 足够
_stats_cache: dict[str, tuple[float, Any]] = {}
_stats_cache_lock = threading.Lock()
_STATS_CACHE_TTL = 30.0


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


def get_overview_stats(db: Session) -> dict[str, Any]:
    return _cached("overview", lambda: _get_overview_stats(db))


def _get_overview_stats(db: Session) -> dict[str, Any]:
    runs = _runs_for_mode(db)
    run_ids = [run.run_id for run in runs]
    if not run_ids:
        return _empty_overview()

    raw_rec_rows = db.execute(
        select(
            Recommendation.recommendation_id,
            Recommendation.run_id,
            Recommendation.final_score,
            Recommendation.trade_date,
            Recommendation.code,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(
            Recommendation.run_id.in_(run_ids),
            *official_sample_conditions(),
        )
        .order_by(
            Recommendation.trade_date,
            Recommendation.code,
            Recommendation.final_score.desc(),
            Recommendation.recommendation_id,
        )
    ).all()
    seen: set[tuple[str, str]] = set()
    rec_rows = []
    for rec in raw_rec_rows:
        key = (str(rec.trade_date), str(rec.code))
        if key in seen:
            continue
        seen.add(key)
        rec_rows.append(rec)

    rec_ids = [row.recommendation_id for row in rec_rows]
    reviewed_count = _reviewed_count(db, rec_ids)
    watched_count = _watched_count(db, run_ids)
    official = overview_metrics(db)

    return {
        "run_count": len(runs),
        "recommendation_count": official["total_recommendations"],
        "labeled_count": official["labeled_count"],
        "triggered_count": official["executed_trade_count"],
        "success_count": official["success_count"],
        "failed_count": official["failed_count"],
        "missed_count": official["missed_count"],
        "pending_count": official["pending_count"],
        "watch_count": watched_count,
        "reviewed_count": reviewed_count,
        "trigger_rate": official["trigger_rate"],
        "hit_rate": official["hit_rate"],
        "avg_return": official["avg_return"],
        "avg_executed_return": official["avg_executed_return"],
        "avg_weighted_return": official["avg_weighted_return"],
        "avg_close_return": official["avg_close_return"],
        "avg_max_return": official["avg_mfe"],
        "avg_drawdown": official["avg_mae"],
        "avg_score": _mean([row.final_score for row in rec_rows]),
        "latest_run_at": runs[0].finished_at.isoformat() if runs and runs[0].finished_at else None,
        # ── 9-KPI 评估指标 ──
        "recommendation_accuracy": official.get("recommendation_accuracy", 0.0),
        "average_net_return": official.get("average_net_return", 0.0),
        "median_net_return": official.get("median_net_return", 0.0),
        "profit_loss_ratio": official.get("profit_loss_ratio", 0.0),
        "max_drawdown": official.get("max_drawdown", 0.0),
        "positive_month_ratio": official.get("positive_month_ratio", 0.0),
        "benchmark_excess_return": official.get("benchmark_excess_return", 0.0),
        "sample_confidence": official.get("sample_confidence", "low"),
        "false_positive_notes": official.get("false_positive_notes", []),
    }


def get_skill_stats(db: Session) -> list[dict[str, Any]]:
    return _cached("skill", lambda: _get_skill_stats(db))


def _get_skill_stats(db: Session) -> list[dict[str, Any]]:
    t0 = time.perf_counter()
    runs = _runs_for_mode(db, extra_cols=[RecommendationRun.market_snapshot_json])
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
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(
            Recommendation.run_id.in_(run_ids),
            *official_sample_conditions(),
        )
    ).all()
    t2 = time.perf_counter()

    rec_ids = [row.recommendation_id for row in rec_rows]
    outcome_map: dict[str, Any] = {}
    if rec_ids:
        outcome_rows = db.execute(
            select(
                RecommendationOutcome.recommendation_id,
                RecommendationOutcome.outcome_label,
                RecommendationOutcome.entry_triggered,
                RecommendationOutcome.executed_return,
            )
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
        has_outcome = is_outcome_labeled(outcome)
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
                    "triggered_count": 0,
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
                if is_trade_executed(outcome):
                    row["triggered_count"] += 1
                    row["avg_return"] += resolved_trade_return(outcome)
                    if is_accurate_recommendation(outcome):
                        row["success_count"] += 1

    t5 = time.perf_counter()
    official_by_skill = {
        item["strategy_id"]: item for item in metrics_by_strategy(db)
    }
    result: list[dict[str, Any]] = []
    for row in buckets.values():
        official = official_by_skill.get(row["skill_id"])
        sample_count = max(1, int((official or row)["sample_count"]))
        labeled_count = int(
            official["labeled_count"] if official else row["labeled_count"]
        )
        triggered_count = int(
            official["triggered_count"] if official else row["triggered_count"]
        )
        success_count = int(
            official["success_count"] if official else row["success_count"]
        )
        result.append(
            {
                "skill_id": row["skill_id"],
                "name": row["name"],
                "current_mode": row["current_mode"],
                "sample_count": official["sample_count"] if official else row["sample_count"],
                "run_count": len(row["runs"]),
                "labeled_count": labeled_count,
                "triggered_count": triggered_count,
                "failed_count": max(0, triggered_count - success_count),
                "missed_count": max(0, labeled_count - triggered_count),
                "pending_count": max(0, row["sample_count"] - labeled_count),
                "success_count": success_count,
                "trigger_rate": round(triggered_count / labeled_count, 3) if labeled_count else 0.0,
                "hit_rate": official["hit_rate"] if official else (
                    round(success_count / triggered_count, 3) if triggered_count else 0.0
                ),
                "avg_signal_score": round(row["avg_signal_score"] / sample_count, 2),
                "avg_final_score": round(row["avg_final_score"] / sample_count, 2),
                "avg_return": official["avg_return"] if official else (
                    round(row["avg_return"] / triggered_count, 2) if triggered_count else 0.0
                ),
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


def get_preset_stats(db: Session) -> list[dict[str, Any]]:
    return _cached("preset", lambda: _get_preset_stats(db))


def _get_preset_stats(db: Session) -> list[dict[str, Any]]:
    runs = _runs_for_mode(db)
    if not runs:
        return []

    run_ids = [run.run_id for run in runs]
    raw_rec_rows = db.execute(
        select(
            Recommendation.recommendation_id,
            Recommendation.run_id,
            Recommendation.final_score,
            Recommendation.trade_date,
            Recommendation.code,
        )
        .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
        .where(
            Recommendation.run_id.in_(run_ids),
            *official_sample_conditions(),
        )
        .order_by(
            Recommendation.trade_date,
            Recommendation.code,
            Recommendation.final_score.desc(),
            Recommendation.recommendation_id,
        )
    ).all()
    seen: set[tuple[str, str]] = set()
    rec_rows = []
    for rec in raw_rec_rows:
        key = (str(rec.trade_date), str(rec.code))
        if key in seen:
            continue
        seen.add(key)
        rec_rows.append(rec)

    rec_map: dict[str, list[Any]] = {}
    for row in rec_rows:
        rec_map.setdefault(row.run_id, []).append(row)

    rec_ids = [row.recommendation_id for row in rec_rows]
    outcome_map: dict[str, Any] = {}
    if rec_ids:
        outcome_rows = db.execute(
            select(
                RecommendationOutcome.recommendation_id,
                RecommendationOutcome.outcome_label,
                RecommendationOutcome.entry_triggered,
                RecommendationOutcome.executed_return,
            )
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
                "triggered_count": 0,
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
            if is_outcome_labeled(outcome):
                row["labeled_count"] += 1
                if is_trade_executed(outcome):
                    row["triggered_count"] += 1
                    row["avg_return"] += resolved_trade_return(outcome)
                    if is_accurate_recommendation(outcome):
                        row["success_count"] += 1

    result: list[dict[str, Any]] = []
    for row in buckets.values():
        recommendation_count = max(1, int(row["recommendation_count"]))
        labeled_count = int(row["labeled_count"])
        triggered_count = int(row["triggered_count"])
        result.append(
            {
                "preset_id": row["preset_id"],
                "preset_name": row["preset_name"],
                "run_count": row["run_count"],
                "recommendation_count": row["recommendation_count"],
                "skill_count": row["skill_count"],
                "labeled_count": labeled_count,
                "triggered_count": triggered_count,
                "failed_count": max(0, triggered_count - row["success_count"]),
                "missed_count": max(0, labeled_count - triggered_count),
                "pending_count": max(0, row["recommendation_count"] - labeled_count),
                "success_count": row["success_count"],
                "trigger_rate": round(triggered_count / labeled_count, 3) if labeled_count else 0.0,
                "hit_rate": round(row["success_count"] / triggered_count, 3) if triggered_count else 0.0,
                "avg_return": round(row["avg_return"] / triggered_count, 2) if triggered_count else 0.0,
                "avg_score": round(row["avg_score"] / recommendation_count, 2),
                "last_run_at": row["last_run_at"],
            }
        )
    return sorted(result, key=lambda item: (item["run_count"], item["hit_rate"], item["recommendation_count"]), reverse=True)


def _empty_overview() -> dict[str, Any]:
    return {
        "run_count": 0,
        "recommendation_count": 0,
        "labeled_count": 0,
        "triggered_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "missed_count": 0,
        "pending_count": 0,
        "watch_count": 0,
        "reviewed_count": 0,
        "hit_rate": 0.0,
        "trigger_rate": 0.0,
        "avg_return": 0.0,
        "avg_executed_return": 0.0,
        "avg_weighted_return": 0.0,
        "avg_close_return": 0.0,
        "avg_max_return": 0.0,
        "avg_drawdown": 0.0,
        "avg_score": 0.0,
        "latest_run_at": None,
    }


def _runs_for_mode(
    db: Session,
    extra_cols: list | None = None,
    run_mode: str = "formal",
) -> list[RecommendationRun]:
    # 仅加载各 stats 函数实际使用的列，避免物化每条 run 的 4 个大型 JSON 列
    # （market_snapshot_json / result_summary_json / filter_audit_json / selected_skills_json）。
    # 全量加载 890 条 run 会产生 ~2.4GB 传输，冷调用 4.5s+；按列裁剪后 <0.05s（~200x）。
    # get_skill_stats 需要 regime（埋在 market_snapshot_json），须显式 opt-in；
    # get_overview_stats / get_preset_stats 不需要该巨型列，默认不加载。
    cols = [
        RecommendationRun.run_id,
        RecommendationRun.preset_id,
        RecommendationRun.finished_at,
        RecommendationRun.selected_skills_json,
    ]
    if extra_cols:
        cols = cols + list(extra_cols)
    return db.scalars(
        select(RecommendationRun)
        .where(
            RecommendationRun.run_mode == run_mode,
            RecommendationRun.status == "completed",
        )
        .options(load_only(*cols))
        .order_by(RecommendationRun.started_at.desc())
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

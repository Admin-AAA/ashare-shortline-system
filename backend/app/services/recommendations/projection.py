from __future__ import annotations

from typing import Any

from app.schemas.dashboard import (
    CandidateTraceRow,
    CockpitRecommendation,
    DataQualitySummary,
    MarketTemperature,
    RecommendationExplanation,
    RiskRadar,
    RunProgress,
    SkillReviewSummary,
)
from app.services.recommendations.explanation import (
    consensus_label,
    data_quality_summary,
    loads_json,
    risk_level_from_tags,
)


def build_market_temperature(snapshot: dict[str, Any]) -> MarketTemperature:
    sample_count = int(_get(snapshot, "sample_count", 0) or 0)
    up_count = int(_get(snapshot, "up_count", 0) or 0)
    down_count = int(_get(snapshot, "down_count", 0) or 0)
    limit_up_count = int(_get(snapshot, "limit_up_count", 0) or 0)
    limit_down_count = int(_get(snapshot, "limit_down_count", 0) or 0)
    up_ratio = round(up_count / sample_count, 3) if sample_count else 0
    heat_score = round(
        min(100.0, max(0.0, up_ratio * 70 + limit_up_count * 0.4 - limit_down_count * 0.8)),
        2,
    )
    breadth = _get(snapshot, "breadth", {}) or {}

    return MarketTemperature(
        trade_date=_get(snapshot, "trade_date", ""),
        sample_count=sample_count,
        up_count=up_count,
        down_count=down_count,
        limit_up_count=limit_up_count,
        limit_down_count=limit_down_count,
        up_ratio=up_ratio,
        heat_score=heat_score,
        regime=_get(breadth, "market_regime") or _get(breadth, "market_sentiment") or "unknown",
        main_industries=list(_get(snapshot, "main_industries", []) or []),
    )


def build_cockpit_recommendation(rec: Any, watch: Any = None) -> CockpitRecommendation:
    feature_snapshot = loads_json(_get(rec, "feature_snapshot_json"), {})
    skill_breakdown = loads_json(_get(rec, "skill_breakdown_json"), [])
    risk_tags = loads_json(_get(rec, "risk_tags_json"), [])
    info_tags = loads_json(_get(rec, "info_tags_json"), [])
    final_score = float(_get(rec, "final_score", 0.0) or 0.0)
    score_line_meta = _get(feature_snapshot, "score_line_meta", {}) or {}
    effective_score_line = _get(score_line_meta, "effective_score_line")
    score_gap = None
    if effective_score_line is not None:
        score_gap = round(final_score - float(effective_score_line), 3)

    return CockpitRecommendation(
        recommendation_id=_get(rec, "recommendation_id", ""),
        run_id=_get(rec, "run_id", ""),
        trade_date=_get(rec, "trade_date", ""),
        code=_get(rec, "code", ""),
        name=_get(rec, "name", ""),
        industry=_get(rec, "industry"),
        final_score=final_score,
        score_gap=score_gap,
        bucket=_get(rec, "bucket", "") or "",
        setup_type=_get(rec, "setup_type"),
        rationale=_get(rec, "rationale", "") or "",
        risk_level=risk_level_from_tags(risk_tags),
        risk_tags=list(risk_tags or []),
        info_tags=list(info_tags or []),
        status=_get(rec, "status", "pending") or "pending",
        watch_status=_get(watch, "watch_status", "none") if watch else "none",
        consensus_label=consensus_label(skill_breakdown),
        data_quality=DataQualitySummary(**data_quality_summary(feature_snapshot)),
    )


def build_skill_reviews(skill_breakdown: list[dict[str, Any]]) -> list[SkillReviewSummary]:
    reviews: list[SkillReviewSummary] = []
    for row in skill_breakdown or []:
        review = _get(row, "agent_skill_review", {}) or {}
        supports = _get(review, "supports")
        if supports is None:
            supports = _get(review, "evidence", [])
        reviews.append(
            SkillReviewSummary(
                skill_id=_get(row, "strategy_id", "") or _get(row, "skill_id", "") or "",
                skill_name=_get(row, "strategy_name", "") or _get(row, "skill_name", "") or "",
                stance=_get(review, "stance", "unknown") or "unknown",
                score=float(_get(review, "score", _get(row, "score", 0.0)) or 0.0),
                confidence=float(_get(review, "confidence", 0.0) or 0.0),
                supports=list(supports or []),
                risks=list(_get(review, "risks", []) or []),
                action=_get(review, "action", "watch") or "watch",
            )
        )
    return reviews


def build_recommendation_explanation(
    rec: Any,
    trace_rows: list[Any] | None = None,
    watch: Any = None,
) -> RecommendationExplanation:
    feature_snapshot = loads_json(_get(rec, "feature_snapshot_json"), {})
    skill_breakdown = loads_json(_get(rec, "skill_breakdown_json"), [])
    score_breakdown = (
        _get(feature_snapshot, "component_scores")
        or _get(feature_snapshot, "score_breakdown")
        or {}
    )
    pipeline_trace = [
        CandidateTraceRow(
            code=_get(row, "code", _get(rec, "code", "")),
            stage=_get(row, "stage", ""),
            passed=bool(_get(row, "passed", False)),
            reason=_get(row, "reason", "") or "",
            metrics=_get(row, "metrics", {}) or {},
        )
        for row in trace_rows or []
    ]
    trade_plan = {}
    for key in ("entry_rule", "invalid_rule"):
        value = _get(rec, key)
        if value is not None:
            trade_plan[key] = value
    for key in ("stop_loss", "take_profit"):
        value = _get(feature_snapshot, key)
        if value is not None:
            trade_plan[key] = value

    return RecommendationExplanation(
        recommendation_id=_get(rec, "recommendation_id", ""),
        score_breakdown=score_breakdown,
        skill_reviews=build_skill_reviews(skill_breakdown),
        pipeline_trace=pipeline_trace,
        trade_plan=trade_plan,
        review_feedback={"watch_status": _get(watch, "watch_status", "none") if watch else "none"},
    )


def build_run_progress(run: Any) -> RunProgress:
    if run is None:
        return RunProgress()

    status = _get(run, "status", "idle") or "idle"
    return RunProgress(
        run_id=_get(run, "run_id"),
        status=status,
        phase=_get(run, "progress_phase") or _get(run, "checkpoint_phase"),
        progress_pct=_get(run, "progress_pct"),
        message=_get(run, "progress_message", "") or "",
        error_message=_get(run, "error_message"),
        heartbeat_stale=bool(_get(run, "is_stale", False)),
        resumable=status in {"interrupted", "failed"},
    )


def build_risk_radar(recommendations: list[CockpitRecommendation]) -> RiskRadar:
    high = 0
    medium = 0
    info = 0
    tags: list[str] = []

    for rec in recommendations:
        if rec.risk_level == "high":
            high += 1
        elif rec.risk_level == "medium":
            medium += 1
        elif rec.risk_level == "low":
            info += len(rec.info_tags)

        for tag in rec.risk_tags:
            if tag not in tags:
                tags.append(tag)

    return RiskRadar(high=high, medium=medium, info=info, tags=tags[:12])


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)

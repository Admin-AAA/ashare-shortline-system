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
    SkillBreakdownItem,
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
    heat_score = round(min(100.0, up_ratio * 70 + limit_up_count * 0.4 - limit_down_count * 0.8), 2)
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
        regime=_get(breadth, "market_regime") or _get(breadth, "market_sentiment") or _get(breadth, "regime") or "unknown",
        main_industries=list(_get(snapshot, "main_industries", []) or []),
    )


def _build_skill_breakdown_items(skill_breakdown: list[dict[str, Any]]) -> list[SkillBreakdownItem]:
    """从 skill_breakdown_json 提取精简展示项（strategy_name/score/phase/setup_match/evidence）。"""
    items: list[SkillBreakdownItem] = []
    for row in skill_breakdown or []:
        if not isinstance(row, dict):
            continue
        items.append(
            SkillBreakdownItem(
                strategy_name=str(row.get("strategy_name") or row.get("strategy_id") or ""),
                score=float(row.get("score") or 0.0),
                phase=str(row.get("phase") or ""),
                setup_match=str(row.get("setup_match") or ""),
                confidence=float(row.get("confidence") or 0.0),
                evidence=list(row.get("evidence") or []),
                hard_veto=bool(row.get("hard_veto")),
                veto_reason=str(row.get("veto_reason") or ""),
            )
        )
    return items


def build_cockpit_recommendation(rec: Any, watch: Any = None) -> CockpitRecommendation:
    feature_snapshot = _loads_recommendation_field(rec, "feature_snapshot_json", "feature_snapshot", {})
    skill_breakdown = _loads_recommendation_field(rec, "skill_breakdown_json", "skill_breakdown", [])
    risk_tags = _loads_recommendation_field(rec, "risk_tags_json", "risk_tags", [])
    info_tags = _loads_recommendation_field(rec, "info_tags_json", "info_tags", [])
    market_context = _loads_recommendation_field(rec, "market_context_json", "market_context", {}) or {}
    agent_summary = _loads_recommendation_field(rec, "agent_summary_json", "agent_summary", {}) or {}
    final_score = float(_get(rec, "final_score", 0.0) or 0.0)
    # 序列化后的 dict（来自 _serialize_recommendation_cached）已有顶层 llm_stance/llm_delta，
    # 优先用；否则从 agent_summary 提取（原始 Recommendation 对象路径）
    llm_stance = _get(rec, "llm_stance") or _get(agent_summary, "stance")
    llm_delta_raw = _get(rec, "llm_delta")
    llm_delta = _as_float_or_none(llm_delta_raw) if llm_delta_raw is not None else _as_float_or_none(_get(agent_summary, "score_delta"))
    score_line_meta = _get(feature_snapshot, "score_line_meta", {}) or {}
    effective_score_line = _get(score_line_meta, "effective_score_line")
    score_gap = None
    if effective_score_line is not None:
        score_gap = round(final_score - float(effective_score_line), 3)
    watch_status = _get(rec, "watch_status", "none")
    if watch:
        watch_status = _get(watch, "watch_status", watch_status)

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
        watch_status=watch_status,
        consensus_label=consensus_label(skill_breakdown),
        data_quality=DataQualitySummary(**data_quality_summary(feature_snapshot)),
        preset_name=_get(rec, "preset_name"),
        preset_id=_get(rec, "preset_id"),
        run_name=_get(rec, "run_name"),
        ideal_buy=_as_float_or_none(feature_snapshot.get("ideal_buy")),
        secondary_buy=_as_float_or_none(feature_snapshot.get("secondary_buy")),
        stop_loss=_as_float_or_none(feature_snapshot.get("stop_loss")),
        take_profit=_as_float_or_none(feature_snapshot.get("take_profit")),
        recommend_price=_as_float_or_none(_get(rec, "recommend_price")),
        entry_rule=_get(rec, "entry_rule"),
        invalid_rule=_get(rec, "invalid_rule"),
        degraded_reason=_detect_degradation(info_tags, _get(rec, "rationale", "") or ""),
        market_context=market_context,
        skill_breakdown=_build_skill_breakdown_items(skill_breakdown),
        # LLM 审查顶层快捷字段（B 项：候选池卡片不展开即可见 LLM 态度）
        llm_stance=llm_stance,
        llm_delta=llm_delta,
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
    feature_snapshot = _loads_recommendation_field(rec, "feature_snapshot_json", "feature_snapshot", {})
    skill_breakdown = _loads_recommendation_field(rec, "skill_breakdown_json", "skill_breakdown", [])
    score_breakdown = (
        _get(feature_snapshot, "component_scores")
        or _get(feature_snapshot, "score_breakdown")
        or _get(rec, "score_breakdown")
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


def _loads_recommendation_field(rec: Any, json_key: str, serialized_key: str, default: Any) -> Any:
    raw_json = _get(rec, json_key)
    if raw_json is not None:
        loaded = loads_json(raw_json, None)
        if loaded is not None:
            return loaded
    return loads_json(_get(rec, serialized_key), default)


def _as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 降级信号关键词。后端 aggregator 在 Skill Agent 失败/数据缺失时会把标记写进
# info_tags 和 rationale，但前端把它们和普通标签混排，用户难以一眼识别。
# 这里提取出明确的降级原因，供卡片头部显示醒目徽章。
_DEGRADATION_MARKERS: tuple[str, ...] = (
    "Skill Agent 暂不可用",
    "降级输出",
    "评分与 Agent 加分已降权",
    "Skill Agent 直接审查失败",
    "规则预审与市场数据降级输出",
)


def _detect_degradation(info_tags: list[str] | None, rationale: str) -> str | None:
    """从 info_tags / rationale 识别降级原因，返回首个匹配的标记，否则 None。"""
    haystack = " | ".join(str(t) for t in (info_tags or []))
    for marker in _DEGRADATION_MARKERS:
        if marker in haystack or marker in (rationale or ""):
            return marker
    return None

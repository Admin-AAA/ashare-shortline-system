from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.time import utc_isoformat
from app.engine.aggregator import market_regime_weights

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


_KEY_LEVEL_FIELDS = (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "yesterday_high",
    "yesterday_low",
    "platform_high_20d",
    "platform_high_60d",
    "high_20d",
    "low_20d",
    "support_levels",
    "resistance_levels",
)


def _key_levels(feature_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project the small set of prices needed to interpret execution rules."""
    levels = {
        key: feature_snapshot[key]
        for key in _KEY_LEVEL_FIELDS
        if feature_snapshot.get(key) is not None
    }

    # Older snapshots stored only the distance from the 60-day high. Rebuild the
    # displayed reference price without loading the full snapshot into list rows.
    if "platform_high_60d" not in levels:
        price = _as_float_or_none(feature_snapshot.get("price"))
        distance = _as_float_or_none(feature_snapshot.get("off_high60_pct"))
        denominator = 1 + distance / 100 if distance is not None else 0
        if price is not None and price > 0 and denominator > 0:
            levels["high_60d"] = round(price / denominator, 2)
    return levels


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

    recommended_at_value = _get(rec, "recommended_at")
    recommended_at_str = None
    if recommended_at_value is not None:
        try:
            recommended_at_str = (
                recommended_at_value.isoformat()
                if hasattr(recommended_at_value, "isoformat")
                else str(recommended_at_value)
            )
        except Exception:  # noqa: BLE001 - best-effort fallback, now debug-logged
            recommended_at_str = str(recommended_at_value)

    return CockpitRecommendation(
        recommendation_id=_get(rec, "recommendation_id", ""),
        run_id=_get(rec, "run_id", ""),
        trade_date=_get(rec, "trade_date", ""),
        recommended_at=recommended_at_str,
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
        review_status=_get(feature_snapshot, "review_status"),
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
        resumable=bool(_get(run, "can_resume", False)),
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


def serialize_recommendation(
    rec: Any,
    watch: Any = None,
    run: Any = None,
    preset_name: str | None = None,
    *,
    slim: bool = False,
) -> dict[str, Any]:
    """将 Recommendation 及其已加载关联对象投影为 API 稳定字典。

    数据库查询由调用方批量完成；本函数只负责确定性字段投影，因此可在不连接
    MySQL 的场景下参与 golden-master 对拍。

    slim=True 时剥离体积庞大的 feature_snapshot / market_context / skill_breakdown /
    agent_logs / score_breakdown / score_line_meta / execution_viability（单条可达数百 KB），
    供推荐历史列表接口使用——列表页只用 trade_date/code/final_score/bucket/价位/规则等轻量字段，
    完整审计字段由 /api/recommendations/{id} 详情接口按需返回。
    """
    feature_snapshot = loads_json(_get(rec, "feature_snapshot_json"), {}) or {}
    market_context = loads_json(_get(rec, "market_context_json"), {}) or {}
    risk_tags = loads_json(_get(rec, "risk_tags_json"), []) or []
    if "aggregation_weights" not in feature_snapshot:
        feature_snapshot["aggregation_weights"] = market_regime_weights(market_context)
    agent_summary = loads_json(_get(rec, "agent_summary_json"), {}) or {}
    selected_skills = loads_json(_get(run, "selected_skills_json"), []) if run else []
    strategy_versions = [
        {
            "strategy_id": str(item.get("skill_id") or ""),
            "name": str(item.get("name") or ""),
            "version_id": str(item.get("version_id") or ""),
        }
        for item in selected_skills
        if isinstance(item, dict) and item.get("skill_id") and item.get("version_id")
    ]
    recommended_at = _get(rec, "recommended_at")
    payload = {
        "recommendation_id": _get(rec, "recommendation_id"),
        "run_id": _get(rec, "run_id"),
        "trade_date": _get(rec, "trade_date"),
        "recommended_at": recommended_at.isoformat() if recommended_at else None,
        "code": _get(rec, "code"),
        "name": _get(rec, "name"),
        "industry": _get(rec, "industry"),
        "recommend_price": _get(rec, "recommend_price"),
        "stop_loss": feature_snapshot.get("stop_loss"),
        "take_profit": feature_snapshot.get("take_profit"),
        "ideal_buy": feature_snapshot.get("ideal_buy"),
        "secondary_buy": feature_snapshot.get("secondary_buy"),
        "bucket": _get(rec, "bucket") or "",
        "final_score": _get(rec, "final_score"),
        "consensus_score": _get(rec, "consensus_score"),
        "setup_type": _get(rec, "setup_type"),
        "entry_rule": _get(rec, "entry_rule"),
        "invalid_rule": _get(rec, "invalid_rule"),
        "rationale": _get(rec, "rationale"),
        "risk_tags": risk_tags,
        "risk_level": risk_level_from_tags(risk_tags),
        "info_tags": loads_json(_get(rec, "info_tags_json"), []),
        "status": _get(rec, "status"),
        "recent_repeat": _get(rec, "recent_repeat"),
        "watch_status": _get(watch, "watch_status", "none") if watch else "none",
        "watch_note": _get(watch, "note") if watch else None,
        "preset_name": preset_name,
        "preset_id": _get(run, "preset_id") if run else None,
        "run_name": _get(run, "run_name") if run else None,
        # 证据身份透传（None 安全）：历史页/详情页据此区分 LIVE 与历史重建，
        # 不改变任何既有字段语义。
        "evidence_class": _get(run, "evidence_class") if run else None,
        "actual_generated_at": _isoformat_or_none(_get(run, "actual_generated_at")) if run else None,
        "reconstruction_batch_id": _get(run, "reconstruction_batch_id") if run else None,
        "key_levels": _key_levels(feature_snapshot),
    }
    if slim:
        # 列表路径只补 LLM 态度顶层快捷字段（轻量），其余大字段留给详情接口。
        payload["llm_stance"] = agent_summary.get("stance")
        payload["llm_delta"] = agent_summary.get("score_delta")
        return payload
    payload.update({
        "feature_snapshot": feature_snapshot,
        "market_context": market_context,
        "skill_breakdown": loads_json(_get(rec, "skill_breakdown_json"), []),
        "agent_logs": agent_summary.get("logs", []),
        "llm_stance": agent_summary.get("stance"),
        "llm_delta": agent_summary.get("score_delta"),
        "score_breakdown": feature_snapshot.get("score_breakdown", {}),
        "score_line_meta": feature_snapshot.get("score_line_meta", {}),
        "execution_viability": feature_snapshot.get("execution_viability", {}),
        "execution_version": _get(run, "execution_version") if run else None,
        "strategy_versions": strategy_versions,
    })
    return payload


def serialize_run_summary(
    run: Any,
    preset: Any,
    hit_rate: float,
    *,
    active_statuses: set[str],
    heartbeat_stale_seconds: int,
    now: datetime | None = None,
    slim_result_summary: bool = False,
) -> dict[str, Any]:
    """投影运行摘要；命中率与预设关联由调用方查询并传入。

    slim_result_summary=True 时剥离 result_summary 中体积庞大的 pipeline / auxiliary_data
    审计快照（单条可达数百 KB），用于 runs 列表接口——列表页只用 top_codes / avg_score
    等轻量字段，完整审计快照由 /api/runs/{id} 详情接口按需返回。
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    heartbeat_at = _get(run, "heartbeat_at")
    heartbeat_age_seconds = (
        max(0, int((now - heartbeat_at).total_seconds()))
        if heartbeat_at is not None
        else None
    )
    progress_current = int(_get(run, "progress_current", 0) or 0)
    progress_total = int(_get(run, "progress_total", 0) or 0)
    progress_pct = round(min(100.0, max(0.0, progress_current * 100 / progress_total)), 2) if progress_total else None
    status = _get(run, "status")
    return {
        "run_id": _get(run, "run_id"),
        "run_name": _get(run, "run_name"),
        "preset_id": _get(run, "preset_id"),
        "preset_name": _get(preset, "name", "临时运行") if preset else "临时运行",
        "trade_date": _get(run, "trade_date"),
        "run_mode": _get(run, "run_mode"),
        "evaluation_scope": _get(run, "evaluation_scope") or "historical_replay",
        "evidence_class": _get(run, "evidence_class"),
        "actual_generated_at": _isoformat_or_none(_get(run, "actual_generated_at")),
        "reconstruction_batch_id": _get(run, "reconstruction_batch_id"),
        "started_at": _isoformat_or_none(_get(run, "started_at")),
        "finished_at": _isoformat_or_none(_get(run, "finished_at")),
        "market_scope": _get(run, "market_scope"),
        "market_scope_value": _get(run, "market_scope_value"),
        "risk_template": _get(run, "risk_template"),
        "score_line": _get(run, "score_line"),
        "universe_count": _get(run, "universe_count"),
        "recommended_count": _get(run, "recommended_count"),
        "status": status,
        "error_message": _get(run, "error_message"),
        "heartbeat_at": _isoformat_or_none(heartbeat_at),
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_stale_seconds": heartbeat_stale_seconds,
        "is_stale": bool(status in active_statuses and heartbeat_age_seconds is not None and heartbeat_age_seconds > heartbeat_stale_seconds),
        "progress_phase": _get(run, "progress_phase"),
        "progress_current": progress_current,
        "progress_total": progress_total,
        "progress_pct": progress_pct,
        "progress_message": _get(run, "progress_message"),
        "interrupted_at": _isoformat_or_none(_get(run, "interrupted_at")),
        "skill_count": len(loads_json(_get(run, "selected_skills_json"), [])),
        "hit_rate": hit_rate,
        "result_summary": _result_summary_for_run(run, slim_result_summary),
        "checkpoint_phase": _get(run, "checkpoint_phase"),
    }


def _result_summary_for_run(run: Any, slim: bool) -> dict[str, Any]:
    summary = loads_json(_get(run, "result_summary_json"), {})
    if not slim or not isinstance(summary, dict):
        return summary
    # pipeline / auxiliary_data 是单条数百 KB 的审计快照，列表页无人使用。
    return {k: v for k, v in summary.items() if k not in ("pipeline", "auxiliary_data")}



def _isoformat_or_none(value: Any) -> str | None:
    # 运行表时间列存无时区 UTC；带上 Z 让前端按 UTC 解析，避免北京时间页面早 8 小时。
    if isinstance(value, datetime):
        return utc_isoformat(value)
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


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

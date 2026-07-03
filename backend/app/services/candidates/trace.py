from __future__ import annotations

from typing import Any


def candidate_trace_row(
    *,
    code: str,
    stage: str,
    passed: bool,
    reason: str = "",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": str(code),
        "stage": str(stage),
        "passed": bool(passed),
        "reason": str(reason or ""),
        "metrics": dict(metrics or {}),
    }


def score_distribution(cards: list[dict[str, Any]]) -> dict[str, int]:
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


def quality_distribution(cards: list[dict[str, Any]]) -> dict[str, int]:
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


def coverage_heatmap(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def merge_reason_counts(*items: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for item in items:
        for key, value in item.items():
            merged[str(key)] = merged.get(str(key), 0) + int(value or 0)
    return merged


def build_stage_summary(
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
    agent_review_disabled = bool(pipeline.get("agent_review_disabled") or (skill_gate_audit or {}).get("agent_review_disabled"))
    failure_audit = pipeline.get("skill_review_failure_audit") if isinstance(pipeline.get("skill_review_failure_audit"), dict) else {}
    review_reason_counts = dict(failure_audit.get("reason_counts") or {})
    if not review_reason_counts and skill_input > reviewed_success and not agent_review_disabled:
        review_reason_counts = {"agent_review_failed": max(0, skill_input - reviewed_success)}
    if agent_review_disabled and skill_input:
        review_reason_counts = {"agent_review_disabled": skill_input}
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
            "reason_counts": merge_reason_counts(
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
            "reason_counts": review_reason_counts,
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


def fallback_filter_audit(summary: dict[str, Any], pipeline: dict[str, Any], recommended_count: int) -> dict[str, Any]:
    stage_summary = build_stage_summary(
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

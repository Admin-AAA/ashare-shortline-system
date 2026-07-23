"""推荐运行的过滤审计（filter-audit）深模块。

任务 7.3（需求 6.2）落地：把此前散落在 `workbench_service.run_workbench` 里的
pipeline audit / filter audit / trace 累积 / 分布统计等分布助手全部收口到本模块，
run_workbench 管线本身只需向 trace 累积器 append，末尾调 `build_filter_audit(...)`
拿最终 audit/分布，不留残件。

对外契约（供 `workbench_service.py` / `app/api/runs.py` 调用）：
- `build_pipeline_audit(...)`：构建单次运行的漏斗审计骨架（各阶段计数/限流标记）。
- `build_filter_audit(...)`：把 prefilter/skill_gate/score_line 三段审计拼成逐票
  trace + 全局分布 + stage_summary。
- `upgrade_filter_audit(...)` / `skill_review_failure_audit(_from_logs)`：供历史
  Run 详情页兼容旧格式审计数据、以及展示 Agent 深审失败归因。

跨股聚合的纯分布统计（score_distribution/quality_distribution/coverage_heatmap/
merge_reason_counts/build_stage_summary/fallback_filter_audit）已经在
`app.services.candidates.trace` 落地（更早的 S7 之前阶段），本模块直接复用，
不再重复实现或包一层 shallow wrapper。
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.engine.aggregator import resolve_display_limit, resolve_score_line_context
from app.models.tables import ApiCallLog
from app.services.candidates import trace as candidate_trace


# ═════════════════════════════════════════════════════════════════════════════
# Pipeline audit（单次运行的漏斗审计骨架）
# ═════════════════════════════════════════════════════════════════════════════


def build_pipeline_audit(
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


def strip_audit_rows(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_audit_rows(item)
            for key, item in value.items()
            if key != "rows"
        }
    if isinstance(value, list):
        return [strip_audit_rows(item) for item in value]
    return value


# ═════════════════════════════════════════════════════════════════════════════
# Skill Agent 深审失败归因
# ═════════════════════════════════════════════════════════════════════════════


def skill_review_failure_audit(
    candidate_results: list[dict[str, Any]],
    api_costs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize Agent review failures so degraded runs are explainable in the funnel."""
    errors: list[str] = []
    affected_codes: set[str] = set()
    affected_skills: set[str] = set()
    for entry in candidate_results:
        code = str((entry.get("stock") or {}).get("code") or "").strip()
        for review in entry.get("skill_reviews") or []:
            error = str(review.get("error") or "").strip()
            if not error:
                continue
            errors.append(error)
            skill_id = str(review.get("skill_id") or "").strip()
            if code:
                affected_codes.add(code)
            if skill_id:
                affected_skills.add(skill_id)

    api_failures = [
        cost for cost in (api_costs or [])
        if str(cost.get("status") or "success") != "success" or cost.get("error_message")
    ]
    for cost in api_failures:
        error = str(cost.get("error_message") or "").strip()
        if error:
            errors.append(error)

    reason_counts = categorical_distribution(skill_review_error_bucket(error) for error in errors)
    sample_errors = unique_head(errors, limit=3, max_chars=260)
    sample_errors_by_bucket = sample_errors_grouped(errors, per_bucket=2, max_chars=260)
    return {
        "failed_count": len(errors),
        "affected_stock_count": len(affected_codes),
        "affected_skill_count": len(affected_skills),
        "api_failure_count": len(api_failures),
        "reason_counts": reason_counts,
        "sample_errors": sample_errors,
        "sample_errors_by_bucket": sample_errors_by_bucket,
    }


def skill_review_failure_audit_from_logs(db: Session, run_id: str) -> dict[str, Any]:
    logs = db.scalars(
        select(ApiCallLog)
        .where(ApiCallLog.run_id == run_id)
        .where(ApiCallLog.status != "success")
        .order_by(ApiCallLog.created_at.desc())
        .limit(200)
    ).all()
    errors = [str(log.error_message or "").strip() for log in logs if str(log.error_message or "").strip()]
    reason_counts = categorical_distribution(skill_review_error_bucket(error) for error in errors)
    return {
        "failed_count": len(errors),
        "affected_stock_count": 0,
        "affected_skill_count": 0,
        "api_failure_count": len(logs),
        "reason_counts": reason_counts,
        "sample_errors": unique_head(errors, limit=3, max_chars=260),
        "sample_errors_by_bucket": sample_errors_grouped(errors, per_bucket=2, max_chars=260),
    }


def skill_review_error_bucket(error: Any) -> str:
    text = str(error or "").strip()
    lowered = text.lower()
    if "402" in text or "payment required" in lowered:
        return "model_payment_required"
    if "401" in text or "unauthorized" in lowered or "invalid api key" in lowered:
        return "model_auth_failed"
    if "429" in text or "rate limit" in lowered:
        return "model_rate_limited"
    # 上游 5xx（502/503/504/500）：此前无专属 bucket，被漏到 agent_review_error 兜底，
    # 导致 self_check 误判为「解析错误 / 查 prompt 格式」，而真实根因是模型 provider
    # 不稳定（upstream_unavailable / service temporarily unavailable）。识别后单列
    # model_upstream_5xx，避免把外部 5xx 误诊为 LLM 输出格式问题去改 prompt。
    if any(
        s in lowered
        for s in (
            "error code: 500", "error code: 502", "error code: 503", "error code: 504",
            " 500", " 502", " 503", " 504",
            "upstream_unavailable", "bad gateway",
            "service temporarily unavailable", "service unavailable",
        )
    ):
        return "model_upstream_5xx"
    # 网络层错误（Connection error / read timeout / remote protocol）：此前被漏到
    # agent_review_error 兜底，但根因是网络/provider 不可达，非 LLM 输出格式问题。
    # 实证 07-22：14 个 agent_review_error 中部分是 Connection error（grok-4.5 503 后
    # failover 到 kimi-k3 也 Connection error），误诊为「查 prompt 格式」。
    if any(s in lowered for s in ("connection error", "connection reset", "remoteprotocolerror", "read timeout")):
        return "model_network_error"
    if "timeout" in lowered or "超时" in text:
        return "model_timeout"
    if "熔断" in text or "circuit" in lowered:
        return "model_circuit_open"
    if "未配置" in text or "no route" in lowered or "route" in lowered:
        return "model_route_unavailable"
    if text:
        return "agent_review_error"
    return "agent_review_failed"


def unique_head(values: list[str], *, limit: int, max_chars: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        if len(text) > max_chars:
            text = text[: max(0, max_chars - 1)] + "…"
        result.append(text)
        if len(result) >= limit:
            break
    return result


def sample_errors_grouped(
    errors: list[str], *, per_bucket: int = 2, max_chars: int = 260
) -> dict[str, list[str]]:
    """按 error bucket 分组各取前 N 条去重样本。

    unique_head 全局取前 3 条，当 errors 前 3 条恰好是 timeout/5xx 时，agent_review_error
    的真实样本被淹没——self_check 报 agent_review_error=N 但 sample 全是别的 bucket，无法
    定位根因（07-22 实证：agent_review_error=14 但 sample_errors 全是 timeout/503）。
    按 bucket 分组确保每个非零 bucket 都有样本可见，让 agent_review_error 的真实错误文本
    （如 JSON 解析失败、数据引用错误）可被直接定位，而非被误归为「查 prompt 格式」。
    """
    by_bucket: dict[str, list[str]] = {}
    seen: set[str] = set()
    for value in errors:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        bucket = skill_review_error_bucket(text)
        lst = by_bucket.setdefault(bucket, [])
        if len(lst) < per_bucket:
            if len(text) > max_chars:
                text = text[: max(0, max_chars - 1)] + "…"
            lst.append(text)
    return by_bucket


# ═════════════════════════════════════════════════════════════════════════════
# 逐票 trace 累积（prefilter -> skill_gate -> score_line）
# ═════════════════════════════════════════════════════════════════════════════


def filter_stage_order(stage: str) -> int:
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


def append_trace_step(row: dict[str, Any], stage: str, passed: bool, reason: Any, **detail: Any) -> None:
    trace = row.setdefault("trace", [])
    step: dict[str, Any] = {
        "stage": stage,
        "label": filter_stage_label(stage),
        "passed": bool(passed),
        "reason": str(reason or ("通过" if passed else "未通过")),
    }
    for key, value in detail.items():
        if value is None or value == "":
            continue
        step[key] = value
    trace.append(step)


def filter_stage_label(stage: str) -> str:
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


def trace_summary(trace: list[dict[str, Any]]) -> str:
    if not trace:
        return ""
    fragments = []
    for item in trace:
        label = item.get("label") or filter_stage_label(str(item.get("stage") or ""))
        marker = "过" if item.get("passed") else "止"
        fragments.append(f"{label}:{marker}")
        if item.get("passed") is False:
            break
    return " -> ".join(fragments)


def score_line_reject_reason(
    *,
    final_score: float,
    effective_line: float | None,
    passed: bool,
    execution_blocked: bool = False,
    block_reason: str | None = None,
    hard_veto: bool = False,
    veto_reason: str | None = None,
    risk_flags: list | None = None,
) -> str:
    """纯函数：score_line 阶段拒绝/通过原因文案。

    背景（2026-07-22 T5）：骨架/hard_veto 票 final_score=0 且无 score_line_meta 时，
    旧逻辑默认 effective_line=0 → 刷出「最终分 0.0 低于有效线 0.0」噪声
    （07-21 formal 34/60），淹没真门线拒绝（如 72<95.55）。
    - 有 meta 的真拒绝：最终分 X 低于有效线 Y
    - hard_veto / 无 meta：单独文案，不进「低于有效线」正则
    """
    if execution_blocked:
        return str(block_reason or "入场不可成交，未纳入正式推荐")
    if passed:
        return "达到正式分数线"
    if hard_veto:
        reason = str(
            veto_reason
            or ((risk_flags or ["策略 hard_veto"])[0] if risk_flags else "策略 hard_veto")
        )
        if not reason.startswith("hard_veto"):
            return f"hard_veto: {reason}"
        return reason
    if effective_line is None:
        return "无有效线元数据（未完成分数线评估）"
    return f"最终分 {round(final_score, 2)} 低于有效线 {round(float(effective_line), 2)}"


def _data_quality_empty_reason(
    score_rows: list[dict[str, Any]],
    recommended_count: int,
) -> str | None:
    if recommended_count or not score_rows:
        return None
    evaluated = [
        row
        for row in score_rows
        if row.get("effective_score_line") is not None and row.get("quality_cap") is not None
    ]
    if not evaluated:
        return None
    blocked = [
        row
        for row in evaluated
        if float(row["quality_cap"]) < float(row["effective_score_line"])
        and (row.get("data_quality") or {}).get("missing_fields")
    ]
    if len(blocked) != len(evaluated):
        return None
    missing = Counter(
        str(field)
        for row in blocked
        for field in ((row.get("data_quality") or {}).get("missing_fields") or [])
    )
    top_missing = ", ".join(field for field, _count in missing.most_common(3)) or "unknown"
    return (
        f"data_quality_blocked: {len(blocked)}/{len(evaluated)} 个已评分候选的质量上限低于有效线；"
        f"主要缺失字段：{top_missing}"
    )


def build_filter_audit(
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
        append_trace_step(
            row,
            "prefilter_common",
            common_pass,
            item.get("common_reason") or ("公共可交易过滤通过" if common_pass else "公共可交易过滤未通过"),
            amount_yi=item.get("amount_yi"),
            turnover_rate=item.get("turnover_rate"),
            pct=item.get("pct"),
        )
        if common_pass:
            append_trace_step(
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
            append_trace_step(row, "prefilter_pass", True, "进入规则扫描")
        row["name"] = row.get("name") or item.get("name")
        row["industry"] = row.get("industry") or item.get("industry")
        row["rule_gate"] = item
        if not item.get("passed"):
            append_trace_step(
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
            append_trace_step(
                row,
                "skill_gate",
                True,
                item.get("reason") or "规则门通过",
                best_skill_id=item.get("best_skill_id"),
                best_gate_score=item.get("best_gate_score"),
                prelim_score=item.get("prelim_score"),
            )
            append_trace_step(
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
            append_trace_step(
                row,
                "skill_gate",
                True,
                item.get("reason") or "规则门通过，进入 Skill 深审",
                best_skill_id=item.get("best_skill_id"),
                best_gate_score=item.get("best_gate_score"),
                prelim_score=item.get("prelim_score"),
            )
            append_trace_step(
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
        # effective_line 缺 meta 时勿默认 0——否则 hard_veto/未聚合骨架票会刷出
        # 「最终分 0.0 低于有效线 0.0」噪声（07-21 formal 34/60 实证），淹没真门线拒绝。
        has_eff = score_line_meta.get("effective_score_line") is not None
        effective_line = float(score_line_meta["effective_score_line"]) if has_eff else None
        passed = code in recommended_codes or bool(score_line_meta.get("passed"))
        score_gap = (
            round(final_score - effective_line, 2)
            if effective_line is not None
            else None
        )
        score_reason = score_line_reject_reason(
            final_score=final_score,
            effective_line=effective_line,
            passed=passed,
            execution_blocked=bool(score_line_meta.get("execution_blocked")),
            block_reason=score_line_meta.get("block_reason"),
            hard_veto=bool(card.get("hard_veto")),
            veto_reason=card.get("veto_reason"),
            risk_flags=card.get("risk_flags") or card.get("risk_tags"),
        )
        score_row = {
            "code": code,
            "final_score": round(final_score, 2),
            "effective_score_line": round(effective_line, 2) if effective_line is not None else None,
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
            append_trace_step(row, "prefilter_pass", True, "进入聚合评分")
            append_trace_step(row, "skill_review", True, "进入聚合评分")
        row["name"] = row.get("name") or card.get("name")
        row["industry"] = row.get("industry") or card.get("industry")
        row["score_line"] = score_row
        append_trace_step(
            row,
            "score_line",
            passed,
            score_reason,
            final_score=round(final_score, 2),
            effective_score_line=round(effective_line, 2) if effective_line is not None else None,
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
        row["trace_summary"] = trace_summary(row.get("trace") or [])
    rows.sort(
        key=lambda row: (
            filter_stage_order(str(row.get("final_stage") or "")),
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

    distributions = build_filter_distributions(
        rows=rows,
        scored_cards=scored_cards,
        recommended_cards=recommended_cards,
        score_rows=score_rows,
    )

    stage_summary = candidate_trace.build_stage_summary(
        pipeline=pipeline,
        prefilter_audit=prefilter_audit or {},
        skill_gate_audit=skill_gate_audit or {},
        scored_count=len(scored_cards),
        recommended_count=len(recommended_cards),
        reason_counts=reason_counts,
    )
    audit = {
        "stage_summary": stage_summary,
        "distributions": distributions,
        "reason_counts": reason_counts,
        "rows": rows,
    }
    empty_reason = _data_quality_empty_reason(score_rows, len(recommended_cards))
    if empty_reason:
        audit["empty_reason"] = empty_reason
    return audit


# ═════════════════════════════════════════════════════════════════════════════
# 历史 Run 详情兼容：从旧格式审计升级出逐票 trace / 完整分布
# ═════════════════════════════════════════════════════════════════════════════


def upgrade_filter_audit(audit: dict[str, Any]) -> dict[str, Any]:
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
            row["trace"] = trace_from_audit_row(row)
        row["trace_summary"] = row.get("trace_summary") or trace_summary(row.get("trace") or [])

    distributions = audit.get("distributions") or {}
    if rows and not {"final_stage", "data_completeness_all", "score_line_adjustment_reason"}.issubset(set(distributions.keys())):
        scored_cards: list[dict[str, Any]] = []
        recommended_cards: list[dict[str, Any]] = []
        score_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("score_line"), dict):
                continue
            score_row = dict(row.get("score_line") or {})
            card = score_card_from_audit_row(row, score_row)
            score_rows.append(score_row)
            scored_cards.append(card)
            if row.get("final_status") == "recommended" or score_row.get("passed"):
                recommended_cards.append(card)
        upgraded_distributions = build_filter_distributions(
            rows=[row for row in rows if isinstance(row, dict)],
            scored_cards=scored_cards,
            recommended_cards=recommended_cards,
            score_rows=score_rows,
        )
        audit["distributions"] = {**distributions, **upgraded_distributions}
    return audit


def trace_from_audit_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    traced: dict[str, Any] = {"trace": []}
    prefilter = row.get("prefilter") if isinstance(row.get("prefilter"), dict) else {}
    rule_gate = row.get("rule_gate") if isinstance(row.get("rule_gate"), dict) else {}
    score_line = row.get("score_line") if isinstance(row.get("score_line"), dict) else {}

    if prefilter:
        common_pass = bool(prefilter.get("common_pass", row.get("final_stage") != "prefilter_common"))
        append_trace_step(
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
            append_trace_step(
                traced,
                "prefilter_pass" if prefilter_pass else "prefilter_skill",
                prefilter_pass,
                prefilter.get("reason") or ("技能预过滤命中" if prefilter_pass else row.get("final_reason")),
                skill_pass_ids=prefilter.get("skill_pass_ids") or [],
                prefilter_source=prefilter.get("prefilter_source"),
            )

    if rule_gate:
        gate_pass = bool(rule_gate.get("passed"))
        append_trace_step(
            traced,
            "skill_gate",
            gate_pass,
            rule_gate.get("reason") or ("规则门通过" if gate_pass else row.get("final_reason")),
            best_skill_id=rule_gate.get("best_skill_id"),
            best_gate_score=rule_gate.get("best_gate_score"),
            prelim_score=rule_gate.get("prelim_score"),
        )
        if gate_pass and rule_gate.get("review_selected") is False:
            append_trace_step(
                traced,
                "skill_review_limit",
                False,
                rule_gate.get("review_limit_reason") or row.get("final_reason") or "规则门通过，但未进入本次深审容量",
                review_skill_ids=rule_gate.get("review_skill_ids") or [],
            )
        elif gate_pass:
            append_trace_step(
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
        append_trace_step(
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
        append_trace_step(
            traced,
            str(row.get("final_stage") or "unknown"),
            row.get("final_status") != "filtered",
            row.get("final_reason") or "无路径明细",
        )
    return traced["trace"]


def score_card_from_audit_row(row: dict[str, Any], score_row: dict[str, Any]) -> dict[str, Any]:
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


def apply_skill_review_failure_audit_to_stage_summary(
    stage_summary: list[dict[str, Any]],
    failure_audit: dict[str, Any],
) -> None:
    reason_counts = failure_audit.get("reason_counts") or {}
    if not reason_counts:
        return
    for row in stage_summary:
        if isinstance(row, dict) and row.get("stage") == "skill_review":
            row["reason_counts"] = reason_counts
            return


# ═════════════════════════════════════════════════════════════════════════════
# 分布统计（跨股聚合）
# ═════════════════════════════════════════════════════════════════════════════


def build_filter_distributions(
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
        "final_stage": categorical_distribution([row.get("final_stage") for row in rows]),
        "final_status": categorical_distribution([row.get("final_status") for row in rows]),
        "industry": categorical_distribution([row.get("industry") for row in rows], limit=12),
        "prefilter_stage": categorical_distribution([(row.get("prefilter") or {}).get("stage") for row in rows if row.get("prefilter")]),
        "prefilter_source": categorical_distribution([(row.get("prefilter") or {}).get("prefilter_source") for row in rows if row.get("prefilter")]),
        "skill_gate_result": categorical_distribution(skill_gate_result(row) for row in rule_gate_rows),
        "skill_gate_reason": categorical_distribution(skill_gate_reason_bucket(row) for row in rule_gate_rows),
        "score_line_result": categorical_distribution(["通过" if row.get("passed") else "未通过" for row in score_rows]),
        "execution_status": categorical_distribution(
            ((row.get("execution_viability") or {}).get("status") or "unknown") for row in score_rows
        ),
        "score_line_adjustment_reason": list_distribution(
            (row.get("score_line_meta") or {}).get("reasons") or ["无附加证据惩罚"] for row in score_rows
        ),
        "setup_type": categorical_distribution([row.get("setup_type") or "未分类" for row in score_rows]),
        "market_regime": categorical_distribution([row.get("market_regime") for row in score_rows], limit=10),
        "prelim_score": numeric_distribution(
            numeric_values(rule_gate_rows, "prelim_score"),
            buckets=[0, 50, 60, 70, 80, 90],
        ),
        "best_gate_score": numeric_distribution(
            numeric_values(rule_gate_rows, "best_gate_score"),
            buckets=[0, 58, 70, 80, 90],
        ),
        "final_score_all": candidate_trace.score_distribution(scored_cards),
        "final_score_filtered": candidate_trace.score_distribution(filtered_scored_cards),
        "final_score_recommended": candidate_trace.score_distribution(recommended_cards),
        "effective_score_line": numeric_distribution(
            numeric_values(score_rows, "effective_score_line"),
            buckets=[0, 60, 70, 75, 80, 85, 90],
        ),
        "score_gap": score_gap_distribution(score_rows),
        "evidence_quality_all": candidate_trace.quality_distribution(scored_cards),
        "data_completeness_all": numeric_distribution(
            numeric_values(score_rows, "data_completeness_score"),
            buckets=[0, 50, 65, 75, 90, 100],
        ),
        "data_authenticity_all": numeric_distribution(
            numeric_values(score_rows, "data_authenticity"),
            buckets=[0, 0.5, 0.75, 0.9, 1.0],
        ),
        "quality_cap": numeric_distribution(
            numeric_values(score_rows, "quality_cap"),
            buckets=[0, 60, 70, 80, 90, 100],
        ),
        "consensus_boost": numeric_distribution(
            numeric_values(score_rows, "consensus_boost"),
            buckets=[0, 0.8, 0.95, 1.0, 1.05, 1.15],
        ),
        "score_line_adjustment": numeric_distribution(
            [float((row.get("score_line_meta") or {}).get("evidence_adjustment") or 0) for row in score_rows],
            buckets=[0, 2, 4, 6, 8, 10, 14],
        ),
        "execution_adjustment": numeric_distribution(
            [float((row.get("score_line_meta") or {}).get("execution_adjustment") or 0) for row in score_rows],
            buckets=[0, 2, 4, 6, 8, 10, 14],
        ),
        "risk_tag_count": count_distribution(len(row.get("risk_tags") or []) for row in score_rows),
        "info_tag_count": count_distribution(len(row.get("info_tags") or []) for row in score_rows),
        "risk_tags": list_distribution(row.get("risk_tags") or [] for row in score_rows),
        "info_tags": list_distribution(row.get("info_tags") or [] for row in score_rows),
        "missing_fields": list_distribution((row.get("data_quality") or {}).get("missing_fields") or [] for row in score_rows),
        "data_source_breakdown": source_breakdown_distribution(score_rows),
        "pct": numeric_distribution(numeric_values(rows, "pct"), buckets=[-20, -10, -5, 0, 3, 7, 10, 20]),
        "amount_yi": numeric_distribution(numeric_values(rows, "amount_yi"), buckets=[0, 1.5, 3, 5, 10, 20, 50]),
        "turnover_rate": numeric_distribution(numeric_values(rows, "turnover_rate"), buckets=[0, 1, 3, 6, 10, 12, 20]),
    }

    for component in ("skill_agent", "strategy_rule", "market", "risk", "evidence"):
        distributions[f"raw_{component}"] = component_distribution(
            score_rows,
            "raw_component_scores",
            component,
            buckets=[0, 20, 40, 60, 80, 100],
        )
        distributions[f"weighted_{component}"] = component_distribution(
            score_rows,
            "weighted_component_scores",
            component,
            buckets=[0, 5, 10, 20, 35, 50],
        )
    return {key: value for key, value in distributions.items() if value}


def skill_gate_result(row: dict[str, Any]) -> str:
    if not row.get("passed"):
        return "规则门未通过"
    if row.get("review_selected") is False:
        return "通过但容量截断"
    return "进入深审"


def skill_gate_reason_bucket(row: dict[str, Any]) -> str:
    if row.get("review_selected") is False:
        return "review_limit"
    return str(row.get("reason_key") or ("gate_pass" if row.get("passed") else "filtered"))


def categorical_distribution(values: Any, limit: int = 12) -> dict[str, int]:
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


def list_distribution(values: Any, limit: int = 12) -> dict[str, int]:
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


def count_distribution(values: Any) -> dict[str, int]:
    counter: Counter[str] = Counter(str(int(value or 0)) for value in values)
    return {key: int(counter[key]) for key in sorted(counter, key=lambda item: int(item))}


def numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
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


def component_distribution(
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
    return numeric_distribution(values, buckets) if values else {}


def source_breakdown_distribution(rows: list[dict[str, Any]], limit: int = 12) -> dict[str, int]:
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


def numeric_distribution(values: list[float], buckets: list[float]) -> dict[str, int]:
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


def score_gap_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
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

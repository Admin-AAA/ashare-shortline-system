from __future__ import annotations

import uuid
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.tables import AgentDecisionLog, Recommendation


ProgressCallback = Callable[[int, int], None]


def best_strategy_id(strategy_signals: list[dict[str, Any]]) -> str:
    """从 strategy_signals 取得分最高的 strategy_id 作为 bucket。

    用于按策略归因胜率：每笔推荐记录贡献最高分的策略，便于后续
    SELECT bucket, COUNT(*), SUM(success) FROM recommendations GROUP BY bucket。
    hard_veto 的 signal 不参与（score 可能虚高但已否决）。
    """
    if not strategy_signals:
        return ""
    candidates = [s for s in strategy_signals if not s.get("hard_veto")]
    pool = candidates or strategy_signals
    best = max(
        pool,
        key=lambda s: float(s.get("score") or 0),
    )
    return str(best.get("strategy_id") or "")


def _build_agent_summary(card: dict[str, Any]) -> dict[str, Any]:
    """构建 agent_summary_json：保留审计 logs，并把首个 LLM 审查的关键决策字段
    提取到顶层（stance/delta/score/summary/action），供列表视图与 B 项自检直接读取。

    之前只存 mode/logs/data_source，LLM 判断埋在 logs[0].output 里，前端列表与
    北极星 B 项自检（$.delta）都拿不到——决策链断层。此处补齐。

    第 N+19 轮 T7：LLM 失败走 fallback_prelim 时 agent_logs 为空，但 strategy_signals
    里的 agent_skill_review 有降级 stance/score_delta。logs 为空时从 fallback review
    提取顶层字段，避免前端 stance=None/delta=None（chip-single-peak 命中但 LLM 余额不足
    时触发，用户看不到降级状态）。
    """
    logs = card.get("agent_logs", []) or []
    summary: dict[str, Any] = {
        "mode": "auditable_skill_agents",
        "logs": logs,
        "data_source": (card.get("market_snapshot", {}).get("breadth") or {}).get("data_source"),
    }
    if logs:
        first_output = (logs[0].get("output") or {}) if isinstance(logs[0], dict) else {}
        if isinstance(first_output, dict) and first_output:
            # 顶层快捷字段（与 RecommendationDetailCard 的 AgentReviewSection 对齐）
            for key in ("stance", "score_delta", "score", "summary", "action",
                        "position_range", "entry_style", "risks"):
                if key in first_output:
                    summary[key] = first_output[key]
            # supports：LLM review 同时存 evidence 与 supports（review_runner 已把
            # evidence 映射为 supports），优先 supports 兜底 evidence，供前端列表/详情
            # 顶层直接读「为什么支持」，不必翻 logs[0].output.evidence（B 项决策链断层修复）。
            supports = first_output.get("supports")
            if supports is None:
                supports = first_output.get("evidence")
            if supports is not None:
                summary["supports"] = supports
        return summary

    # logs 为空：LLM 失败走 fallback_prelim，从 strategy_signals 的 agent_skill_review 提取降级字段
    signals = card.get("strategy_signals") or []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        review = signal.get("agent_skill_review")
        if not isinstance(review, dict) or not review:
            continue
        if str(review.get("runtime") or "") == "agent_skill_fallback_prelim":
            for key in ("stance", "score_delta", "score", "summary", "action",
                        "position_range", "entry_style", "risks", "supports"):
                if key in review:
                    summary[key] = review[key]
            break
    return summary


def _slim_market_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Strip run-level audit blobs from a market snapshot before per-recommendation storage.

    The snapshot's ``breadth`` contains heavy audit fields
    (``auxiliary_data_audit``, ``auxiliary_orchestration``,
    ``recommendation_pipeline``) that are identical across all
    recommendations in the same run.  Storing a full copy per row wastes
    ~200 KB × N recommendations and can trigger MySQL ``Data too long``
    when the audit payload grows unexpectedly.  The full snapshot is
    already persisted once in ``recommendation_runs.market_snapshot_json``,
    so per-row copies only need the lightweight regime/risk/counts.
    """
    breadth = snapshot.get("breadth")
    if not isinstance(breadth, dict):
        return snapshot
    slim_breadth = {k: v for k, v in breadth.items() if k not in (
        "auxiliary_data_audit",
        "auxiliary_orchestration",
        "recommendation_pipeline",
        "prefilter_audit",
        "market_source_audit",
        "factor_execution_plan",
    )}
    # Keep a size hint so downstream code knows data was trimmed.
    for key in ("auxiliary_data_audit", "auxiliary_orchestration", "recommendation_pipeline"):
        original = breadth.get(key)
        if isinstance(original, dict):
            slim_breadth[f"{key}_summary"] = {
                "datasets": list(original.keys()),
                "trimmed": True,
            }
        elif isinstance(original, list):
            slim_breadth[f"{key}_summary"] = {"count": len(original), "trimmed": True}
    slim = dict(snapshot)
    slim["breadth"] = slim_breadth
    return slim


def persist_recommendation_cards(
    db: Session,
    *,
    run_id: str,
    trade_date: str,
    cards: list[dict[str, Any]],
    dump_json: Callable[[Any], str],
    on_progress: ProgressCallback | None = None,
) -> list[Recommendation]:
    saved: list[Recommendation] = []
    total = len(cards)
    for index, card in enumerate(cards, start=1):
        recommendation_id = f"rec_{trade_date.replace('-', '')}_{index:03d}_{uuid.uuid4().hex[:4]}"
        rec = Recommendation(
            recommendation_id=recommendation_id,
            run_id=run_id,
            trade_date=trade_date,
            code=card["code"],
            name=card["name"],
            industry=card.get("industry"),
            recommend_price=float(card.get("recommend_price") or 0),
            bucket=best_strategy_id(card.get("strategy_signals") or []),
            final_score=float(card["final_score"]),
            consensus_score=float(card.get("consensus_score") or 0),
            setup_type=card.get("setup_type"),
            entry_rule=card.get("entry_rule"),
            invalid_rule=card.get("invalid_rule"),
            rationale=card.get("rationale"),
            risk_tags_json=dump_json(card.get("risk_flags", [])),
            info_tags_json=dump_json(card.get("info_tags", [])),
            recent_repeat=bool(card.get("recent_repeat")),
            feature_snapshot_json=dump_json(
                {
                    **(card.get("feature_snapshot", {}) or {}),
                    "recommend_price": card.get("recommend_price"),
                    "stop_loss": card.get("stop_loss"),
                    "take_profit": card.get("take_profit"),
                    "market_discipline": card.get("market_discipline", {}),
                    "aggregation_weights": card.get("aggregation_weights", {}),
                    "raw_component_scores": card.get("raw_component_scores", {}),
                    "weighted_component_scores": card.get("weighted_component_scores", {}),
                    "score_breakdown": card.get("score_breakdown", {}),
                    "score_line_meta": card.get("score_line_meta", {}),
                    "execution_viability": card.get("execution_viability", {}),
                    "sector_diversity_penalty": card.get("sector_diversity_penalty", 0),
                    "component_scale": card.get("component_scale", 1.0),
                    "consensus_boost": card.get("consensus_boost", 1.0),
                }
            ),
            market_context_json=dump_json(_slim_market_snapshot(card.get("market_snapshot") or {})),
            skill_breakdown_json=dump_json(card.get("strategy_signals", [])),
            agent_summary_json=dump_json(_build_agent_summary(card)),
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
                    output_json=dump_json(log["output"]),
                )
            )
        saved.append(rec)
        if on_progress is not None:
            on_progress(index, total)
    return saved

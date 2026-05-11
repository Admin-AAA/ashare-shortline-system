from __future__ import annotations

from typing import Any


def review_stock(
    stock: dict[str, Any],
    signals: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """
    旧接口兼容层 —— 内部委托给 Agno Skill Agent 审查。

    注意: 新代码应直接使用 review_runner.run_skill_reviews()，
    该函数拥有完整的 db session 和 skills 上下文。

    兼容策略:
    1. 如果 stock 中已有 'skill_reviews'（由 review_runner 预计算），
       直接使用其结果。
    2. 否则，基于 signals 中的 strategy.yaml 评分构建降级版本。
    3. 如果 signals 也为空，返回中性默认评分。
    """
    # ── 优先路径: 使用 review_runner 预计算的 Agent 审查结果 ──
    skill_reviews: list[dict[str, Any]] = stock.get("skill_reviews", [])

    if skill_reviews:
        logs: list[dict[str, Any]] = []
        valid_scores: list[float] = []

        for r in skill_reviews:
            review = r.get("review")
            if review and not r.get("error"):
                logs.append({
                    "agent_name": f"Skill Agent · {r.get('skill_name', 'Unknown')}",
                    "output": {
                        "stance": review.get("stance"),
                        "score": review.get("score"),
                        "confidence": review.get("confidence"),
                        "summary": review.get("summary", ""),
                        "market_regime": review.get("market_regime_judgment", ""),
                    },
                })
                valid_scores.append(float(review.get("score", 50)))
            else:
                logs.append({
                    "agent_name": f"Skill Agent · {r.get('skill_name', 'Unknown')}",
                    "output": {"error": r.get("error", "Unknown error")},
                })

        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 50.0
        return {
            "agent_review_score": round(avg_score, 2),
            "logs": logs,
        }

    # ── 降级路径: 基于 signals 中的 strategy.yaml 评分 ──
    if signals:
        scores: list[float] = []
        logs = []

        for sig in signals:
            skill_id = sig.get("skill_id", "unknown")
            score = float(sig.get("score", 50))
            runtime = sig.get("runtime", "strategy_yaml")

            logs.append({
                "agent_name": f"Rule Signal · {skill_id}",
                "output": {
                    "stance": _score_to_stance(score),
                    "score": score,
                    "confidence": 0.5,
                    "summary": (
                        f"基于 strategy.yaml 评分的降级结果 "
                        f"(runtime={runtime})"
                    ),
                    "market_regime": snapshot.get("breadth", {}).get("regime", ""),
                    "evidence": sig.get("evidence", []),
                    "risks": sig.get("risk_flags", []),
                },
            })
            scores.append(score)

        avg_score = sum(scores) / len(scores) if scores else 50.0
        return {
            "agent_review_score": round(avg_score, 2),
            "logs": logs,
        }

    # ── 兜底: 完全无数据，返回中性评分 ──
    return {
        "agent_review_score": 50.0,
        "logs": [{
            "agent_name": "System",
            "output": {
                "message": "Agent 审查未在此次运行中执行，使用默认评分",
            },
        }],
    }


def _score_to_stance(score: float) -> str:
    """将 strategy.yaml 评分映射为 stance 字符串。"""
    if score >= 82:
        return "strong_support"
    elif score >= 70:
        return "support"
    elif score >= 58:
        return "neutral_support"
    elif score >= 45:
        return "neutral"
    elif score >= 30:
        return "oppose"
    else:
        return "veto"

from __future__ import annotations

import uuid
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.tables import AgentDecisionLog, Recommendation


ProgressCallback = Callable[[int, int], None]


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
            bucket="",
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
            market_context_json=dump_json(card.get("market_snapshot", {})),
            skill_breakdown_json=dump_json(card.get("strategy_signals", [])),
            agent_summary_json=dump_json(
                {
                    "mode": "auditable_skill_agents",
                    "logs": card.get("agent_logs", []),
                    "data_source": (card.get("market_snapshot", {}).get("breadth") or {}).get("data_source"),
                }
            ),
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

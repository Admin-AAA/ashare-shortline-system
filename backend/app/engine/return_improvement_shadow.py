from __future__ import annotations

from typing import Any


SHADOW_SCHEMA_VERSION = "return-improvement-shadow:v1"
_RANK_POSITION_CAP_PCT = {1: 50.0, 2: 25.0, 3: 25.0}
_OBSERVE_ONLY_SETUP = "一线游资净买接力"
_RELEASE_GATE = {
    "min_executed_trades": 100,
    "min_signal_days": 20,
    "requires_frozen_oos": True,
    "requires_forward_canary": True,
}


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def annotate_return_improvement_shadow(
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach rank/setup/auction hypotheses without changing live selection or sizing."""

    for rank, card in enumerate(cards, start=1):
        feature_snapshot = card.get("feature_snapshot")
        if not isinstance(feature_snapshot, dict):
            feature_snapshot = {}
            card["feature_snapshot"] = feature_snapshot

        auction_available = bool(
            feature_snapshot.get(
                "auction_signal_available",
                card.get("auction_signal_available", False),
            )
        )
        auction_gap_pct = _finite_number(
            feature_snapshot.get("auction_gap_pct", card.get("auction_gap_pct"))
        )
        if not auction_available:
            auction_gap_pct = None

        setup_type = str(card.get("setup_type") or "").strip()
        market_regime = str(
            (card.get("market_discipline") or {}).get("market_regime")
            or card.get("market_regime")
            or ""
        )
        weak_active = market_regime == "弱势·活跃"
        code = str(card.get("code") or "")
        is_twenty_cm = code.startswith(("300", "301", "688", "689"))
        payload = {
            "schema_version": SHADOW_SCHEMA_VERSION,
            "mode": "shadow",
            "production_effect_applied": False,
            "rank_hypothesis": {
                "rank": rank,
                "target_position_cap_pct": _RANK_POSITION_CAP_PCT.get(rank, 0.0),
            },
            "setup_hypothesis": {
                "setup_type": setup_type,
                "observe_only_candidate": setup_type == _OBSERVE_ONLY_SETUP,
            },
            "auction_dimension": {
                "available": auction_available,
                "gap_pct": auction_gap_pct,
            },
            "weak_active_risk_budget": {
                "applies": weak_active,
                "target_daily_output_limit": 2 if weak_active else None,
                "target_position_cap_pct": (
                    15.0
                    if weak_active and is_twenty_cm
                    else 20.0
                    if weak_active
                    else None
                ),
            },
            "release_gate": dict(_RELEASE_GATE),
            "release_ready": False,
            "release_blocker": "frozen_oos_and_forward_canary_required",
        }
        card["return_improvement_shadow"] = payload
        feature_snapshot["return_improvement_shadow"] = payload
    return cards

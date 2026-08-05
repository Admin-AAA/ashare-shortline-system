"""Audit-only PortfolioRisk boundary for recommendation persistence.

The boundary decorates cards before the existing RecommendationPersistencePort is
called.  It owns neither a repository nor a transaction and cannot enforce, remove,
reorder, or resize production recommendations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping, Sequence

from app.engine.portfolio_risk import (
    PortfolioRiskDecision,
    PortfolioRiskPolicy,
    PortfolioRiskState,
    RiskChannel,
    attach_would_decisions,
)


class PortfolioRiskWouldDecisionAudit:
    """Stateful audit calculator intended for injection at the existing boundary.

    The production SQL composition root must construct this from a versioned policy
    and CAS-loaded state in the recommendation Session transaction.  Until 0007 is
    authorized/applied, leaving the optional port unbound keeps enforcement and
    production selection unchanged.
    """

    def __init__(self, policy: PortfolioRiskPolicy, state: PortfolioRiskState) -> None:
        if state.policy_fingerprint != policy.policy_fingerprint:
            raise ValueError("risk audit state/policy mismatch")
        self._policy = policy
        self._state = state
        self._lock = RLock()
        self._decisions: tuple[PortfolioRiskDecision, ...] = ()

    @property
    def decisions(self) -> tuple[PortfolioRiskDecision, ...]:
        return self._decisions

    @property
    def state(self) -> PortfolioRiskState:
        return self._state

    @staticmethod
    def _requested_size(card: Mapping[str, Any]) -> float:
        for key in ("position_size", "position_fraction", "recommended_position"):
            value = card.get(key)
            if value is not None:
                return float(value)
        viability = card.get("execution_viability")
        if isinstance(viability, Mapping):
            for key in ("position_size", "position_fraction", "recommended_position"):
                value = viability.get(key)
                if value is not None:
                    return float(value)
        # No implicit risk budget is invented.  Missing sizing is a zero-size
        # would-decision, while the recommendation itself remains untouched.
        return 0.0

    def audit(
        self,
        *,
        run_id: str,
        trade_date: str,
        cards: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        del trade_date  # run/card identity is sufficient; no clock inference.
        with self._lock:
            audited, decisions, next_state = attach_would_decisions(
                self._policy,
                self._state,
                run_id=run_id,
                cards=cards,
                requested_size=self._requested_size,
                decided_at=datetime.now(timezone.utc),
                channel=RiskChannel.RECOMMENDATION,
            )
            self._state = next_state
            self._decisions = (*self._decisions, *decisions)
            return audited


__all__ = ["PortfolioRiskWouldDecisionAudit"]

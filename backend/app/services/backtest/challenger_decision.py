"""Single production-decision policy for self-improve challengers.

All research scripts must use this module instead of embedding their own
sample-size, confidence-interval, shadow, or release thresholds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isclose


PERCENTAGE_POINT_TOLERANCE = 0.01
PROFIT_FACTOR_TOLERANCE = 0.01

# Trade-off policy: a return metric may drop (within RETURN_TRADE_OFF_CAP_PP)
# when a substantial win-rate or PF gain compensates.  This lets candidates
# that trade per-trade return for a much higher win rate through, instead of
# demanding every return metric improve unconditionally.
WIN_RATE_QUALIFYING_GAIN_PP = 3.0
PF_QUALIFYING_GAIN = 0.1
RETURN_TRADE_OFF_CAP_PP = 1.0


def _exceeds(delta: float, tolerance: float) -> bool:
    return delta > tolerance and not isclose(delta, tolerance, abs_tol=1e-12)


def _regresses(delta: float, tolerance: float) -> bool:
    return delta < -tolerance and not isclose(delta, -tolerance, abs_tol=1e-12)


@dataclass(frozen=True)
class PerformanceSnapshot:
    """Metrics calculated on the same inputs, costs, and capital timeline."""

    average_net_return_pct: float
    cumulative_net_return_pct: float
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float


@dataclass(frozen=True)
class ChallengerDecision:
    should_publish: bool
    decision: str
    improvements: tuple[str, ...]
    regressions: tuple[str, ...]
    deltas: dict[str, float]

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_challenger(
    champion: PerformanceSnapshot,
    challenger: PerformanceSnapshot,
) -> ChallengerDecision:
    """Apply the project's holistic-improvement production policy.

    Production requires at least one of the two return measures to improve,
    at most one auxiliary metric (win-rate / PF / max drawdown) to regress,
    and at least one auxiliary to improve.  A regressing return metric is
    acceptable only when it stays within RETURN_TRADE_OFF_CAP_PP AND a
    substantial win-rate (>= WIN_RATE_QUALIFYING_GAIN_PP) or PF
    (>= PF_QUALIFYING_GAIN) gain compensates.

    Tolerances only remove floating-point noise; they are not evidence gates.
    A less-negative max drawdown is an improvement.
    """

    deltas = {
        "average_net_return_pct": (
            challenger.average_net_return_pct - champion.average_net_return_pct
        ),
        "cumulative_net_return_pct": (
            challenger.cumulative_net_return_pct - champion.cumulative_net_return_pct
        ),
        "win_rate_pct": challenger.win_rate_pct - champion.win_rate_pct,
        "profit_factor": challenger.profit_factor - champion.profit_factor,
        "max_drawdown_pct": challenger.max_drawdown_pct - champion.max_drawdown_pct,
    }

    tolerances = {
        "average_net_return_pct": PERCENTAGE_POINT_TOLERANCE,
        "cumulative_net_return_pct": PERCENTAGE_POINT_TOLERANCE,
        "win_rate_pct": PERCENTAGE_POINT_TOLERANCE,
        "profit_factor": PROFIT_FACTOR_TOLERANCE,
        "max_drawdown_pct": PERCENTAGE_POINT_TOLERANCE,
    }
    improvements = tuple(
        metric
        for metric, delta in deltas.items()
        if _exceeds(delta, tolerances[metric])
    )

    return_metrics = ("average_net_return_pct", "cumulative_net_return_pct")
    auxiliary_metrics = ("win_rate_pct", "profit_factor", "max_drawdown_pct")

    # At least one return metric must actually improve.
    returns_improve = any(
        _exceeds(deltas[m], tolerances[m]) for m in return_metrics
    )

    # A regressing return metric is allowed only when compensated by a
    # substantial win-rate/PF gain and bounded by the trade-off cap.
    compensating_gain = (
        _exceeds(deltas["win_rate_pct"], WIN_RATE_QUALIFYING_GAIN_PP)
        or _exceeds(deltas["profit_factor"], PF_QUALIFYING_GAIN)
    )
    returns_regressions_bounded = all(
        not _regresses(deltas[m], tolerances[m])
        or (compensating_gain and deltas[m] >= -RETURN_TRADE_OFF_CAP_PP)
        for m in return_metrics
    )

    regressions = tuple(
        metric
        for metric in auxiliary_metrics
        if _regresses(deltas[metric], tolerances[metric])
    )
    auxiliary_improves = any(metric in improvements for metric in auxiliary_metrics)
    # Relaxed from "none may regress" to "at most one auxiliary may regress".
    auxiliary_regressions_bounded = len(regressions) <= 1

    should_publish = (
        returns_improve
        and returns_regressions_bounded
        and auxiliary_improves
        and auxiliary_regressions_bounded
    )

    return ChallengerDecision(
        should_publish=should_publish,
        decision="PRODUCTION" if should_publish else "FALSIFIED",
        improvements=improvements,
        regressions=regressions,
        deltas={metric: round(delta, 10) for metric, delta in deltas.items()},
    )

"""Single production-decision policy for self-improve challengers.

All research scripts must use this module instead of embedding their own
sample-size, confidence-interval, shadow, or release thresholds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isclose


PERCENTAGE_POINT_TOLERANCE = 0.01
PROFIT_FACTOR_TOLERANCE = 0.01


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

    Production requires both return measures to improve, at least one of
    win-rate/PF/drawdown to improve, and none of those three to regress.
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

    auxiliary_metrics = ("win_rate_pct", "profit_factor", "max_drawdown_pct")
    regressions = tuple(
        metric
        for metric in auxiliary_metrics
        if _regresses(deltas[metric], tolerances[metric])
    )
    returns_improve = all(
        _exceeds(deltas[metric], tolerances[metric])
        for metric in ("average_net_return_pct", "cumulative_net_return_pct")
    )
    auxiliary_improves = any(metric in improvements for metric in auxiliary_metrics)
    should_publish = returns_improve and auxiliary_improves and not regressions

    return ChallengerDecision(
        should_publish=should_publish,
        decision="PRODUCTION" if should_publish else "FALSIFIED",
        improvements=improvements,
        regressions=regressions,
        deltas={metric: round(delta, 10) for metric, delta in deltas.items()},
    )

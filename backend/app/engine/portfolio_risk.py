"""Pure, versioned portfolio-risk domain and atomic reservation model.

This module is deliberately offline: it imports no ORM, database session, HTTP,
brokerage, credential, account, position or order adapter.  Recommendation,
simulation, paper and shadow callers pass immutable facts in and receive immutable
facts out.  Persistence adapters may commit the returned facts at the existing
recommendation transaction boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import math
from threading import RLock
from typing import Any, Callable, Mapping, Sequence

from app.engine.canonical import CanonicalValue, canonical_fingerprint
from app.engine.execution_core import compose_position_caps


class PortfolioRiskError(ValueError):
    """Base error for invalid or unsafe portfolio-risk operations."""


class StaleRiskStateError(PortfolioRiskError):
    """The command lost its state-version/hash compare-and-set race."""


class RiskTransactionError(RuntimeError):
    """An atomic model transaction failed and was fully rolled back."""


class RiskDecisionKind(str, Enum):
    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    REJECT = "REJECT"
    HALT = "HALT"


class RiskHaltStatus(str, Enum):
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"


class RiskChannel(str, Enum):
    RECOMMENDATION = "recommendation"
    SIMULATION = "simulation"
    PAPER = "paper"
    SHADOW = "shadow"


class QuoteStatus(str, Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    MISSING = "MISSING"


def _utc(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PortfolioRiskError(f"{name} must be a timezone-aware datetime")


def _fraction(value: float, name: str, *, upper: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PortfolioRiskError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= upper:
        raise PortfolioRiskError(f"{name} must be in [0, {upper}]")
    return result


def _positive(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PortfolioRiskError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise PortfolioRiskError(f"{name} must be positive and finite")
    return result


@dataclass(frozen=True, slots=True)
class PortfolioRiskPolicy(CanonicalValue):
    """An immutable policy artifact; any rule change creates a new fingerprint."""

    policy_id: str
    policy_version: str
    schema_version: int = 1
    single_name_cap: float = 0.10
    gross_exposure_cap: float = 0.80
    net_exposure_cap: float = 0.80
    industry_cap: float = 0.30
    theme_cap: float = 0.25
    daily_loss_reduce_at: float = 0.02
    daily_loss_halt_at: float = 0.04
    drawdown_reduce_at: float = 0.06
    drawdown_halt_at: float = 0.10
    resume_daily_loss_below: float = 0.01
    resume_drawdown_below: float = 0.04
    reduce_multiplier: float = 0.50
    stale_quote_multiplier: float = 1.10
    missing_quote_multiplier: float = 1.25

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.policy_version.strip():
            raise PortfolioRiskError("policy_id and policy_version are required")
        if self.schema_version < 1:
            raise PortfolioRiskError("schema_version must be positive")
        for name in (
            "single_name_cap", "gross_exposure_cap", "net_exposure_cap",
            "industry_cap", "theme_cap", "daily_loss_reduce_at",
            "daily_loss_halt_at", "drawdown_reduce_at", "drawdown_halt_at",
            "resume_daily_loss_below", "resume_drawdown_below", "reduce_multiplier",
        ):
            _fraction(getattr(self, name), name)
        for name in ("stale_quote_multiplier", "missing_quote_multiplier"):
            _positive(getattr(self, name), name)
        if self.daily_loss_reduce_at > self.daily_loss_halt_at:
            raise PortfolioRiskError("daily loss reduce threshold must not exceed halt")
        if self.drawdown_reduce_at > self.drawdown_halt_at:
            raise PortfolioRiskError("drawdown reduce threshold must not exceed halt")
        if self.resume_daily_loss_below >= self.daily_loss_reduce_at:
            raise PortfolioRiskError("daily loss resume threshold must be below reduce")
        if self.resume_drawdown_below >= self.drawdown_reduce_at:
            raise PortfolioRiskError("drawdown resume threshold must be below reduce")

    @property
    def policy_fingerprint(self) -> str:
        return self.fingerprint()

    def quote_multiplier(self, status: QuoteStatus) -> float:
        if status is QuoteStatus.STALE:
            return self.stale_quote_multiplier
        if status is QuoteStatus.MISSING:
            return self.missing_quote_multiplier
        return 1.0


@dataclass(frozen=True, slots=True)
class PositionExposure(CanonicalValue):
    """Signed capital fraction. Locked/stale/missing lots remain in all budgets."""

    symbol: str
    exposure: float
    industry: str = ""
    themes: tuple[str, ...] = ()
    quote_status: QuoteStatus = QuoteStatus.CURRENT
    locked: bool = False

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise PortfolioRiskError("exposure symbol is required")
        if isinstance(self.exposure, bool) or not isinstance(self.exposure, (int, float)):
            raise PortfolioRiskError("exposure must be numeric")
        if not math.isfinite(float(self.exposure)) or abs(float(self.exposure)) > 1:
            raise PortfolioRiskError("absolute exposure must be in [0, 1]")
        if len(set(self.themes)) != len(self.themes):
            raise PortfolioRiskError("exposure themes must be unique")


@dataclass(frozen=True, slots=True)
class ExposureReservation(CanonicalValue):
    reservation_id: str
    reservation_key: str
    decision_id: str
    symbol: str
    approved_size: float
    direction: int
    industry: str
    themes: tuple[str, ...]
    status: str
    lease_token: str
    created_at: datetime
    version: int

    def __post_init__(self) -> None:
        if not self.reservation_id or not self.reservation_key or not self.decision_id:
            raise PortfolioRiskError("reservation identity is required")
        _fraction(self.approved_size, "approved_size")
        if self.direction not in {-1, 1}:
            raise PortfolioRiskError("reservation direction must be -1 or 1")
        _utc(self.created_at, "created_at")
        if self.version < 1:
            raise PortfolioRiskError("reservation version must be positive")

    @property
    def signed_exposure(self) -> float:
        return self.approved_size * self.direction


@dataclass(frozen=True, slots=True)
class PortfolioRiskState(CanonicalValue):
    """One append-only state version; predecessors are identified by hash."""

    state_id: str
    state_scope: str
    policy_fingerprint: str
    as_of: datetime
    capital: float
    cash: float
    open_exposures: tuple[PositionExposure, ...] = ()
    pending_reservations: tuple[ExposureReservation, ...] = ()
    realized_pnl_today_pct: float = 0.0
    unrealized_pnl_today_pct: float = 0.0
    equity_peak: float = 1.0
    current_equity: float = 1.0
    halt_status: RiskHaltStatus = RiskHaltStatus.ACTIVE
    version: int = 1
    predecessor_state_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.state_id.strip() or not self.state_scope.strip():
            raise PortfolioRiskError("state_id and state_scope are required")
        if len(self.policy_fingerprint) != 64:
            raise PortfolioRiskError("policy_fingerprint must be a sha256 digest")
        _utc(self.as_of, "as_of")
        _positive(self.capital, "capital")
        if not math.isfinite(float(self.cash)):
            raise PortfolioRiskError("cash must be finite")
        _positive(self.equity_peak, "equity_peak")
        if not math.isfinite(float(self.current_equity)) or self.current_equity < 0:
            raise PortfolioRiskError("current_equity must be finite and non-negative")
        if self.version < 1:
            raise PortfolioRiskError("state version must be positive")
        if self.predecessor_state_hash is not None and len(self.predecessor_state_hash) != 64:
            raise PortfolioRiskError("predecessor_state_hash must be sha256 when present")
        for name in ("realized_pnl_today_pct", "unrealized_pnl_today_pct"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise PortfolioRiskError(f"{name} must be finite")

    @property
    def state_hash(self) -> str:
        return self.fingerprint()

    @property
    def daily_loss_pct(self) -> float:
        return max(0.0, -(self.realized_pnl_today_pct + self.unrealized_pnl_today_pct))

    @property
    def drawdown_pct(self) -> float:
        return max(0.0, (self.equity_peak - self.current_equity) / self.equity_peak)


@dataclass(frozen=True, slots=True)
class RiskGateCommand(CanonicalValue):
    command_id: str
    state_scope: str
    expected_state_version: int
    expected_state_hash: str
    run_id: str
    recommendation_id: str
    symbol: str
    requested_size: float
    industry: str = ""
    themes: tuple[str, ...] = ()
    direction: int = 1
    applicable_execution_caps: tuple[tuple[str, float | None], ...] = ()
    channel: RiskChannel = RiskChannel.RECOMMENDATION
    audit_only: bool = True
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not all((self.command_id, self.state_scope, self.run_id, self.recommendation_id, self.symbol)):
            raise PortfolioRiskError("risk command identities are required")
        if self.expected_state_version < 1 or len(self.expected_state_hash) != 64:
            raise PortfolioRiskError("risk command requires expected state version and hash")
        _fraction(self.requested_size, "requested_size")
        if self.direction not in {-1, 1}:
            raise PortfolioRiskError("direction must be -1 or 1")
        if len({name for name, _ in self.applicable_execution_caps}) != len(self.applicable_execution_caps):
            raise PortfolioRiskError("execution cap names must be unique")
        if len(set(self.themes)) != len(self.themes):
            raise PortfolioRiskError("themes must be unique")
        _utc(self.decided_at, "decided_at")


@dataclass(frozen=True, slots=True)
class PortfolioRiskDecision(CanonicalValue):
    decision_id: str
    command_id: str
    state_id: str
    state_version: int
    state_hash: str
    policy_fingerprint: str
    run_id: str
    recommendation_id: str
    decision: RiskDecisionKind
    requested_size: float
    approved_size: float
    applicable_caps: tuple[tuple[str, float], ...]
    binding_caps: tuple[str, ...]
    reason_codes: tuple[str, ...]
    decided_at: datetime
    audit_only: bool


@dataclass(frozen=True, slots=True)
class RiskGateTransition(CanonicalValue):
    decision: PortfolioRiskDecision
    reservation: ExposureReservation | None
    next_state: PortfolioRiskState


def _valued_open_exposures(
    state: PortfolioRiskState, policy: PortfolioRiskPolicy
) -> tuple[tuple[PositionExposure, float], ...]:
    return tuple(
        (lot, float(lot.exposure) * policy.quote_multiplier(lot.quote_status))
        for lot in state.open_exposures
    )


def _pending_signed(state: PortfolioRiskState) -> tuple[tuple[ExposureReservation, float], ...]:
    return tuple(
        (reservation, reservation.signed_exposure)
        for reservation in state.pending_reservations
        if reservation.status == "RESERVED"
    )


def exposure_metrics(
    state: PortfolioRiskState, policy: PortfolioRiskPolicy
) -> dict[str, Any]:
    """Compute conservative exposure metrics, retaining locked and bad-quote lots."""
    open_values = _valued_open_exposures(state, policy)
    pending_values = _pending_signed(state)
    signed = [value for _, value in open_values] + [value for _, value in pending_values]
    by_symbol: dict[str, float] = {}
    by_industry: dict[str, float] = {}
    by_theme: dict[str, float] = {}
    for lot, value in open_values:
        amount = abs(value)
        by_symbol[lot.symbol] = by_symbol.get(lot.symbol, 0.0) + amount
        if lot.industry:
            by_industry[lot.industry] = by_industry.get(lot.industry, 0.0) + amount
        for theme in lot.themes:
            by_theme[theme] = by_theme.get(theme, 0.0) + amount
    for reservation, value in pending_values:
        amount = abs(value)
        by_symbol[reservation.symbol] = by_symbol.get(reservation.symbol, 0.0) + amount
        if reservation.industry:
            by_industry[reservation.industry] = by_industry.get(reservation.industry, 0.0) + amount
        for theme in reservation.themes:
            by_theme[theme] = by_theme.get(theme, 0.0) + amount
    return {
        "gross": sum(abs(value) for value in signed),
        "net": sum(signed),
        "by_symbol": by_symbol,
        "by_industry": by_industry,
        "by_theme": by_theme,
        "locked": tuple(lot.symbol for lot, _ in open_values if lot.locked),
        "stale": tuple(lot.symbol for lot, _ in open_values if lot.quote_status is QuoteStatus.STALE),
        "missing": tuple(lot.symbol for lot, _ in open_values if lot.quote_status is QuoteStatus.MISSING),
    }


def _remaining_caps(
    policy: PortfolioRiskPolicy,
    state: PortfolioRiskState,
    command: RiskGateCommand,
) -> tuple[dict[str, float | None], list[str]]:
    metrics = exposure_metrics(state, policy)
    reasons: list[str] = []
    caps: dict[str, float | None] = dict(command.applicable_execution_caps)
    caps["risk.single_name"] = max(
        0.0, policy.single_name_cap - metrics["by_symbol"].get(command.symbol, 0.0)
    )
    caps["risk.gross"] = max(0.0, policy.gross_exposure_cap - metrics["gross"])
    if command.direction > 0:
        caps["risk.net"] = max(0.0, policy.net_exposure_cap - metrics["net"])
    else:
        caps["risk.net"] = max(0.0, policy.net_exposure_cap + metrics["net"])
    caps["risk.industry"] = (
        max(0.0, policy.industry_cap - metrics["by_industry"].get(command.industry, 0.0))
        if command.industry else None
    )
    theme_remaining = [
        max(0.0, policy.theme_cap - metrics["by_theme"].get(theme, 0.0))
        for theme in command.themes
    ]
    caps["risk.theme"] = min(theme_remaining) if theme_remaining else None
    if metrics["locked"]:
        reasons.append("LOCKED_EXPOSURE_COUNTS")
    if metrics["stale"]:
        reasons.append("STALE_QUOTE_CONSERVATIVE_VALUE")
    if metrics["missing"]:
        reasons.append("MISSING_QUOTE_CONSERVATIVE_VALUE")
    if state.daily_loss_pct >= policy.daily_loss_reduce_at:
        caps["risk.daily_loss_reduce"] = command.requested_size * policy.reduce_multiplier
        reasons.append("DAILY_LOSS_REDUCE")
    if state.drawdown_pct >= policy.drawdown_reduce_at:
        caps["risk.drawdown_reduce"] = command.requested_size * policy.reduce_multiplier
        reasons.append("DRAWDOWN_REDUCE")
    return caps, reasons


def evaluate_risk_gate(
    policy: PortfolioRiskPolicy,
    state: PortfolioRiskState,
    command: RiskGateCommand,
) -> RiskGateTransition:
    """Pure command evaluator.  It never mutates state or invokes an external system."""
    if command.channel not in set(RiskChannel):
        raise PortfolioRiskError("unsupported risk channel")
    if state.state_scope != command.state_scope:
        raise PortfolioRiskError("command and state scope mismatch")
    if state.policy_fingerprint != policy.policy_fingerprint:
        raise PortfolioRiskError("state is not bound to supplied policy")
    if (
        state.version != command.expected_state_version
        or state.state_hash != command.expected_state_hash
    ):
        raise StaleRiskStateError("portfolio risk state compare-and-set failed")

    reasons: list[str] = []
    halt = state.halt_status is RiskHaltStatus.HALTED
    if halt:
        reasons.append("HALT_LATCHED_REQUIRES_INDEPENDENT_RESUME")
    if state.daily_loss_pct >= policy.daily_loss_halt_at:
        halt = True
        reasons.append("DAILY_LOSS_HALT")
    if state.drawdown_pct >= policy.drawdown_halt_at:
        halt = True
        reasons.append("DRAWDOWN_HALT")

    if halt:
        approved = 0.0
        applicable_caps: tuple[tuple[str, float], ...] = (("risk.halt", 0.0),)
        binding_caps = ("risk.halt",)
        kind = RiskDecisionKind.HALT
        next_halt = RiskHaltStatus.HALTED
    else:
        caps, cap_reasons = _remaining_caps(policy, state, command)
        reasons.extend(cap_reasons)
        sizing = compose_position_caps(command.requested_size, caps)
        approved = sizing.approved_size
        applicable_caps = sizing.applicable_caps
        binding_caps = sizing.binding_caps
        next_halt = state.halt_status
        if approved <= 0:
            kind = RiskDecisionKind.REJECT
            reasons.append("NO_RISK_BUDGET")
        elif approved < command.requested_size:
            kind = RiskDecisionKind.REDUCE
            reasons.append("POSITION_CAP_REDUCED")
        else:
            kind = RiskDecisionKind.ALLOW
            reasons.append("WITHIN_ALL_CAPS")

    decision_id = "risk_dec_" + canonical_fingerprint({
        "command": command.command_id,
        "state_hash": state.state_hash,
        "policy": policy.policy_fingerprint,
    })[:48]
    decision = PortfolioRiskDecision(
        decision_id=decision_id,
        command_id=command.command_id,
        state_id=state.state_id,
        state_version=state.version,
        state_hash=state.state_hash,
        policy_fingerprint=policy.policy_fingerprint,
        run_id=command.run_id,
        recommendation_id=command.recommendation_id,
        decision=kind,
        requested_size=command.requested_size,
        approved_size=approved,
        applicable_caps=applicable_caps,
        binding_caps=binding_caps,
        reason_codes=tuple(dict.fromkeys(reasons)),
        decided_at=command.decided_at,
        audit_only=command.audit_only,
    )
    reservation: ExposureReservation | None = None
    pending = state.pending_reservations
    if approved > 0:
        reservation_key = canonical_fingerprint({
            "scope": state.state_scope,
            "command": command.command_id,
            "recommendation": command.recommendation_id,
        })
        reservation = ExposureReservation(
            reservation_id="risk_res_" + reservation_key[:48],
            reservation_key=reservation_key,
            decision_id=decision_id,
            symbol=command.symbol,
            approved_size=approved,
            direction=command.direction,
            industry=command.industry,
            themes=command.themes,
            status="RESERVED",
            lease_token="audit:" + command.command_id,
            created_at=command.decided_at,
            version=1,
        )
        pending = (*pending, reservation)
    next_state = replace(
        state,
        as_of=command.decided_at,
        pending_reservations=pending,
        halt_status=next_halt,
        version=state.version + 1,
        predecessor_state_hash=state.state_hash,
    )
    return RiskGateTransition(decision, reservation, next_state)


@dataclass(frozen=True, slots=True)
class ResumeRiskCommand(CanonicalValue):
    command_id: str
    expected_state_version: int
    expected_state_hash: str
    actor_kind: str
    resumed_at: datetime


def resume_halt(
    policy: PortfolioRiskPolicy,
    state: PortfolioRiskState,
    command: ResumeRiskCommand,
) -> PortfolioRiskState:
    """Resume only through the independent audited boundary, never a normal task."""
    _utc(command.resumed_at, "resumed_at")
    if command.actor_kind != "independent_risk_audit":
        raise PortfolioRiskError("ordinary recommendation tasks cannot resume risk halt")
    if state.version != command.expected_state_version or state.state_hash != command.expected_state_hash:
        raise StaleRiskStateError("resume state compare-and-set failed")
    if state.halt_status is not RiskHaltStatus.HALTED:
        raise PortfolioRiskError("risk state is not halted")
    if state.daily_loss_pct >= policy.resume_daily_loss_below:
        raise PortfolioRiskError("daily loss has not recovered below resume threshold")
    if state.drawdown_pct >= policy.resume_drawdown_below:
        raise PortfolioRiskError("drawdown has not recovered below resume threshold")
    return replace(
        state,
        as_of=command.resumed_at,
        halt_status=RiskHaltStatus.ACTIVE,
        version=state.version + 1,
        predecessor_state_hash=state.state_hash,
    )


class InMemoryRiskTransactionModel:
    """Deterministic CAS/transaction model for Layer-1 concurrency verification.

    This is not a production repository.  It models the existing recommendation
    transaction's all-or-nothing contract and is intentionally incapable of DB or
    brokerage access.  A real SQL adapter must persist the same staged facts inside
    the existing recommendation Session transaction after migration authorization.
    """

    def __init__(self, policy: PortfolioRiskPolicy, initial_state: PortfolioRiskState) -> None:
        if initial_state.policy_fingerprint != policy.policy_fingerprint:
            raise PortfolioRiskError("initial state policy mismatch")
        self._lock = RLock()
        self._policies: tuple[PortfolioRiskPolicy, ...] = (policy,)
        self._states: tuple[PortfolioRiskState, ...] = (initial_state,)
        self._decisions: tuple[PortfolioRiskDecision, ...] = ()
        self._reservations: tuple[ExposureReservation, ...] = ()
        self._recommendations: tuple[Mapping[str, Any], ...] = ()

    @property
    def current_policy(self) -> PortfolioRiskPolicy:
        return self._policies[-1]

    @property
    def current_state(self) -> PortfolioRiskState:
        return self._states[-1]

    @property
    def policies(self) -> tuple[PortfolioRiskPolicy, ...]:
        return self._policies

    @property
    def states(self) -> tuple[PortfolioRiskState, ...]:
        return self._states

    @property
    def decisions(self) -> tuple[PortfolioRiskDecision, ...]:
        return self._decisions

    @property
    def reservations(self) -> tuple[ExposureReservation, ...]:
        return self._reservations

    @property
    def recommendations(self) -> tuple[Mapping[str, Any], ...]:
        return self._recommendations

    def register_policy(self, policy: PortfolioRiskPolicy) -> None:
        with self._lock:
            if any(item.policy_fingerprint == policy.policy_fingerprint for item in self._policies):
                return
            if any(
                item.policy_id == policy.policy_id and item.policy_version == policy.policy_version
                for item in self._policies
            ):
                raise PortfolioRiskError("policy version is append-only and already has another fingerprint")
            self._policies = (*self._policies, policy)

    def execute(
        self,
        command: RiskGateCommand,
        recommendation: Mapping[str, Any],
        *,
        fail_at: str | None = None,
    ) -> RiskGateTransition:
        """Stage decision/reservation/state/recommendation, then commit one snapshot."""
        with self._lock:
            before = (
                self._states, self._decisions, self._reservations, self._recommendations
            )
            try:
                transition = evaluate_risk_gate(self.current_policy, self.current_state, command)
                staged_decisions = (*self._decisions, transition.decision)
                if fail_at == "decision":
                    raise RiskTransactionError("injected decision append failure")
                staged_reservations = self._reservations
                if transition.reservation is not None:
                    staged_reservations = (*staged_reservations, transition.reservation)
                if fail_at == "reservation":
                    raise RiskTransactionError("injected reservation failure")
                audit = transition.decision.canonical_payload()
                staged_recommendation = dict(recommendation)
                staged_recommendation["portfolio_risk_would_decision"] = audit
                if fail_at == "recommendation":
                    raise RiskTransactionError("injected recommendation write failure")
                self._states = (*self._states, transition.next_state)
                self._decisions = staged_decisions
                self._reservations = staged_reservations
                self._recommendations = (*self._recommendations, staged_recommendation)
                return transition
            except Exception:
                (
                    self._states, self._decisions, self._reservations,
                    self._recommendations,
                ) = before
                raise


def attach_would_decisions(
    policy: PortfolioRiskPolicy,
    state: PortfolioRiskState,
    *,
    run_id: str,
    cards: Sequence[Mapping[str, Any]],
    requested_size: Callable[[Mapping[str, Any]], float],
    decided_at: datetime,
    channel: RiskChannel = RiskChannel.RECOMMENDATION,
) -> tuple[tuple[dict[str, Any], ...], tuple[PortfolioRiskDecision, ...], PortfolioRiskState]:
    """Audit-only recommendation boundary; card order/content and count are preserved.

    The decision is embedded in the existing ``feature_snapshot`` payload so the
    existing recommendation persistence transaction owns the write.  Approved size
    is explicitly a *would* value and never changes production recommendation size.
    """
    _utc(decided_at, "decided_at")
    current = state
    output: list[dict[str, Any]] = []
    decisions: list[PortfolioRiskDecision] = []
    for index, original in enumerate(cards):
        card = dict(original)
        recommendation_id = str(card.get("recommendation_id") or f"{run_id}:card:{index}")
        command = RiskGateCommand(
            command_id=f"{run_id}:risk-audit:{index}",
            state_scope=current.state_scope,
            expected_state_version=current.version,
            expected_state_hash=current.state_hash,
            run_id=run_id,
            recommendation_id=recommendation_id,
            symbol=str(card.get("code") or ""),
            requested_size=requested_size(card),
            industry=str(card.get("industry") or ""),
            themes=tuple(str(item) for item in (card.get("themes") or ())),
            channel=channel,
            audit_only=True,
            decided_at=decided_at,
        )
        transition = evaluate_risk_gate(policy, current, command)
        snapshot = dict(card.get("feature_snapshot") or {})
        snapshot["portfolio_risk_would_decision"] = transition.decision.canonical_payload()
        card["feature_snapshot"] = snapshot
        output.append(card)
        decisions.append(transition.decision)
        current = transition.next_state
    return tuple(output), tuple(decisions), current


__all__ = [
    "ExposureReservation", "InMemoryRiskTransactionModel", "PortfolioRiskDecision",
    "PortfolioRiskError", "PortfolioRiskPolicy", "PortfolioRiskState", "PositionExposure",
    "QuoteStatus", "ResumeRiskCommand", "RiskChannel", "RiskDecisionKind",
    "RiskGateCommand", "RiskGateTransition", "RiskHaltStatus", "RiskTransactionError",
    "StaleRiskStateError", "attach_would_decisions", "evaluate_risk_gate",
    "exposure_metrics", "resume_halt",
]

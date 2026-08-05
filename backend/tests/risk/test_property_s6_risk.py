"""Property 27–28 and S6.6/S6.7 Layer-1 portfolio-risk verification.

Every property runs 100 generated examples.  Tests use only pure
recommendation/simulation/paper/shadow facts; no database, network or brokerage
resource is imported or accessed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from hypothesis import given, settings, strategies as st
import pytest

from app.engine.portfolio_risk import (
    InMemoryRiskTransactionModel,
    PortfolioRiskError,
    PortfolioRiskPolicy,
    PortfolioRiskState,
    PositionExposure,
    QuoteStatus,
    ResumeRiskCommand,
    RiskChannel,
    RiskDecisionKind,
    RiskGateCommand,
    RiskHaltStatus,
    RiskTransactionError,
    StaleRiskStateError,
    evaluate_risk_gate,
    exposure_metrics,
    resume_halt,
)
from app.services.runs.risk_audit import PortfolioRiskWouldDecisionAudit

NOW = datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)


def policy(**changes) -> PortfolioRiskPolicy:
    values = {
        "policy_id": "s6-risk",
        "policy_version": "1.0.0",
        "single_name_cap": 0.20,
        "gross_exposure_cap": 0.80,
        "net_exposure_cap": 0.80,
        "industry_cap": 0.50,
        "theme_cap": 0.40,
        "daily_loss_reduce_at": 0.02,
        "daily_loss_halt_at": 0.04,
        "drawdown_reduce_at": 0.06,
        "drawdown_halt_at": 0.10,
        "resume_daily_loss_below": 0.01,
        "resume_drawdown_below": 0.04,
        "reduce_multiplier": 0.50,
        "stale_quote_multiplier": 1.10,
        "missing_quote_multiplier": 1.25,
    }
    values.update(changes)
    return PortfolioRiskPolicy(**values)


def state(p: PortfolioRiskPolicy, **changes) -> PortfolioRiskState:
    values = {
        "state_id": "state-main",
        "state_scope": "paper:alpha",
        "policy_fingerprint": p.policy_fingerprint,
        "as_of": NOW,
        "capital": 1_000_000.0,
        "cash": 1_000_000.0,
        "equity_peak": 1_000_000.0,
        "current_equity": 1_000_000.0,
    }
    values.update(changes)
    return PortfolioRiskState(**values)


def command(s: PortfolioRiskState, index: int, size: float, **changes) -> RiskGateCommand:
    values = {
        "command_id": f"cmd-{index}",
        "state_scope": s.state_scope,
        "expected_state_version": s.version,
        "expected_state_hash": s.state_hash,
        "run_id": "run-paper",
        "recommendation_id": f"rec-{index}",
        "symbol": f"{index:06d}",
        "requested_size": size,
        "industry": "technology",
        "themes": ("ai",),
        "channel": RiskChannel.PAPER,
        "audit_only": True,
        "decided_at": NOW + timedelta(microseconds=index),
    }
    values.update(changes)
    return RiskGateCommand(**values)


@settings(max_examples=100, deadline=None)
@given(
    first_cap=st.floats(min_value=0.05, max_value=0.50, allow_nan=False, allow_infinity=False),
    second_cap=st.floats(min_value=0.05, max_value=0.50, allow_nan=False, allow_infinity=False),
)
def test_property_27_risk_facts_are_versioned_append_only(first_cap, second_cap):
    """Property 27: Risk policy, state, and decisions are versioned append-only facts."""
    p1 = policy(single_name_cap=first_cap)
    s1 = state(p1)
    ledger = InMemoryRiskTransactionModel(p1, s1)
    t1 = ledger.execute(command(s1, 1, min(0.04, first_cap)), {"code": "000001"})

    p2 = policy(policy_version="1.0.1", single_name_cap=second_cap)
    ledger.register_policy(p2)

    assert ledger.policies[0] == p1
    assert ledger.policies[-1] == p2
    assert ledger.states[0] == s1
    assert ledger.states[-1].version == s1.version + 1
    assert ledger.states[-1].predecessor_state_hash == s1.state_hash
    assert ledger.decisions[-1] == t1.decision
    assert len(t1.decision.state_hash) == 64
    assert len(t1.decision.policy_fingerprint) == 64
    assert t1.decision.reason_codes
    assert t1.decision.decided_at == NOW + timedelta(microseconds=1)


@settings(max_examples=100, deadline=None)
@given(
    locked=st.floats(min_value=0.01, max_value=0.60, allow_nan=False, allow_infinity=False),
    request=st.floats(min_value=0.001, max_value=0.30, allow_nan=False, allow_infinity=False),
    fail_at=st.sampled_from([None, "decision", "reservation", "recommendation"]),
)
def test_property_28_atomic_reservation_respects_locked_exposure(locked, request, fail_at):
    """Property 28: Risk reservation is atomic and respects loss and locked exposure."""
    p = policy(gross_exposure_cap=0.65, single_name_cap=0.50)
    s = state(
        p,
        open_exposures=(PositionExposure("600000", locked, locked=True),),
    )
    ledger = InMemoryRiskTransactionModel(p, s)
    before = (ledger.states, ledger.decisions, ledger.reservations, ledger.recommendations)
    cmd = command(s, 1, request)

    if fail_at is not None:
        with pytest.raises(RiskTransactionError):
            ledger.execute(cmd, {"code": "000001"}, fail_at=fail_at)
        assert (ledger.states, ledger.decisions, ledger.reservations, ledger.recommendations) == before
        return

    transition = ledger.execute(cmd, {"code": "000001"})
    maximum = max(0.0, 0.65 - locked)
    assert transition.decision.approved_size <= maximum + 1e-12
    assert len(ledger.decisions) == 1
    assert len(ledger.states) == 2
    assert len(ledger.recommendations) == 1
    assert len(ledger.reservations) == (1 if transition.decision.approved_size > 0 else 0)


def test_stress_same_theme_multi_stock_drawdown_gap_and_weak_regime_caps_compose():
    p = policy(theme_cap=0.30, gross_exposure_cap=0.60)
    lots = tuple(
        PositionExposure(
            f"60000{i}", 0.08, industry="technology", themes=("ai",),
            quote_status=QuoteStatus.STALE if i == 0 else QuoteStatus.CURRENT,
            locked=i == 1,
        )
        for i in range(3)
    )
    s = state(p, open_exposures=lots, current_equity=930_000.0)
    c = command(
        s, 9, 0.20,
        applicable_execution_caps=(("execution.weak_regime", 0.07), ("execution.gap_down", 0.05)),
    )
    result = evaluate_risk_gate(p, s, c)
    assert result.decision.decision is RiskDecisionKind.REDUCE
    assert result.decision.approved_size <= min(0.05, 0.30 - (0.08 * 1.10 + 0.16), 0.10)
    assert "STALE_QUOTE_CONSERVATIVE_VALUE" in result.decision.reason_codes
    assert "LOCKED_EXPOSURE_COUNTS" in result.decision.reason_codes
    assert "DRAWDOWN_REDUCE" in result.decision.reason_codes


def test_consecutive_limit_locked_and_missing_quotes_continue_consuming_budget():
    p = policy(gross_exposure_cap=0.50)
    s = state(
        p,
        open_exposures=(
            PositionExposure("600001", 0.20, locked=True, quote_status=QuoteStatus.MISSING),
            PositionExposure("600002", 0.15, locked=True, quote_status=QuoteStatus.STALE),
        ),
    )
    metrics = exposure_metrics(s, p)
    assert metrics["gross"] == pytest.approx(0.20 * 1.25 + 0.15 * 1.10)
    result = evaluate_risk_gate(p, s, command(s, 2, 0.20, symbol="600003"))
    assert result.decision.approved_size == pytest.approx(0.50 - metrics["gross"])


def test_daily_loss_and_drawdown_halt_latch_and_only_independent_resume():
    p = policy()
    halted = state(p, realized_pnl_today_pct=-0.041)
    transition = evaluate_risk_gate(p, halted, command(halted, 3, 0.10))
    assert transition.decision.decision is RiskDecisionKind.HALT
    assert transition.next_state.halt_status is RiskHaltStatus.HALTED

    recovered = PortfolioRiskState(
        **{
            **transition.next_state.canonical_payload(),
            "as_of": NOW + timedelta(minutes=1),
            "realized_pnl_today_pct": 0.0,
            "unrealized_pnl_today_pct": 0.0,
            "current_equity": 1_000_000.0,
            "open_exposures": transition.next_state.open_exposures,
            "pending_reservations": transition.next_state.pending_reservations,
            "halt_status": RiskHaltStatus.HALTED,
        }
    )
    with pytest.raises(PortfolioRiskError):
        resume_halt(
            p, recovered,
            ResumeRiskCommand("ordinary", recovered.version, recovered.state_hash, "recommendation_task", NOW),
        )
    resumed = resume_halt(
        p, recovered,
        ResumeRiskCommand(
            "approved", recovered.version, recovered.state_hash,
            "independent_risk_audit", NOW + timedelta(minutes=2),
        ),
    )
    assert resumed.halt_status is RiskHaltStatus.ACTIVE
    assert resumed.predecessor_state_hash == recovered.state_hash


def test_audit_boundary_records_would_decision_without_changing_recommendation():
    p = policy(gross_exposure_cap=0.05)
    s = state(p)
    audit = PortfolioRiskWouldDecisionAudit(p, s)
    original = ({"code": "000001", "name": "A", "position_size": 0.10, "final_score": 88.0},)
    cards = audit.audit(run_id="run-audit", trade_date="2026-08-04", cards=original)
    assert original[0].get("feature_snapshot") is None
    assert cards[0]["final_score"] == original[0]["final_score"]
    assert cards[0]["position_size"] == original[0]["position_size"]
    would = cards[0]["feature_snapshot"]["portfolio_risk_would_decision"]
    assert would["decision"] == "REDUCE"
    assert would["approved_size"] == 0.05
    assert would["audit_only"] is True


def test_recommendation_failure_rolls_back_all_risk_facts():
    p = policy()
    s = state(p)
    ledger = InMemoryRiskTransactionModel(p, s)
    with pytest.raises(RiskTransactionError):
        ledger.execute(command(s, 4, 0.10), {"code": "000004"}, fail_at="recommendation")
    assert ledger.states == (s,)
    assert ledger.decisions == ledger.reservations == ledger.recommendations == ()


def test_stale_state_retry_and_concurrent_requests_never_overallocate():
    p = policy(gross_exposure_cap=0.50, single_name_cap=0.50, industry_cap=0.50, theme_cap=0.50)
    s = state(p)
    ledger = InMemoryRiskTransactionModel(p, s)

    def contender(index: int) -> str:
        snapshot = ledger.current_state
        try:
            ledger.execute(command(snapshot, index, 0.20, symbol=f"60{index:04d}"), {"index": index})
            return "committed"
        except StaleRiskStateError:
            return "stale"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(contender, range(12)))

    assert "committed" in outcomes
    assert sum(item.approved_size for item in ledger.reservations) <= 0.50 + 1e-12
    assert len(ledger.decisions) == len(ledger.recommendations)
    assert len(ledger.states) == len(ledger.decisions) + 1


def test_channels_are_closed_to_any_brokerage_shape():
    assert {item.value for item in RiskChannel} == {"recommendation", "simulation", "paper", "shadow"}
    p = policy()
    s = state(p)
    with pytest.raises(TypeError):
        command(s, 1, 0.1, brokerage_account="forbidden")

"""Hypothesis properties 11–14 for canonical execution.

Every property uses at least 100 generated examples and performs no database,
network, or production write.
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.engine.backtest import BarInput, ExecutionPolicy, RecommendationInput
from app.engine.execution_core import (
    CausalExitConfig,
    ExecutionBar,
    ExecutionStatus,
    compose_position_caps,
    resolve_causal_exit,
)
from app.engine.execution_policy import (
    DEFAULT_EXECUTION_POLICY,
    FeePolicy,
    SlippagePolicy,
    TrailingPolicy,
    execution_policy_from_config_projection,
)
from app.engine.simulation import simulate
from testkit import TPlusOneExecutionAdapter, decimal_text


@settings(max_examples=100, deadline=None)
@given(
    horizon=st.integers(min_value=1, max_value=20),
    entry_bps=st.integers(min_value=0, max_value=50),
    exit_bps=st.integers(min_value=0, max_value=50),
)
def test_property_11_execution_policy_identity_is_deterministic_and_versioned(
    horizon, entry_bps, exit_bps
):
    """**Property 11: ExecutionPolicy identity is deterministic and versioned**"""
    payload = dict(DEFAULT_EXECUTION_POLICY.config_projection())
    payload.update(
        horizon_days=horizon,
        entry_slippage_bps=float(entry_bps),
        exit_slippage_bps=float(exit_bps),
    )
    first = execution_policy_from_config_projection(payload)
    reordered = execution_policy_from_config_projection(
        dict(reversed(tuple(payload.items())))
    )
    changed_payload = {**payload, "horizon_days": 21 if horizon == 20 else horizon + 1}
    changed = execution_policy_from_config_projection(changed_payload)

    assert first == reordered
    assert first.fingerprint() == reordered.fingerprint()
    if payload != dict(DEFAULT_EXECUTION_POLICY.config_projection()):
        assert first.version.startswith("3.0.0-cfg-")
    assert changed.version != first.version
    assert changed.fingerprint() != first.fingerprint()


@settings(max_examples=100, deadline=None)
@given(
    entry_high=st.floats(min_value=106, max_value=130, allow_nan=False, allow_infinity=False),
    entry_low=st.floats(min_value=70, max_value=94, allow_nan=False, allow_infinity=False),
    next_close=st.floats(min_value=96, max_value=104, allow_nan=False, allow_infinity=False),
)
def test_property_12_canonical_adapters_preserve_parity_and_t_plus_one(
    entry_high, entry_low, next_close
):
    """**Property 12: Canonical adapters preserve parity and T+1**"""
    policy = ExecutionPolicy(
        policy_id="property12",
        version="1.0.0",
        entry_lookahead_days=1,
        horizon_days=2,
        t_plus_one=True,
        fees=FeePolicy(0.0, 0.0),
        slippage=SlippagePolicy(0.0, 0.0),
        trailing=TrailingPolicy(enabled=False),
        stop_loss_pct=5.0,
        take_profit_pct=5.0,
    )
    recommendation = RecommendationInput(
        "000001", "2026-01-01", 100.0, "property12-rec"
    )
    bars = (
        BarInput("000001", "2026-01-02", 100.0, entry_high, entry_low, 100.0),
        BarInput(
            "000001",
            "2026-01-03",
            100.0,
            max(104.0, next_close),
            min(96.0, next_close),
            next_close,
        ),
    )
    simulation = simulate(recommendation, bars, policy)
    projection = TPlusOneExecutionAdapter().run(recommendation, bars, policy)

    assert simulation.trade.entry_date == "2026-01-02"
    assert simulation.trade.exit_date == "2026-01-03"
    assert simulation.trade.entry_date != simulation.trade.exit_date
    assert projection.entry_date == simulation.trade.entry_date
    assert projection.exit_date == simulation.trade.exit_date
    assert projection.entry_price == decimal_text(
        simulation.trade.entry_price, "entry_price"
    )
    assert projection.exit_price == decimal_text(
        simulation.trade.exit_price, "exit_price"
    )
    assert projection.exit_reason == simulation.trade.exit_reason
    assert projection.net_return_pct == decimal_text(
        simulation.trade.net_return_pct, "net_return_pct"
    )


@settings(max_examples=100, deadline=None)
@given(
    requested=st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False),
    caps=st.lists(
        st.one_of(
            st.none(),
            st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False),
        ),
        min_size=0,
        max_size=10,
    ),
)
def test_property_13_no_position_can_bypass_an_applicable_cap(requested, caps):
    """**Property 13: No position can bypass an applicable cap**"""
    mapping = {f"cap_{index}": value for index, value in enumerate(caps)}
    decision = compose_position_caps(requested, mapping)
    applicable = [value for value in caps if value is not None]
    expected = min([requested, *applicable])

    assert decision.approved_size == expected
    assert decision.approved_size <= requested
    assert all(
        decision.approved_size <= value for value in applicable
    )


@settings(max_examples=100, deadline=None)
@given(
    priority=st.sampled_from(("stop_loss", "take_profit")),
    gap_open=st.floats(min_value=70, max_value=94.99, allow_nan=False, allow_infinity=False),
)
def test_property_14_canonical_execution_matches_registered_edge_semantics(
    priority, gap_open
):
    """**Property 14: Canonical execution matches registered edge semantics**"""
    both_hit = resolve_causal_exit(
        (ExecutionBar("000001", "2026-01-02", 100, 110, 90, 100),),
        CausalExitConfig(
            stop_loss_price=95,
            take_profit_price=105,
            entry_price=100,
            t_plus_one=False,
            dynamic_trailing=False,
            intraday_exit_priority=priority,
        ),
    )
    assert both_hit is not None
    assert both_hit.reason == (
        "stop_loss_intraday_both_hit"
        if priority == "stop_loss"
        else "take_profit_intraday_both_hit"
    )

    gap = resolve_causal_exit(
        (
            ExecutionBar("000001", "2026-01-02", 100, 101, 99, 100),
            ExecutionBar("000001", "2026-01-03", gap_open, gap_open, gap_open, gap_open),
        ),
        CausalExitConfig(
            stop_loss_price=95,
            take_profit_price=105,
            entry_price=100,
            t_plus_one=True,
            dynamic_trailing=False,
            stop_gap_loss_cap_pct=15,
        ),
    )
    assert gap is not None
    assert gap.reason == "stop_loss_gap"
    assert gap.raw_price == max(gap_open, 85.0)

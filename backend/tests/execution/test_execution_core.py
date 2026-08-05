"""S2-b canonical execution core and T+1 regression tests.

**Validates: Requirements 3.2–3.5, 3.8**
"""
from __future__ import annotations

import inspect

import pytest

from app.engine.backtest import BarInput, ExecutionPolicy, RecommendationInput
from app.engine.execution_core import (
    CausalExitConfig,
    CausalExitResult,
    ExecutionBar,
    ExecutionIntent,
    ExecutionRequest,
    ExecutionStatus,
    compose_position_caps,
    execute_trade,
    resolve_causal_exit,
)
from app.engine.execution_policy import (
    DEFAULT_EXECUTION_POLICY,
    FeePolicy,
    IntradayExitPriority,
    SlippagePolicy,
    TrailingPolicy,
)
from app.engine.simulation import simulate
from app.models.tables import DailyKbar
from app.services import outcome_simulation


def _policy(*, t_plus_one: bool = True, horizon_days: int = 2, priority="stop_loss"):
    return ExecutionPolicy(
        policy_id="canonical-core-test",
        version=f"t{int(t_plus_one)}-{priority}-{horizon_days}",
        entry_lookahead_days=1,
        horizon_days=horizon_days,
        t_plus_one=t_plus_one,
        fees=FeePolicy(commission_rate_per_side=0.0, stamp_tax_rate_on_exit=0.0),
        slippage=SlippagePolicy(entry_bps=0.0, exit_bps=0.0),
        trailing=TrailingPolicy(enabled=False),
        stop_loss_pct=5.0,
        take_profit_pct=5.0,
        intraday_exit_priority=priority,
    )


def _request(*bars: ExecutionBar) -> ExecutionRequest:
    return ExecutionRequest(
        intent=ExecutionIntent("000001", "2026-06-01", 100.0, "rec-core-t1"),
        bars=tuple(bars),
    )


def _bar(date: str, *, open=100.0, high=101.0, low=99.0, close=100.0, sellable=True):
    return ExecutionBar(
        "000001", date, open, high, low, close, tradable=True, sellable=sellable
    )


def test_t_plus_one_never_scans_entry_bar_even_when_stop_and_target_both_hit():
    request = _request(
        _bar("2026-06-02", high=110.0, low=90.0),
        _bar("2026-06-03", high=101.0, low=99.0, close=100.5),
    )

    result = execute_trade(request, _policy(t_plus_one=True))

    assert result.status is ExecutionStatus.CLOSED
    assert result.entry.trade_date == "2026-06-02"
    assert result.exit is not None
    assert result.exit.trade_date == "2026-06-03"
    assert result.exit.reason == "horizon_close"
    assert result.exit.trade_date > result.entry.trade_date


def test_t_plus_one_false_can_exit_entry_bar_using_registered_priority():
    request = _request(_bar("2026-06-02", high=110.0, low=90.0))

    stop_first = execute_trade(
        request,
        _policy(t_plus_one=False, horizon_days=1, priority="stop_loss"),
    )
    target_first = execute_trade(
        request,
        _policy(t_plus_one=False, horizon_days=1, priority="take_profit"),
    )

    assert stop_first.exit is not None
    assert stop_first.exit.trade_date == stop_first.entry.trade_date
    assert stop_first.exit.reason == "stop_loss_intraday_both_hit"
    assert target_first.exit is not None
    assert target_first.exit.reason == "take_profit_intraday_both_hit"


def test_single_entry_bar_is_open_t1_locked_not_a_same_day_final_trade():
    request = _request(_bar("2026-06-02", high=110.0, low=90.0, close=98.0))

    core_result = execute_trade(request, _policy(t_plus_one=True, horizon_days=1))
    adapter_result = simulate(
        RecommendationInput("000001", "2026-06-01", 100.0),
        (BarInput("000001", "2026-06-02", 100.0, 110.0, 90.0, 98.0),),
        _policy(t_plus_one=True, horizon_days=1),
    )

    assert core_result.status is ExecutionStatus.OPEN_T1_LOCKED
    assert core_result.entry.triggered
    assert core_result.exit is None
    assert adapter_result.entry_triggered is True
    assert adapter_result.status == "open_t1_locked"
    assert adapter_result.trade.entry_date is None
    assert adapter_result.trade.exit_date is None
    assert adapter_result.trade.exit_reason == "horizon_close_t1_locked"


def test_research_adapter_is_order_invariant_and_has_zero_t1_same_day_exits():
    recommendation = RecommendationInput("000001", "2026-06-01", 100.0)
    bars = (
        BarInput("000001", "2026-06-02", 100.0, 110.0, 90.0, 100.0),
        BarInput("000001", "2026-06-03", 100.0, 101.0, 99.0, 100.0),
    )

    forward = simulate(recommendation, bars, _policy())
    reverse = simulate(recommendation, tuple(reversed(bars)), _policy())

    assert forward == reverse
    assert forward.trade.entry_date == "2026-06-02"
    assert forward.trade.exit_date == "2026-06-03"
    assert forward.trade.entry_date != forward.trade.exit_date


def test_untradable_exit_is_deferred_and_attributed_at_horizon():
    bars = (
        _bar("2026-06-02"),
        _bar("2026-06-03", open=90.0, high=90.0, low=90.0, close=90.0, sellable=False),
        _bar("2026-06-04", open=100.0, high=101.0, low=99.0, close=100.0),
    )
    config = CausalExitConfig(
        stop_loss_price=95.0,
        take_profit_price=105.0,
        entry_price=100.0,
        t_plus_one=True,
        dynamic_trailing=False,
    )

    result = resolve_causal_exit(bars, config)

    assert result is not None
    assert result.trade_date == "2026-06-04"
    assert result.reason == "horizon_close_after_locked_limit_down"


def test_formal_outcome_exit_is_only_an_adapter_to_execution_core(monkeypatch):
    observed = {}

    def fake_resolve(bars, config):
        observed["bars"] = bars
        observed["config"] = config
        return CausalExitResult(101.0, "2026-06-03", "horizon_close", 2)

    monkeypatch.setattr(outcome_simulation, "resolve_causal_exit", fake_resolve)
    bars = [
        DailyKbar(
            code="000001",
            trade_date="2026-06-02",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
            amount=1.0,

            pct_chg=0.0,
        ),
        DailyKbar(
            code="000001",
            trade_date="2026-06-03",
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1.0,
            amount=1.0,
            pct_chg=1.0,
        ),
    ]

    result = outcome_simulation._resolve_exit_causal(
        bars,
        stop_loss_price=95.0,
        take_profit_price=106.0,
        code="000001",
        entry_price=100.0,
    )

    assert result == (101.0, "2026-06-03", "horizon_close")
    assert len(observed["bars"]) == 2
    assert observed["config"].t_plus_one is True


def test_position_cap_contract_uses_every_cap_and_fails_closed():
    decision = compose_position_caps(
        0.9,
        {
            "score": 0.5,
            "market": 0.4,
            "risk_template": 0.3,
            "strategy": 0.25,
            "single_name": 0.2,
            "gross": 0.8,
            "net": 0.7,
            "industry": 0.15,
            "theme": 0.18,
        },
    )
    assert decision.approved_size == 0.15
    assert decision.binding_caps == ("industry",)

    with pytest.raises(ValueError, match="position cap"):
        compose_position_caps(0.5, {"bad": -0.01})


def test_research_current_v2_applies_global_minimum_cap():
    result = execute_trade(
        _request(
            _bar("2026-06-02"),
            _bar("2026-06-03", close=101.0),
        ),
        DEFAULT_EXECUTION_POLICY,
    )

    assert result.status is ExecutionStatus.CLOSED
    assert result.position_size == 0.35


def test_execution_core_has_no_orm_service_or_configuration_imports():
    source = inspect.getsource(__import__(
        "app.engine.execution_core", fromlist=["execution_core"]
    ))
    assert "app.models" not in source
    assert "app.services" not in source
    assert "app.core.config" not in source
    assert "sqlalchemy" not in source

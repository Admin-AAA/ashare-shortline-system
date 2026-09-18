"""S2-b canonical execution core and T+1 regression tests.

**Validates: Requirements 3.2–3.5, 3.8**
"""
from __future__ import annotations

import inspect

import pytest

from app.engine.backtest import BarInput, ExecutionPolicy, RecommendationInput
from app.engine.execution_core import (
    CausalEntryBar,
    CausalEntryConfig,
    CausalExitConfig,
    CausalExitResult,
    ExecutionBar,
    ExecutionIntent,
    ExecutionRequest,
    ExecutionStatus,
    IntradayExitEvidence,
    IntradayFirstHit,
    compose_position_caps,
    execute_trade,
    resolve_causal_entry,
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




def _entry_config(**overrides) -> CausalEntryConfig:
    values = {
        "policy_id": "causal_strategy_entry_v1",
        "price_source": "intraday_snapshot_or_postclose_target",
        "signal_date": "2026-06-01",
        "recommendation_price": 10.0,
        "intraday": False,
        "target_prices": (9.8,),
        "max_entry_trade_days": 2,
        "horizon_days": 5,
        "allow_target_touch": True,
        "fallback_to_open_when_targets_miss": False,
        "entry_slippage_bps": 10.0,
    }
    values.update(overrides)
    return CausalEntryConfig(**values)


def test_canonical_entry_core_owns_target_touch_and_slippage():
    decision = resolve_causal_entry(
        (
            CausalEntryBar("000001", "2026-06-02", 10.0, 9.7, 9.9),
            CausalEntryBar("000001", "2026-06-03", 10.1, 9.9, 10.0),
        ),
        _entry_config(),
    )

    assert decision.triggered is True
    assert decision.trade_date == "2026-06-02"
    assert decision.raw_price == pytest.approx(9.8)
    assert decision.executed_price == pytest.approx(9.8098)
    assert decision.reason == "buy_target_touched"


def test_canonical_entry_core_owns_intraday_snapshot_entry():
    decision = resolve_causal_entry(
        (),
        _entry_config(
            intraday=True,
            recommendation_price=10.35,
            target_prices=(),
        ),
    )

    assert decision.triggered is True
    assert decision.trade_date == "2026-06-01"
    assert decision.raw_price == pytest.approx(10.35)
    assert decision.executed_price == pytest.approx(10.36035)
    assert decision.reason == "intraday_recommend_price_entry"


def test_canonical_entry_core_rejects_unreached_target_without_fallback():
    decision = resolve_causal_entry(
        (CausalEntryBar("000001", "2026-06-02", 10.2, 10.1, 10.15),),
        _entry_config(),
    )

    assert decision.triggered is False
    assert "未触发目标买点" in decision.reason


def test_canonical_entry_core_uses_second_open_after_first_day_target_miss():
    decision = resolve_causal_entry(
        (
            CausalEntryBar("000001", "2026-06-02", 10.2, 10.1, 10.15),
            CausalEntryBar("000001", "2026-06-03", 10.3, 10.0, 10.2),
        ),
        _entry_config(
            fallback_to_open_when_targets_miss=True,
            max_entry_gap_pct=6.0,
        ),
    )

    assert decision.triggered is True
    assert decision.trade_date == "2026-06-03"
    assert decision.raw_price == pytest.approx(10.3)
    assert decision.executed_price == pytest.approx(10.3103)
    assert decision.reason == "next_open_after_target_miss_entry（前一交易日买点未命中，次日开盘入场）"


def test_canonical_entry_core_does_not_backdate_fallback_with_one_bar():
    decision = resolve_causal_entry(
        (CausalEntryBar("000001", "2026-06-02", 10.2, 10.1, 10.15),),
        _entry_config(
            fallback_to_open_when_targets_miss=True,
            max_entry_gap_pct=6.0,
        ),
    )

    assert decision.triggered is False
    assert "未触发目标买点" in decision.reason


def test_canonical_entry_core_owns_max_entry_gap_rejection():
    decision = resolve_causal_entry(
        (CausalEntryBar("000001", "2026-06-02", 11.2, 11.1, 11.15),),
        _entry_config(
            target_prices=(),
            fallback_to_open_when_targets_miss=True,
            recommendation_price=10.0,
            max_entry_gap_pct=10.0,
        ),
    )

    assert decision.triggered is False
    assert decision.reason == "gap_up_blocked"
    assert decision.observed_gap_pct == pytest.approx(12.0)


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
    assert result.exit.intraday_exit_evidence.first_hit is IntradayFirstHit.NOT_APPLICABLE
    assert result.exit.intraday_exit_evidence.same_bar_stop_take_profit_hit is False
    assert result.exit.intraday_exit_evidence.applied_priority is None
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
    assert stop_first.exit.raw_price == 95.0
    assert stop_first.exit.intraday_exit_evidence == IntradayExitEvidence(
        IntradayFirstHit.AMBIGUOUS,
        True,
        IntradayExitPriority.STOP_LOSS,
    )
    assert target_first.exit is not None
    assert target_first.exit.reason == "take_profit_intraday_both_hit"
    assert target_first.exit.raw_price == 105.0
    assert target_first.exit.intraday_exit_evidence == IntradayExitEvidence(
        IntradayFirstHit.AMBIGUOUS,
        True,
        IntradayExitPriority.TAKE_PROFIT,
    )


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
    assert adapter_result.intraday_exit_evidence is None
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
    assert forward.intraday_exit_evidence is not None
    assert forward.intraday_exit_evidence.first_hit is IntradayFirstHit.NOT_APPLICABLE
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
    assert result.intraday_exit_evidence.first_hit is IntradayFirstHit.NOT_APPLICABLE


def test_canonical_exit_evidence_covers_single_hits_trailing_gap_and_horizon():
    only_stop = resolve_causal_exit(
        (_bar("2026-06-02"), _bar("2026-06-03", high=104.0, low=94.0)),
        CausalExitConfig(95.0, 105.0, 100.0, dynamic_trailing=False),
    )
    only_target = resolve_causal_exit(
        (_bar("2026-06-02"), _bar("2026-06-03", high=106.0, low=96.0)),
        CausalExitConfig(95.0, 105.0, 100.0, dynamic_trailing=False),
    )
    gap = resolve_causal_exit(
        (_bar("2026-06-02"), _bar("2026-06-03", open=94.0, high=96.0, low=93.0)),
        CausalExitConfig(95.0, 105.0, 100.0, dynamic_trailing=False),
    )
    trailing = resolve_causal_exit(
        (
            _bar("2026-06-02", close=103.0),
            _bar("2026-06-03", open=103.0, high=104.0, low=101.0),
        ),
        CausalExitConfig(
            95.0,
            110.0,
            100.0,
            trailing_mode="profit_floor",
            profit_lock_ratio=0.5,
        ),
    )

    assert only_stop is not None
    assert only_stop.intraday_exit_evidence.first_hit is IntradayFirstHit.STOP_LOSS
    assert only_target is not None
    assert only_target.intraday_exit_evidence.first_hit is IntradayFirstHit.TAKE_PROFIT
    assert gap is not None
    assert gap.intraday_exit_evidence.first_hit is IntradayFirstHit.NOT_APPLICABLE
    assert trailing is not None
    assert trailing.reason == "trailing_stop_hit"
    assert trailing.intraday_exit_evidence.first_hit is IntradayFirstHit.TRAILING_STOP


def test_default_numerical_execution_is_unchanged_with_typed_evidence():
    result = execute_trade(
        _request(
            _bar("2026-06-02"),
            _bar("2026-06-03", high=110.0, low=90.0),
        ),
        _policy(),
    )

    assert result.exit is not None
    assert result.exit.reason == "stop_loss_intraday_both_hit"
    assert result.exit.raw_price == 95.0
    assert result.exit.executed_price == 95.0
    assert result.gross_return_pct == -5.0
    assert result.net_return_pct == -5.0
    assert result.position_size == 1.0
    assert result.exit.intraday_exit_evidence.first_hit is IntradayFirstHit.AMBIGUOUS


def test_formal_outcome_exit_is_only_an_adapter_to_execution_core(monkeypatch):
    observed = {}

    def fake_resolve(bars, config):
        observed["bars"] = bars
        observed["config"] = config
        return CausalExitResult(
            101.0,
            "2026-06-03",
            "horizon_close",
            2,
            IntradayExitEvidence(
                IntradayFirstHit.NOT_APPLICABLE,
                False,
                None,
            ),
        )

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

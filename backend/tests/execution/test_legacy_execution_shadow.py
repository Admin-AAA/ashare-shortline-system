"""Versioned legacy shadow adapter tests; never formal production path."""
from app.engine.backtest import BarInput, ExecutionPolicy, RecommendationInput
from app.engine.simulation import simulate
from app.services.legacy_execution_shadow import LEGACY_EXECUTION_ADAPTER_VERSION, simulate_legacy_shadow


def test_legacy_adapter_is_explicitly_versioned_and_preserves_head_entry_bar_semantics():
    policy = ExecutionPolicy(
        policy_id="legacy-test",
        version="1.0.0",
        entry_lookahead_days=1,
        horizon_days=2,
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
        commission_rate=0.0,
        stamp_tax_rate=0.0,
        entry_slippage_bps=0.0,
        exit_slippage_bps=0.0,
    )
    recommendation = RecommendationInput("000001", "2026-01-01", 100.0, "legacy-test")
    bars = (
        BarInput("000001", "2026-01-02", 100, 110, 90, 100),
        BarInput("000001", "2026-01-03", 100, 101, 99, 100),
    )

    legacy = simulate_legacy_shadow(recommendation, bars, policy)
    canonical = simulate(recommendation, bars, policy).trade

    assert LEGACY_EXECUTION_ADAPTER_VERSION == "engine-simulation@HEAD-9d81483"
    assert legacy.entry_date == legacy.exit_date == "2026-01-02"
    assert legacy.exit_reason == "stop_loss"
    assert canonical.entry_date == "2026-01-02"
    assert canonical.exit_date == "2026-01-03"


def test_legacy_adapter_is_read_only_dto_function():
    assert "Session" not in simulate_legacy_shadow.__annotations__.values()

from __future__ import annotations

from dataclasses import replace

from app.engine.backtest import Trade, TradeOutcome
from app.services.backtest.strategy_quality import (
    StrategyQualityConfig,
    StrategyTradeEvidence,
    apply_quality_gate,
    compute_quality_profile,
)


def _trade(
    code: str,
    *,
    signal_date: str = "2026-06-01",
    entry_date: str = "2026-06-02",
    exit_date: str = "2026-06-04",
    net: float = 3.0,
    position_size: float = 0.5,
) -> Trade:
    return Trade(
        code=code,
        signal_date=signal_date,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=10.0,
        exit_price=10.0 * (1 + net / 100),
        gross_return_pct=net + 0.07,
        net_return_pct=net,
        position_size=position_size,
        holding_days=2,
        outcome_label=TradeOutcome.SUCCESS if net > 0 else TradeOutcome.FAILED,
        exit_reason="take_profit_hit" if net > 0 else "stop_loss_gap",
    )


def _evidence(
    code: str,
    *,
    score: float = 90,
    regime: str = "强势",
    setup: str = "小盘主线共振",
    main_theme: bool = True,
    gap: float = 2.0,
    net: float = 3.0,
    industry: str = "半导体",
) -> StrategyTradeEvidence:
    trade = _trade(code, net=net)
    return StrategyTradeEvidence(
        trade=replace(
            trade,
            entry_price=10.0 * (1 + gap / 100),
            exit_price=10.0 * (1 + gap / 100) * (1 + net / 100),
        ),
        score=score,
        regime=regime,
        setup=setup,
        evidence_text=("主线题材顺风",),
        is_main_theme=main_theme,
        industry=industry,
        signal_price=10.0,
    )


def test_quality_gate_requires_score_regime_theme_setup_and_entry_gap() -> None:
    config = StrategyQualityConfig(
        strategy_id="small-cap-thematic-momentum",
        min_score=85,
        allowed_regimes=("强势",),
        require_main_theme=True,
        required_setup_keywords=("主线",),
    )
    result = apply_quality_gate(
        (
            _evidence("000001"),
            _evidence("000002", score=84),
            _evidence("000003", regime="弱势"),
            _evidence("000004", main_theme=False),
            replace(_evidence("000005", setup="普通观察"), evidence_text=()),
            _evidence("000006", gap=7),
        ),
        config,
    )
    assert [item.trade.code for item in result.selected] == ["000001"]
    assert result.rejected_reasons["score_below_minimum"] == 1
    assert result.rejected_reasons["regime_not_allowed"] == 1
    assert result.rejected_reasons["main_theme_required"] == 1
    assert result.rejected_reasons["setup_required"] == 1
    assert result.rejected_reasons["entry_gap_too_high"] == 1


def test_portfolio_caps_limit_overlapping_exposure() -> None:
    config = StrategyQualityConfig(
        strategy_id="small-cap-thematic-momentum",
        min_score=85,
        allowed_regimes=("强势",),
        require_main_theme=True,
        required_setup_keywords=("主线",),
        strategy_cap=0.6,
        gross_exposure_cap=0.8,
    )
    result = apply_quality_gate(
        tuple(_evidence(f"00000{index}", industry=str(index)) for index in range(1, 5)),
        config,
    )
    assert len(result.selected) == 2
    assert sum(item.trade.position_size for item in result.selected) == 0.6
    assert result.portfolio_rejected == 2


def test_quality_profile_keeps_score_calibration_and_release_gate_explicit() -> None:
    config = StrategyQualityConfig(
        strategy_id="small-cap-thematic-momentum",
        min_score=85,
        allowed_regimes=("强势",),
        require_main_theme=True,
        required_setup_keywords=("主线",),
    )
    profile = compute_quality_profile((_evidence("000001"),), config)
    assert profile["mode"] == "shadow"
    assert profile["release_ready"] is False
    assert set(profile["score_calibration"]["buckets"]) == {"0-69", "70-79", "80-84", "85+"}
    assert "stop_gap_diagnostics" in profile


def test_quality_profile_keeps_unexecuted_trade_zero_and_out_of_portfolio() -> None:
    trade = Trade(
        code="000001",
        signal_date="2026-06-01",
        entry_date=None,
        exit_date=None,
        entry_price=None,
        exit_price=None,
        gross_return_pct=0.0,
        net_return_pct=0.0,
        position_size=0.5,
        holding_days=0,
        outcome_label=TradeOutcome.NOT_EXECUTED,
        exit_reason="unfilled",
    )
    evidence = StrategyTradeEvidence(
        trade=trade,
        score=90,
        regime="强势",
        setup="小盘主线共振",
        is_main_theme=True,
        signal_price=10.0,
    )
    config = StrategyQualityConfig(
        strategy_id="small-cap-thematic-momentum",
        min_score=85,
        allowed_regimes=("强势",),
        require_main_theme=True,
        required_setup_keywords=("主线",),
    )

    profile = compute_quality_profile((evidence,), config)

    assert profile["raw_metrics"]["sample_count"] == 0
    assert profile["raw_metrics"]["total_return_pct"] == 0.0
    assert profile["gate"]["selected_count"] == 0
    assert profile["gate"]["rejected_reasons"] == {"not_executed": 1}

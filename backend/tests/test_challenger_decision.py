from app.services.backtest.challenger_decision import (
    PerformanceSnapshot,
    evaluate_challenger,
)


def _snapshot(**overrides) -> PerformanceSnapshot:
    values = {
        "average_net_return_pct": 1.0,
        "cumulative_net_return_pct": 10.0,
        "win_rate_pct": 50.0,
        "profit_factor": 1.2,
        "max_drawdown_pct": -8.0,
    }
    values.update(overrides)
    return PerformanceSnapshot(**values)


def test_publishes_when_both_returns_and_one_auxiliary_improve_without_regression():
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=1.2,
            cumulative_net_return_pct=12.0,
            win_rate_pct=52.0,
        ),
    )

    assert decision.should_publish is True
    assert decision.decision == "PRODUCTION"
    assert decision.regressions == ()


def test_rejects_single_metric_improvement():
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(win_rate_pct=52.0),
    )

    assert decision.should_publish is False
    assert decision.decision == "FALSIFIED"


def test_rejects_when_coverage_reduction_lowers_portfolio_return():
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=2.0,
            cumulative_net_return_pct=8.0,
            win_rate_pct=60.0,
            profit_factor=1.8,
            max_drawdown_pct=-5.0,
        ),
    )

    assert decision.should_publish is False
    assert decision.deltas["cumulative_net_return_pct"] == -2.0


def test_rejects_when_an_auxiliary_metric_regresses():
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=1.2,
            cumulative_net_return_pct=12.0,
            win_rate_pct=52.0,
            profit_factor=0.9,
        ),
    )

    assert decision.should_publish is False
    assert decision.regressions == ("profit_factor",)


def test_tolerance_only_neutralizes_float_noise():
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=1.01,
            cumulative_net_return_pct=10.01,
            win_rate_pct=50.01,
        ),
    )

    assert decision.should_publish is False
    assert decision.improvements == ()


def test_less_negative_drawdown_is_an_improvement():
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=1.2,
            cumulative_net_return_pct=12.0,
            max_drawdown_pct=-6.0,
        ),
    )

    assert decision.should_publish is True
    assert "max_drawdown_pct" in decision.improvements

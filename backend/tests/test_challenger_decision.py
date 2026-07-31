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


def test_rejects_when_two_auxiliary_metrics_regress():
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=1.2,
            cumulative_net_return_pct=12.0,
            win_rate_pct=52.0,
            profit_factor=0.9,
            max_drawdown_pct=-9.0,
        ),
    )

    assert decision.should_publish is False
    assert decision.regressions == ("profit_factor", "max_drawdown_pct")


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


# ── 收益 ↔ 胜率权衡 ─────────────────────────────────────────────────────


def test_publishes_when_return_slightly_drops_but_winrate_jumps():
    """平均单笔收益小幅下降、累计收益改善、胜率大幅提升 → 允许发布。"""
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=0.6,   # -0.4pp, within cap
            cumulative_net_return_pct=12.0,
            win_rate_pct=55.0,            # +5pp >= qualifying gain
        ),
    )

    assert decision.should_publish is True
    assert decision.decision == "PRODUCTION"


def test_rejects_return_drop_beyond_cap_even_with_winrate_jump():
    """收益下降超过 1pp 上限，即使胜率大幅提升也不发布。"""
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=-0.5,  # -1.5pp, beyond cap
            cumulative_net_return_pct=12.0,
            win_rate_pct=55.0,
        ),
    )

    assert decision.should_publish is False


def test_rejects_return_drop_without_qualifying_winrate_gain():
    """收益下降但胜率提升不足 3pp → 不发布。"""
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=0.6,   # -0.4pp
            cumulative_net_return_pct=12.0,
            win_rate_pct=51.0,            # +1pp, below qualifying gain
        ),
    )

    assert decision.should_publish is False


def test_publishes_when_pf_compensates_return_drop():
    """PF 显著提升也可补偿收益小幅下降。"""
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=0.6,   # -0.4pp
            cumulative_net_return_pct=12.0,
            profit_factor=1.4,            # +0.2 >= qualifying gain
        ),
    )

    assert decision.should_publish is True


def test_single_auxiliary_regression_allowed_when_another_improves():
    """辅助指标单项目退化 + 另一项改善 → 允许发布。"""
    decision = evaluate_challenger(
        _snapshot(),
        _snapshot(
            average_net_return_pct=1.2,
            cumulative_net_return_pct=12.0,
            win_rate_pct=52.0,
            max_drawdown_pct=-9.0,        # one aux regression
        ),
    )

    assert decision.should_publish is True
    assert decision.regressions == ("max_drawdown_pct",)

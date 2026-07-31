import json
from types import SimpleNamespace

import pytest

from scripts.si_sequential_champion_replay import (
    _decision_regime,
    _in_production_universe,
    _portfolio_metrics,
)


def test_portfolio_metrics_treats_same_exit_day_as_equal_weight_portfolio():
    snapshot, details = _portfolio_metrics(
        [
            {"net_return": 2.0, "exit_date": "2026-01-02", "trade_date": "2026-01-01"},
            {"net_return": 0.0, "exit_date": "2026-01-02", "trade_date": "2026-01-01"},
            {"net_return": -1.0, "exit_date": "2026-01-03", "trade_date": "2026-01-02"},
        ]
    )

    # Day returns are +1% and -1%; cumulative is 1.01 * 0.99 - 1.
    assert snapshot.cumulative_net_return_pct == pytest.approx(-0.01)
    assert snapshot.max_drawdown_pct == pytest.approx(-1.0)
    assert details["exit_days"] == 2


def test_portfolio_metrics_empty_population_returns_n0_placeholder():
    """Empty population returns n=0 placeholder instead of crashing, so
    regime slices with no trades can be reported as 'no samples'."""
    snapshot, details = _portfolio_metrics([])

    assert details["n"] == 0
    assert details["trade_days"] == 0
    assert snapshot.average_net_return_pct == 0.0
    assert snapshot.win_rate_pct == 0.0
    assert snapshot.profit_factor == 0.0


def test_production_universe_filter_uses_current_20cm_setting():
    assert _in_production_universe("300001") is True
    assert _in_production_universe("688001") is True
    assert _in_production_universe("600001") is False


def test_decision_regime_prefers_score_line_meta_over_market_context():
    rec = SimpleNamespace(
        feature_snapshot_json=json.dumps(
            {"score_line_meta": {"decision_regime": "弱势"}}
        ),
        market_context_json=json.dumps({"breadth": {"regime": "结构性强势"}}),
    )

    assert _decision_regime(rec) == "弱势"


def test_decision_regime_uses_persisted_decision_time_market_context_for_old_rows():
    rec = SimpleNamespace(
        feature_snapshot_json=json.dumps({"market_regime": "事后回溯值"}),
        market_context_json=json.dumps({"breadth": {"regime": "结构性强势"}}),
    )

    assert _decision_regime(rec) == "结构性强势"

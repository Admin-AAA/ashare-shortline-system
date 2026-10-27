"""结算止损线与回测同口径：买入成交价（含滑点）×（1 − 5.5%），不看推荐快照里的止损价、不取整（2026-10-27 用户定）。"""
from types import SimpleNamespace

import pytest

from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY
from app.services import outcome_simulation as osim


def test_stop_formula_equals_backtest_policy():
    executed = 146.49635
    expected = executed * (1 - DEFAULT_EXECUTION_POLICY.stop_loss_pct / 100)  # execution_core.execute_trade 同式
    assert osim._backtest_stop_loss_price(executed) == pytest.approx(expected, abs=1e-9)
    assert osim._backtest_stop_loss_price(0.0) == 0.0


def _bar(day, o, h, l, c, pct=0.0):
    return SimpleNamespace(code="600000", trade_date=day, open=o, high=h, low=l, close=c,
                           volume=1e5, amount=1e8, pct_chg=pct)


def test_settlement_ignores_snapshot_stop_and_uses_executed_entry():
    # 快照止损 9.43（−5.7%）比回测宽；入场次日最低 9.44：回测止损线 ≈9.454 被击穿，旧口径（9.43）不会
    snap = '{"entry_mode": "next_open", "stop_loss": 9.43}'
    rec = SimpleNamespace(recommendation_id="r", code="600000", name="测试", trade_date="2026-10-15",
                          recommend_price=10.0, bucket="chen-xiaoqun-skill", recommended_at=None,
                          feature_snapshot_json=snap, run_id="x")
    bars = [_bar("2026-10-16", 10.0, 10.1, 9.9, 10.0), _bar("2026-10-19", 9.8, 9.9, 9.44, 9.6, -4.0)]
    out = osim._simulate_outcome(rec, bars, [b.trade_date for b in bars], horizon_days=5)
    executed = out["entry_price"]
    assert out["stop_loss_price"] == round(executed * (1 + osim.DEFAULT_STOP_LOSS_PCT / 100), 2)
    assert str(out["exit_reason"]).startswith("stop_loss") and out["exit_date"] == "2026-10-19"

"""data_gap_pure / backtest_validation / outcome_settlement 纯函数小测试（无 DB）。"""
from types import SimpleNamespace

from app.services import backtest_validation as bv
from app.services import data_gap_pure as dg
from app.services import outcome_settlement as osl


# ── data_gap_pure ─────────────────────────────────────────────
def test_kline_fetch_status():
    f = dg._kline_fetch_status
    assert f(row_count=0, latest_trade_date=None, quote_date="2026-10-23", min_bars=30) == "empty"
    assert f(row_count=10, latest_trade_date="2026-10-23", quote_date="2026-10-23", min_bars=30) == "short_history"
    assert f(row_count=60, latest_trade_date="2026-10-22", quote_date="2026-10-23", min_bars=30) == "stale"
    assert f(row_count=60, latest_trade_date="2026-10-23", quote_date="2026-10-23", min_bars=30) == "completed"


def test_local_pool_sync_status():
    f = dg._local_pool_sync_status
    assert f(target_count=0, failed_count=0, unresolved_count=0) == "no_gap"
    assert f(target_count=3, failed_count=3, unresolved_count=0) == "failed"
    assert f(target_count=3, failed_count=1, unresolved_count=0) == "partial_completed"
    assert f(target_count=3, failed_count=0, unresolved_count=2) == "partial_completed"
    assert f(target_count=3, failed_count=0, unresolved_count=0) == "completed"


def test_numeric_helpers():
    assert dg._numeric_delta(5, 2) == 3
    assert dg._numeric_delta(None, 2) is None
    assert dg._numeric_delta("x", 2) is None
    assert dg._float(None) == 0.0 and dg._float("1.5") == 1.5 and dg._float("bad") == 0.0
    assert dg._optional_float("") is None and dg._optional_float("2") == 2.0 and dg._optional_float("bad") is None
    assert dg._average([]) is None and dg._average([1, 2]) == 1.5
    assert dg._trade_date_variants("2026-10-23") == ["2026-10-23", "20261023"]
    assert dg._trade_date_variants(None) == []


# ── outcome_settlement ────────────────────────────────────────
def _o(**kw):
    base = dict(outcome_label="success", entry_triggered=True, label_source="auto", executed_return=None, close_return=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_normalized_outcome_label():
    n = osl.normalized_outcome_label
    assert n(None) == osl.OUTCOME_PENDING
    assert n(_o(outcome_label="pending")) == osl.OUTCOME_PENDING
    assert n(_o(outcome_label="not_triggered")) == osl.OUTCOME_MISSED
    assert n(_o(outcome_label="risk_hit")) == osl.OUTCOME_FAILED
    assert n(_o(outcome_label="success", entry_triggered=False)) == osl.OUTCOME_MISSED
    assert n(_o(outcome_label="success")) == osl.OUTCOME_SUCCESS
    assert n(_o(outcome_label="weird")) == osl.OUTCOME_FAILED
    assert n(_o(outcome_label="success", entry_triggered=False, label_source="manual")) == osl.OUTCOME_SUCCESS


def test_trade_helpers():
    assert osl.is_outcome_labeled(_o(outcome_label="pending")) is False
    assert osl.is_outcome_labeled(_o()) is True
    assert osl.is_trade_executed(None) is False and osl.is_trade_executed(_o()) is True
    assert osl.resolved_trade_return(None) == 0.0
    assert osl.resolved_trade_return(_o(executed_return=1.5, close_return=9)) == 1.5
    assert osl.resolved_trade_return(_o(executed_return=None, close_return=2.0)) == 2.0
    assert osl._is_20cm_code("300750") and osl._is_20cm_code("688981") and not osl._is_20cm_code("600000")
    assert osl._net_pl_ratio([1.0, 1.0]) is None  # 无亏损侧
    assert osl._net_pl_ratio([3.0, -2.0]) is not None


# ── backtest_validation ───────────────────────────────────────
def test_drawdown_worse_by_and_low_confidence():
    assert bv._drawdown_worse_by({"max_drawdown": -10}, {"max_drawdown": -15}) == 5.0
    assert bv._drawdown_worse_by({"max_drawdown": -10}, {"max_drawdown": -5}) == 0.0
    assert bv._is_low_confidence({"trade_count": 0, "sample_confidence": "normal"}) is True
    assert bv._is_low_confidence({"trade_count": 10_000, "sample_confidence": "low"}) is True
    assert bv._is_low_confidence({"trade_count": 10_000, "sample_confidence": "normal"}) is False


def test_validation_fallback_reason():
    assert bv._validation_fallback_reason("walk_forward") is None
    assert "time-split" in bv._validation_fallback_reason("time_split")
    assert "全量" in bv._validation_fallback_reason("aggregate")


def test_pick_decision_scope_falls_back_to_aggregate():
    r = bv._pick_decision_scope({}, {}, {}, {}, {"trade_count": 1}, {"trade_count": 1})
    assert r["scope"] == "aggregate" and r["guardrail_notes"]

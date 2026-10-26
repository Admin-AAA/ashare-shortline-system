"""入场日检查与回测买入规则一一对应：窗口 2 日、可成交、一字涨停、开盘跳空 ≤6%。"""
from types import SimpleNamespace

from app.services.entry_day_checks import (
    ENTRY_WINDOW_TRADE_DAYS,
    MAX_ENTRY_GAP_PCT,
    check_entry_bar,
    first_day_blocked,
)
from app.services.execution_confirmation import _derive_decisions


def _bar(open_, high, low, close, pre_close=10.0, volume=100.0, amount=1e6):
    return {"open": open_, "high": high, "low": low, "close": close, "pre_close": pre_close,
            "volume": volume, "amount": amount}


def _decide(age, check, *, prior_day_blocked=False, closed=False):
    entry, _ = _derive_decisions(
        fresh=True, signal_age_trade_days=age, entry_window_closed=closed, prefilter_ok=True,
        signal={"hard_veto": False, "score": 70, "setup_match": "x"}, review=None,
        current_price=10.0, stop_loss=9.0, entry_check=check, prior_day_blocked=prior_day_blocked,
    )
    return entry["code"]


def test_policy_constants_match_backtest_policy():
    assert ENTRY_WINDOW_TRADE_DAYS == 2
    assert MAX_ENTRY_GAP_PCT == 6.0


def test_normal_open_passes():
    assert check_entry_bar("600000", _bar(10.1, 10.3, 9.9, 10.2), 10.0, session_closed=False).blocked is False


def test_gap_down_below_stop_still_passes():
    # 回测没有「入场前跌破推荐止损就不买」
    assert check_entry_bar("600000", _bar(9.0, 9.2, 8.9, 9.1), 10.0, session_closed=False).blocked is False


def test_gap_up_over_limit_blocked():
    check = check_entry_bar("600000", _bar(10.61, 10.8, 10.5, 10.7), 10.0, session_closed=False)
    assert check.blocked and check.rule_id == "entry_gap_up" and not check.inconclusive


def test_one_word_limit_up_intraday_is_inconclusive_and_final_after_close():
    sealed = _bar(11.0, 11.0, 11.0, 11.0)
    intraday = check_entry_bar("600000", sealed, 10.5, session_closed=False)
    closed = check_entry_bar("600000", sealed, 10.5, session_closed=True)
    assert intraday.rule_id == "entry_one_word_limit_up" and intraday.inconclusive
    assert closed.blocked and not closed.inconclusive


def test_limit_up_open_that_broke_is_buyable_like_backtest():
    # 开盘涨停但盘中开板：不是一字板，回测按开盘价买（开盘相对推荐价 4.8% ≤6%）
    assert check_entry_bar("600000", _bar(11.0, 11.0, 10.6, 10.8), 10.5, session_closed=False).blocked is False


def test_untradable_bar_blocked():
    assert check_entry_bar("600000", _bar(10.0, 10.0, 10.0, 10.0, volume=0, amount=0), 10.0, session_closed=False).rule_id == "entry_untradable"


def test_first_day_blocked_uses_closed_bar_rules():
    day_one_gap = SimpleNamespace(open=10.8, high=11.0, low=10.6, close=10.9, pct_chg=9.0, volume=1, amount=1)
    day_one_ok = SimpleNamespace(open=10.1, high=10.3, low=9.9, close=10.2, pct_chg=2.0, volume=1, amount=1)
    assert first_day_blocked("600000", day_one_gap, 10.0) is True
    assert first_day_blocked("600000", day_one_ok, 10.0) is False
    assert first_day_blocked("600000", None, 10.0) is True  # 停牌：回测跳过该日


def test_decision_state_machine_follows_two_day_window():
    ok = check_entry_bar("600000", _bar(10.1, 10.3, 9.9, 10.2), 10.0, session_closed=False)
    gap = check_entry_bar("600000", _bar(10.7, 10.8, 10.5, 10.7), 10.0, session_closed=False)
    assert _decide(1, ok) == "buy"
    assert _decide(1, gap) == "wait"                           # 第 1 日跳过，次日再确认
    assert _decide(2, gap, prior_day_blocked=True) == "cancel"  # 窗口最后一日仍跳空 → 取消
    assert _decide(2, ok, prior_day_blocked=True) == "buy"      # 第 1 日被挡，第 2 日可买
    assert _decide(2, ok, prior_day_blocked=False) == "expired"  # 第 1 日本可买，第 2 日不追
    assert _decide(3, ok, prior_day_blocked=True) == "expired"
    assert _decide(1, ok, closed=True) == "expired"

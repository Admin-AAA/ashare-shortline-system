"""第 7 条：一字涨停判定的幅度按日期取 ST 规则（2026-07-06 起主板 ST 10%，之前 5%），确认与结算同一函数。"""
from types import SimpleNamespace

from app.services.entry_day_checks import RULE_ONE_WORD, check_entry_bar
from app.services.outcome_simulation import _entry_execution_block_reason, _rec_is_st


def _bar(day, price, prev=10.0):
    pct = round((price / prev - 1) * 100, 4)
    return SimpleNamespace(trade_date=day, open=price, high=price, low=price, close=price,
                           pct_chg=pct, volume=1000, amount=1e6, pre_close=prev)


def test_st_main_board_5pct_one_word_before_rule_change():
    bar = _bar("2026-06-30", 10.5)
    assert check_entry_bar("600123", bar, 10.0, session_closed=True, is_st=True).rule_id == RULE_ONE_WORD
    assert _entry_execution_block_reason("600123", bar, is_st=True) is not None
    # 非 ST 主板 10%：+5% 一字不是涨停
    assert _entry_execution_block_reason("600123", bar, is_st=False) is None


def test_st_main_board_10pct_after_rule_change():
    assert _entry_execution_block_reason("600123", _bar("2026-07-10", 10.5), is_st=True) is None
    assert _entry_execution_block_reason("600123", _bar("2026-07-10", 11.0), is_st=True) is not None
    check = check_entry_bar("600123", _bar("2026-07-10", 10.5), 10.0, session_closed=True, is_st=True)
    assert check.rule_id != RULE_ONE_WORD


def test_live_bar_uses_given_trade_date():
    live = {"open": 10.5, "high": 10.5, "low": 10.5, "close": 10.5, "volume": 1000, "amount": 1e6, "pre_close": 10.0}
    assert check_entry_bar("600123", live, 10.0, session_closed=True, is_st=True,
                           trade_date="2026-06-30").rule_id == RULE_ONE_WORD
    assert check_entry_bar("600123", live, 10.0, session_closed=True, is_st=True,
                           trade_date="2026-10-27").rule_id != RULE_ONE_WORD


def test_rec_is_st_from_name_or_snapshot():
    assert _rec_is_st(SimpleNamespace(name="*ST海华"), {}) is True
    assert _rec_is_st(SimpleNamespace(name="海华科技"), {"is_st": True}) is True
    assert _rec_is_st(SimpleNamespace(name="海华科技"), {}) is False

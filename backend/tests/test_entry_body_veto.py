from app.services.entry_body_veto import body_veto_blocked, body_veto_enabled_for, signal_body_mid
from app.services.entry_day_checks import RULE_PASS, check_entry_bar
from app.services.entry_body_veto import RULE_BODY_VETO

SIG = {"open": 10.0, "close": 11.0}  # mid 10.5


def _bar(o):
    return {"open": o, "high": o * 1.02, "low": o * 0.98, "close": o, "pre_close": 11.0, "volume": 1e6, "amount": 1e7}


def test_mid_and_block():
    assert signal_body_mid(SIG) == 10.5
    assert body_veto_blocked(SIG, 10.4) == (True, 10.5)
    assert body_veto_blocked(SIG, 10.5)[0] is False
    assert body_veto_blocked(None, 10.0)[0] is False
    assert body_veto_blocked({"open": 0, "close": 11}, 1.0)[0] is False


def test_enabled_only_for_oversold():
    assert body_veto_enabled_for("oversold-capital-reversal")
    assert not body_veto_enabled_for("chao-gu-yang-jia")
    assert not body_veto_enabled_for(None)


def test_check_entry_bar_integration():
    r = check_entry_bar("600000", _bar(10.4), 11.0, session_closed=True, strategy_id="oversold-capital-reversal", signal_bar=SIG)
    assert r.blocked and r.rule_id == RULE_BODY_VETO
    assert check_entry_bar("600000", _bar(10.6), 11.0, session_closed=True, strategy_id="oversold-capital-reversal", signal_bar=SIG).rule_id == RULE_PASS
    # 未声明的策略 / 未传信号日 K：行为不变
    assert check_entry_bar("600000", _bar(10.4), 11.0, session_closed=True, strategy_id="other", signal_bar=SIG).rule_id == RULE_PASS
    assert check_entry_bar("600000", _bar(10.4), 11.0, session_closed=True).rule_id == RULE_PASS

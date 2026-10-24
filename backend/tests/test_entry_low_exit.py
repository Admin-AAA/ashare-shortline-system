from types import SimpleNamespace as NS

from app.services import entry_low_exit as e


def _bar(d, close, low=None, vol=1.0):
    low = close * 0.99 if low is None else low
    return NS(trade_date=d, open=close, high=close * 1.01, low=low, close=close, pct_chg=0.0, volume=vol)


def test_first_entry_low_break_bounds():
    bars = [
        _bar("2026-01-02", 10, low=9.5),
        _bar("2026-01-05", 9.4, low=9.3),  # close 9.4 < entry low 9.5
        _bar("2026-01-06", 9.0, low=8.9),
    ]
    assert e.first_entry_low_break("600000", bars, "2026-01-02", "2026-01-07") == "2026-01-05"
    assert e.first_entry_low_break("600000", bars, "2026-01-02", "2026-01-05") is None
    bars[1].volume = 0
    assert e.first_entry_low_break("600000", bars, "2026-01-02", "2026-01-07") == "2026-01-06"


def test_apply_entry_low_backtest_truncates_and_relabels():
    trade = NS(executed=True, code="600000", entry_date="2026-01-02", exit_date="2026-01-08")
    bars = [
        _bar("2026-01-02", 10, low=9.5),
        _bar("2026-01-05", 9.4, low=9.3),
        _bar("2026-01-06", 9.0, low=8.9),
    ]
    seen = {}

    def resim(cut):
        seen["cut"] = [b.trade_date for b in cut]
        return NS(executed=True, code="600000", entry_date="2026-01-02", exit_date="2026-01-05", exit_reason="horizon_close")

    import dataclasses
    orig = dataclasses.replace
    try:
        e.dc.replace = lambda o, **kw: NS(**{**vars(o), **kw})
        out = e.apply_entry_low_backtest(trade, bars, True, resim)
    finally:
        e.dc.replace = orig
    assert seen["cut"] == ["2026-01-02", "2026-01-05"]
    assert out.exit_reason == e.ENTRY_LOW_REASON


def test_disabled_is_noop():
    t = NS(executed=True)
    assert e.apply_entry_low_backtest(t, [], False, lambda c: None) is t


def test_yaml_modes_loaded():
    modes = e.load_entry_low_modes()
    for sid in (
        "chao-gu-yang-jia-skill",
        "chen-xiaoqun-skill",
        "oversold-capital-reversal",
        "sector-relative-strength-leader",
    ):
        assert modes.get(sid) == "close", sid
    assert e.entry_low_enabled_for("high-range-breakout-momentum") is False

from types import SimpleNamespace as NS

from app.services import ma_break_exit as m


def _row(d, c, ma5, ma10=None, vol=1.0):
    return NS(trade_date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, pct_chg=0.0, volume=vol, ma5=ma5, ma10=ma10 or ma5)


def test_first_break_date_window_and_bounds():
    rows = [_row("2026-01-02", 10, 9), _row("2026-01-05", 9, 9.5), _row("2026-01-06", 8, 9.5)]
    # 入场日当天不算；第一天收盘<MA5 的日子
    assert m.first_break_date("600000", rows, 5, "2026-01-02", "2026-01-07") == "2026-01-05"
    # 原离场日及之后不算
    assert m.first_break_date("600000", rows, 5, "2026-01-02", "2026-01-05") is None
    # 无量不可卖跳过
    rows[1].volume = 0
    assert m.first_break_date("600000", rows, 5, "2026-01-02", "2026-01-07") == "2026-01-06"


def test_apply_ma_break_backtest_truncates_and_relabels():
    trade = NS(executed=True, code="600000", entry_date="2026-01-02", exit_date="2026-01-08")
    rows = [_row("2026-01-05", 9, 9.5)]
    bars = [NS(trade_date=d) for d in ("2026-01-02", "2026-01-05", "2026-01-06")]
    seen = {}

    def resim(cut):
        seen["cut"] = [b.trade_date for b in cut]
        return NS(executed=True, code="600000", entry_date="2026-01-02", exit_date="2026-01-05", exit_reason="horizon_close")

    import dataclasses
    orig = dataclasses.replace
    try:
        m.dc.replace = lambda o, **kw: NS(**{**vars(o), **kw})
        out = m.apply_ma_break_backtest(trade, bars, 5, resim, rows_loader=lambda c: rows)
    finally:
        m.dc.replace = orig
    assert seen["cut"] == ["2026-01-02", "2026-01-05"]
    assert out.exit_reason == m.MA_BREAK_REASON


def test_window_zero_is_noop():
    t = NS(executed=True)
    assert m.apply_ma_break_backtest(t, [], 0, lambda c: None) is t


def test_yaml_windows_loaded():
    assert m.load_ma_break_windows().get("sector-relative-strength-leader") == 5
    assert m.ma_break_window_for("chen-xiaoqun-skill") == 0

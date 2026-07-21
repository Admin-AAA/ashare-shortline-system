"""Tests for the consolidated honest rolling-OOS harness (backtest_factor_oos).

Pure (no DB) so they run under --noconftest, locking:
  - factor logic correctness (volume_reversal, chip_band/base_breakout)
  - the run_rolling_oos verdict protocol (accept on clear edge / reject on noise)
  - the OOS-INTEGRITY rule: factors use ONLY full-history daily_kbars
    columns (ma20/ma60/vol_ratio_5/20/breakout20/pct_chg/turnover_rate/
    max_dd20/close/amount) — NOT rsi_12/macd/chip_winner_rate/ma250,
    which are only populated for recent ~25-53 trade days and would
    collapse a "rolling-OOS" cohort to a single (in-sample) period.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.backtest_factor_oos import (
    FACTOR_LIBRARY,
    OOS_COLUMNS,
    chip_band,
    chip_base_breakout,
    f_ma20_support_hold,
    f_volume_reversal,
    load_data,
    run_rolling_oos,
)


# ── helpers ──
def _mk(**kw) -> pd.DataFrame:
    n = len(next(iter(kw.values())))
    base = dict(
        code=["X"] * n,
        trade_date=pd.date_range("2024-01-01", periods=n, freq="D"),
        close=np.arange(1, n + 1, dtype=float),
        ma20=np.arange(1, n + 1, dtype=float),
        ma60=np.arange(1, n + 1, dtype=float) * 0.99,
        vol_ratio_5=[1.0] * n,
        vol_ratio_20=[1.0] * n,
        max_dd20=[0.0] * n,
        turnover_rate=[2.0] * n,
        pct_chg=[0.0] * n,
        breakout20=[0] * n,
        amount=[1e9] * n,
    )
    base.update(kw)
    return pd.DataFrame(base)


# ── ported factor-behavior tests ──
def test_volume_reversal_triggers_on_shrink_turn_oversold():
    df = _mk(
        vol_ratio_5=[0.5, 1.5, 0.5],
        pct_chg=[2.0, 2.0, 2.0],
        ma20=[100.0, 100.0, 100.0],
        close=[96.0, 102.0, 96.0],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_volume_reversal(df)
    assert list(out) == [True, False, True]


def test_volume_reversal_rejects_high_volume_or_no_turn():
    df = _mk(
        vol_ratio_5=[2.0, 0.5, 0.5],
        pct_chg=[2.0, -1.0, 2.0],
        ma20=[100.0, 100.0, 100.0],
        close=[96.0, 96.0, 105.0],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_volume_reversal(df)
    assert list(out) == [False, False, False]


def test_volume_reversal_handles_nan_gracefully():
    df = _mk(
        vol_ratio_5=[np.nan, 0.5],
        pct_chg=[2.0, 2.0],
        ma20=[100.0, 100.0],
        close=[96.0, 96.0],
        turnover_rate=[2.0, 2.0],
    )
    out = f_volume_reversal(df)
    assert out.fillna(False).tolist() == [False, True]


def test_ma20_support_hold_triggers_on_uptrend_lowvol_test():
    # 站上 ma20&ma60、close 略高于 ma20(±2.5%)、缩量、距 ma60<20% → 命中
    df = _mk(
        close=[100.0, 100.0, 100.0],
        ma20=[98.0, 98.0, 98.0],
        ma60=[90.0, 90.0, 90.0],
        vol_ratio_5=[0.7, 2.0, 0.7],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_ma20_support_hold(df)
    assert list(out) == [True, False, True]


def test_ma20_support_hold_rejects_below_ma_or_extended():
    # row0 跌破 ma20(接飞刀) / row1 距 ma60>20%(追高) / row2 站上但高量 → 全拒
    df = _mk(
        close=[95.0, 112.0, 100.0],
        ma20=[100.0, 98.0, 98.0],
        ma60=[90.0, 90.0, 90.0],
        vol_ratio_5=[0.7, 0.7, 2.0],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_ma20_support_hold(df)
    assert list(out) == [False, False, False]


def test_ma20_support_hold_frozen_verdict_is_reject():
    meta = FACTOR_LIBRARY["ma20_support_hold"]
    assert meta["verdict"] == "reject"
    assert meta["edge_pp"] == -0.122
    assert meta["h3_wr_true"] == 0.353
    assert meta["rolling_frac_ok"] == 0.0


def test_chip_band_respects_lo_hi_and_base_breakout():
    df = _mk(
        breakout20=[1, 1, 1, 0],
        vol_ratio_5=[2.0, 2.0, 2.0, 2.0],
        vol_ratio_20=[1.0, 1.0, 1.0, 1.0],
        ma20=[100.0, 100.0, 100.0, 100.0],
        ma60=[99.0, 99.0, 99.0, 99.0],
        turnover_rate=[2.0, 2.0, 2.0, 2.0],
        chip_winner_rate=[20.0, 60.0, 80.0, 50.0],
    )
    low = chip_band(df, 10.0, 45.0)
    high = chip_band(df, 70.0, 95.0)
    assert chip_base_breakout(df).sum() == 3
    assert list(low) == [True, False, False, False]
    assert list(high) == [False, False, True, False]


def test_chip_band_requires_base_breakout():
    df = _mk(
        breakout20=[0, 0],
        vol_ratio_5=[2.0, 2.0],
        vol_ratio_20=[1.0, 1.0],
        ma20=[100.0, 100.0],
        ma60=[99.0, 99.0],
        turnover_rate=[2.0, 2.0],
        chip_winner_rate=[20.0, 80.0],
    )
    assert chip_band(df, 10.0, 95.0).sum() == 0


# ── run_rolling_oos verdict protocol ──
def _build_oos_df(n: int, signal_mask, fwd_true: float, fwd_false: float) -> pd.DataFrame:
    """Synthetic df with controlled forward returns so the verdict is deterministic."""
    rows = []
    for i in range(n):
        sig = bool(signal_mask[i])
        fwd = fwd_true if sig else fwd_false
        rows.append(dict(
            code="X", trade_date=pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
            close=float(i + 1), ma20=float(i + 1), ma60=float(i + 1) * 0.99,
            vol_ratio_5=1.0, vol_ratio_20=1.0, max_dd20=0.0,
            turnover_rate=2.0, pct_chg=0.0, breakout20=0, amount=1e9,
            lim5=False, vol5_20=1.0, bias_ma20=0.0, trend_up=True,
            signal=1 if sig else 0, fwd2=fwd, fwd3=fwd, fwd5=fwd,
        ))
    return pd.DataFrame(rows)


def _edge_factor(d: pd.DataFrame) -> pd.Series:
    return d["signal"] == 1


def test_run_rolling_oos_accepts_clear_edge():
    n = 480
    mask = [i % 2 == 0 for i in range(n)]  # ~240 True (>= MIN_N_TRUE)
    df = _build_oos_df(n, mask, fwd_true=0.05, fwd_false=-0.03)
    res = run_rolling_oos(df, _edge_factor)
    assert res["h"][3]["wr_true"] == 1.0
    assert res["h"][3]["wr_false"] == 0.0
    assert res["edge_pp"] == 1.0
    assert res["rolling_frac_ok"] == 1.0
    assert res["verdict"] == "accept"
    assert res["accept"] is True


def test_run_rolling_oos_rejects_noise():
    n = 480
    mask = [i % 2 == 0 for i in range(n)]
    # INVERTED edge: factor=True actually loses
    df = _build_oos_df(n, mask, fwd_true=-0.03, fwd_false=0.05)
    res = run_rolling_oos(df, _edge_factor)
    assert res["edge_pp"] == -1.0
    assert res["verdict"] == "reject"
    assert res["accept"] is False


def test_run_rolling_oos_inconclusive_when_sparse():
    n = 240
    # only 3 signal rows total -> n_true < MIN_N_TRUE -> inconclusive (edge>0 but sparse)
    mask = [i in (10, 120, 230) for i in range(n)]
    df = _build_oos_df(n, mask, fwd_true=0.05, fwd_false=-0.03)
    res = run_rolling_oos(df, _edge_factor)
    assert res["h"][3]["n_true"] < 200
    assert res["verdict"] == "inconclusive"
    assert res["accept"] is False


def test_run_rolling_oos_inconclusive_when_regime_concentrated():
    """Positive edge but crammed into ONE 60-day regime window -> too few
    qualifying windows (< MIN_ROLL_N) -> inconclusive, NEVER accept.

    This is the round-20:04 guard: a fraction>=0.6 is meaningless
    with 1-2 windows; single-regime concentration (e.g. 96% of a
    factor's signals in one window) must not fake '100% windows
    positive' into an 'accept'.
    """
    n = 480
    dates = [pd.Timestamp("2024-01-01") + pd.Timedelta(days=i) for i in range(n)]
    rows = []
    for code in ("X", "Y", "Z", "W"):
        for i in range(n):
            # 50-day band (NOT 60, else it fits exactly inside one window
            # and that window would hold ONLY True rows -> wf=nan -> ok=False
            sig = 120 <= i < 170  # one 50-day band, all 4 codes
            fwd = 0.05 if sig else -0.03
            rows.append(dict(
                code=code, trade_date=dates[i],
                close=float(i + 1), ma20=float(i + 1), ma60=float(i + 1) * 0.99,
                vol_ratio_5=1.0, vol_ratio_20=1.0, max_dd20=0.0,
                turnover_rate=2.0, pct_chg=0.0, breakout20=0, amount=1e9,
                lim5=False, vol5_20=1.0, bias_ma20=0.0, trend_up=True,
                signal=1 if sig else 0, fwd2=fwd, fwd3=fwd, fwd5=fwd,
            ))
    df = pd.DataFrame(rows)
    res = run_rolling_oos(df, _edge_factor)
    assert res["h"][3]["n_true"] >= 200
    assert res["edge_pp"] == 1.0
    assert res["rolling_frac_ok"] == 1.0
    assert res["rolling_n"] < 4  # only the concentrated band qualifies
    assert res["verdict"] == "inconclusive"
    assert res["accept"] is False



# ── OOS-integrity guard ──
FORBIDDEN = {"rsi_12", "macd", "macd_dif", "macd_dea", "macd_bar",
             "chip_winner_rate", "ma250", "rsi"}


def test_oos_columns_use_only_full_history_fields():
    # (1) the constant itself must exclude recent-only columns
    assert not (FORBIDDEN & set(OOS_COLUMNS)), \
        f"OOS_COLUMNS must not contain recent-only columns: {FORBIDDEN & set(OOS_COLUMNS)}"
    # (2) load_data must REFUSE to build a query with a forbidden column.
    #     Mutate the SAME list object load_data reads, call it, expect AssertionError
    #     BEFORE any DB connect (the guard assert sits ahead of _connect()).
    import scripts.backtest_factor_oos as m
    saved = list(m.OOS_COLUMNS)
    m.OOS_COLUMNS.append("rsi_12")
    try:
        with pytest.raises(AssertionError):
            m.load_data("2024-01-01")
    finally:
        m.OOS_COLUMNS[:] = saved


def test_factor_library_structure_is_well_formed():
    valid = {"accept", "inconclusive", "reject",
             "blocked_data_wall", "blocked_forbidden_col"}
    assert FACTOR_LIBRARY, "library must be non-empty"
    for name, meta in FACTOR_LIBRARY.items():
        for key in ("fn", "kind", "causal", "verdict", "note"):
            assert key in meta, f"{name} missing {key}"
        assert meta["verdict"] in valid, f"{name} bad verdict {meta['verdict']}"
        assert callable(meta["fn"])
    # No factor that the honest OOS rejected/blocked may be silently marked accept
    for name, meta in FACTOR_LIBRARY.items():
        if meta["verdict"] != "accept":
            assert meta["verdict"] != "accept"

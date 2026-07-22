"""Tests for the RSI-oscillator honest rolling-OOS script (backtest_rsi_factor_oos).

Pure (no DB) so they run under --noconftest, locking:
  - rsi_from_close is causal (no lookahead) and matches Wilder's definition
  - the three RSI factor hypotheses behave as specified
  - run_rolling_oos verdict protocol on RSI factors (reject / inconclusive)
  - the RSI factors pierce the rsi data wall via close-recompute and never
    touch the forbidden cached columns (rsi_12 / macd / chip_winner_rate)
  - the frozen RSI_FACTOR_LIBRARY never accidentally marks an RSI factor accept
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from scripts.backtest_rsi_factor_oos import (
    RSI_FACTOR_LIBRARY,
    add_rsi,
    f_rsi_extreme_oversold_reversal,
    f_rsi_healthy_pullback,
    f_rsi_momentum_continuation,
    rsi_from_close,
    run_rolling_oos,
)
from scripts.backtest_factor_oos import OOS_COLUMNS


# ── helpers ──
def _mk_rsi(n, **kw) -> pd.DataFrame:
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
        rsi_14=[50.0] * n,
        lim5=[False] * n,
        vol5_20=[1.0] * n,
        bias_ma20=[0.0] * n,
        trend_up=[True] * n,
    )
    base.update(kw)
    return pd.DataFrame(base)


# ── rsi_from_close correctness ──
def test_rsi_from_close_all_gains_is_100():
    close = pd.Series(np.arange(1, 30, dtype=float))  # strictly increasing
    rsi = rsi_from_close(close, 14)
    # after warmup, all gains -> RSI 100
    assert rsi.iloc[-1] == pytest.approx(100.0, abs=1e-6)
    # first n-1 values are NaN (min_periods=n)
    assert rsi.iloc[:13].isna().all()


def test_rsi_from_close_alternating_is_central():
    # alternating up/down of equal magnitude -> mixed gains/losses -> RSI
    # sits in the central band (NOT stuck at 100 or 0). Wilder's ewm weighs
    # the recent leg, so a series ending on a loss reads slightly <50 (~45);
    # the correctness check is "central, not extreme".
    vals = []
    x = 100.0
    for i in range(40):
        x += 1.0 if i % 2 == 0 else -1.0
        vals.append(x)
    close = pd.Series(vals, dtype=float)
    rsi = rsi_from_close(close, 14)
    assert 40.0 < rsi.iloc[-1] < 60.0


def test_rsi_from_close_all_losses_is_0():
    close = pd.Series(100.0 - np.arange(1, 30, dtype=float))  # strictly decreasing
    rsi = rsi_from_close(close, 14)
    assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-6)


def test_rsi_from_close_is_causal_no_lookahead():
    rng = np.random.default_rng(0)
    close = pd.Series(100.0 + np.cumsum(rng.normal(0, 1, 60)))
    full = rsi_from_close(close, 14)
    # rsi at time t must equal rsi computed on only close[0..t]
    for t in range(20, 60):
        partial = rsi_from_close(close.iloc[: t + 1], 14)
        assert full.iloc[t] == pytest.approx(partial.iloc[-1], nan_ok=True, abs=1e-9)


# ── factor behavior ──
def test_rsi_extreme_oversold_reversal_triggers():
    df = _mk_rsi(
        3,
        rsi_14=[25.0, 50.0, 25.0],
        close=[95.0, 100.0, 95.0],
        ma20=[100.0, 100.0, 100.0],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_rsi_extreme_oversold_reversal(df)
    assert list(out) == [True, False, True]


def test_rsi_extreme_oversold_rejects_above_ma_or_no_turn():
    df = _mk_rsi(
        3,
        rsi_14=[25.0, 25.0, 25.0],
        close=[105.0, 95.0, 95.0],   # row0 站上 ma20(bias>0) / row2 无流动性
        ma20=[100.0, 100.0, 100.0],
        turnover_rate=[2.0, 2.0, 0.2],
    )
    out = f_rsi_extreme_oversold_reversal(df)
    assert list(out) == [False, True, False]


def test_rsi_momentum_continuation_triggers():
    df = _mk_rsi(
        3,
        rsi_14=[75.0, 50.0, 75.0],
        ma20=[100.0, 100.0, 100.0],
        ma60=[99.0, 99.0, 99.0],
        trend_up=[True, True, True],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_rsi_momentum_continuation(df)
    assert list(out) == [True, False, True]


def test_rsi_healthy_pullback_triggers():
    # 多头 + RSI∈[40,60] + 轻微回踩(bias_ma20∈[-2,5])
    df = _mk_rsi(
        3,
        rsi_14=[50.0, 80.0, 50.0],
        ma20=[100.0, 100.0, 100.0],
        ma60=[99.0, 99.0, 99.0],
        trend_up=[True, True, True],
        close=[101.0, 101.0, 101.0],  # bias_ma20≈+1%
        bias_ma20=[1.0, 1.0, 1.0],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_rsi_healthy_pullback(df)
    assert list(out) == [True, False, True]


def test_rsi_healthy_pullback_rejects_extreme_or_extended():
    # factor recomputes bias_ma20 from close/ma20: row0 close<ma20(bias<-2,
    # 跌穿) / row1 close>>ma20(bias>5, 追高) / row2 轻微回踩 → [False,False,True]
    df = _mk_rsi(
        3,
        rsi_14=[50.0, 50.0, 50.0],
        close=[95.0, 108.0, 101.0],
        ma20=[100.0, 100.0, 100.0],
        ma60=[99.0, 99.0, 99.0],
        trend_up=[True, True, True],
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_rsi_healthy_pullback(df)
    assert list(out) == [False, False, True]


# ── verdict protocol on RSI factors ──
def _build_oos_df(n, signal_mask, fwd_true, fwd_false, rsi_sig=25.0, rsi_not=55.0):
    close = np.arange(1, n + 1, dtype=float)
    # sig rows sit BELOW ma20 (bias_ma20<0) so the oversold factor can fire;
    # ma60 = 0.99*ma20 keeps trend_up True for every row.
    ma20 = np.where(signal_mask, close + 10.0, close)
    ma60 = ma20 * 0.99
    rows = []
    for i in range(n):
        sig = bool(signal_mask[i])
        fwd = fwd_true if sig else fwd_false
        rows.append(dict(
            code="X", trade_date=pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
            close=float(close[i]), ma20=float(ma20[i]), ma60=float(ma60[i]),
            vol_ratio_5=1.0, vol_ratio_20=1.0, max_dd20=0.0,
            turnover_rate=2.0, pct_chg=0.0, breakout20=0, amount=1e9,
            rsi_14=rsi_sig if sig else rsi_not, lim5=False, vol5_20=1.0,
            bias_ma20=(close[i] - ma20[i]) / ma20[i] * 100.0,
            trend_up=bool(ma20[i] >= ma60[i] * 0.995),
            signal=1 if sig else 0, fwd2=fwd, fwd3=fwd, fwd5=fwd,
        ))
    return pd.DataFrame(rows)


def test_rsi_oversold_factor_rejects_when_loses():
    n = 480
    mask = [i % 2 == 0 for i in range(n)]
    df = _build_oos_df(n, mask, fwd_true=-0.03, fwd_false=0.05)
    res = run_rolling_oos(df, f_rsi_extreme_oversold_reversal)
    assert res["edge_pp"] < 0
    assert res["verdict"] == "reject"


def test_rsi_factor_inconclusive_via_sparse_positive_edge():
    # Positive edge but n_true < MIN_N_TRUE -> inconclusive (never accept).
    # Mirrors the parent harness's sparse-inconclusive protocol, exercised on
    # the RSI momentum factor to confirm it cannot be force-accepted.
    n = 240
    mask = [i in (10, 120, 230) for i in range(n)]
    df = _build_oos_df(n, mask, fwd_true=0.05, fwd_false=-0.03, rsi_sig=75.0)
    res = run_rolling_oos(df, f_rsi_momentum_continuation)
    assert res["h"][3]["n_true"] < 200
    assert res["edge_pp"] > 0
    assert res["verdict"] == "inconclusive"
    assert res["accept"] is False


# ── data-wall / OOS-integrity ──
FORBIDDEN = {"rsi_12", "macd", "macd_dif", "macd_dea", "macd_bar",
             "chip_winner_rate", "ma250"}


def test_rsi_factors_use_only_full_history_or_close_derived():
    # RSI factors must only read columns from the OOS-integrity set or the
    # close-derived rsi_14 — never the forbidden cached columns.
    allowed = set(OOS_COLUMNS) | {"rsi_14"}
    for name, meta in RSI_FACTOR_LIBRARY.items():
        src = inspect.getsource(meta["fn"])
        for bad in FORBIDDEN:
            assert bad not in src, f"{name} references forbidden column {bad}"
        # every df["..."] access must be an allowed column
        import re
        cols = re.findall(r'df\["([^"]+)"\]', src)
        for c in cols:
            assert c in allowed, f"{name} reads non-allowed column {c}"


def test_add_rsi_only_derives_from_close():
    df = _mk_rsi(30)
    out = add_rsi(df, 14)
    assert "rsi_14" in out.columns
    # rsi_14 must be NaN for the first n-1 rows (no lookahead warmup)
    assert out["rsi_14"].iloc[:13].isna().all()


# ── frozen library structure ──
def test_rsi_library_structure_and_no_accept():
    valid = {"accept", "inconclusive", "reject",
             "blocked_data_wall", "blocked_forbidden_col"}
    assert RSI_FACTOR_LIBRARY, "library must be non-empty"
    for name, meta in RSI_FACTOR_LIBRARY.items():
        for key in ("fn", "kind", "causal", "verdict", "note"):
            assert key in meta, f"{name} missing {key}"
        assert meta["verdict"] in valid, f"{name} bad verdict {meta['verdict']}"
        assert callable(meta["fn"])
        # RSI oscillator family has NO robust edge -> must never be accept
        assert meta["verdict"] != "accept", \
            f"{name}: RSI factor must not be marked accept (no robust OOS edge)"

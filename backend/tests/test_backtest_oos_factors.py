"""Unit tests for the honest rolling-OOS backtest factor functions.

These test the PURE factor logic (no DB) so the methodology is locked and
regressions in factor definitions are caught. They also encode the critical
OOS-integrity rule: factors must use ONLY full-history daily_kbars fields
(ma20, vol_ratio_5/20, pct_chg, turnover_rate, close) — NOT ma60 / rsi_12 /
macd / chip_winner_rate, which are only populated for recent ~25-53 trade days
and would collapse a "rolling-OOS" cohort to a single period.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.backtest_volume_reversal import f_volume_reversal
from scripts.backtest_chip_winner_breakout import chip_band, base_breakout


def _mk(**kw) -> pd.DataFrame:
    n = len(next(iter(kw.values())))
    base = dict(
        code=["X"] * n,
        trade_date=pd.date_range("2024-01-01", periods=n, freq="D"),
        close=np.arange(1, n + 1, dtype=float),
        ma20=np.arange(1, n + 1, dtype=float),
        vol_ratio_5=[1.0] * n,
        vol_ratio_20=[1.0] * n,
        turnover_rate=[2.0] * n,
        pct_chg=[0.0] * n,
        chip_winner_rate=[50.0] * n,
        breakout20=[0] * n,
    )
    base.update(kw)
    return pd.DataFrame(base)


def test_volume_reversal_triggers_on_shrink_turn_oversold():
    df = _mk(
        vol_ratio_5=[0.5, 1.5, 0.5],          # 地量 / 正常 / 地量
        pct_chg=[2.0, 2.0, 2.0],               # 当日转强
        ma20=[100.0, 100.0, 100.0],
        close=[96.0, 102.0, 96.0],            # bias: -4% / +2% / -4%
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_volume_reversal(df)
    # row0: 地量+转强+bias-4% -> True ; row1: bias+2% -> False ; row2: 地量+转强+bias-4% -> True
    assert list(out) == [True, False, True]


def test_volume_reversal_rejects_high_volume_or_no_turn():
    df = _mk(
        vol_ratio_5=[2.0, 0.5, 0.5],           # 放量 / 地量 / 地量
        pct_chg=[2.0, -1.0, 2.0],              # 转强 / 下跌 / 转强
        ma20=[100.0, 100.0, 100.0],
        close=[96.0, 96.0, 105.0],            # bias -4% / -4% / +5%
        turnover_rate=[2.0, 2.0, 2.0],
    )
    out = f_volume_reversal(df)
    # 放量->False ; 下跌无转强->False ; bias+5%(不在ma20下方)->False
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
    # base breakout (vol_conf) True for rows 0-2 (breakout20==1 & vol5_20>=1.2 & trend_up)
    assert base_breakout(df).sum() == 3
    assert list(low) == [True, False, False, False]   # only row0 in [10,45]
    assert list(high) == [False, False, True, False]  # only row2 in [70,95]


def test_chip_band_requires_base_breakout():
    # no breakout -> band never True even if winner_rate in range
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

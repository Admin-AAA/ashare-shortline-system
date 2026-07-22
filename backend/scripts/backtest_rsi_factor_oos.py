"""Honest rolling-OOS backtest for RSI-oscillator short-term factors.

This is a SIBLING of backtest_factor_oos.py, reusing its engine
(run_rolling_oos / forward_returns / load_data) so methodology stays
identical and cannot drift.

═════════════════════════════════════════════════════════════
Why this script exists (T1 data-wall unblock):

  The parent harness froze rsi_12 / macd_* / chip_winner_rate / ma250 as
  "blocked_data_wall" because those daily_kbars COLUMNS are only populated
  for ~25-53 recent trade days — filtering on them collapses a rolling-OOS
  cohort to a single (in-sample) period.

  BUT RSI is a PURE FUNCTION OF CLOSE PRICES, and `close` is available for
  the FULL multi-year history (1.35M/1.36M rows, 2021→2026). Round-224602
  already proved RSI is recomputable from closes (the live system now
  recomputes rsi_6/12/24 from closes in market_data._build_stock_from_bars
  instead of trusting the corrupt cached column). So the RSI "data wall" is
  pierceable WITHOUT touching any forbidden column: we compute Wilder's RSI
  from `close` inside this script (exactly as the parent computes
  `bias_ma20` from close/ma20). The only columns read from daily_kbars are
  the OOS-integrity-locked full-history set; RSI is derived in pandas.

  This script therefore HONESTLY tests RSI-oscillator hypotheses over the
  full history where prior rounds could not. The chip_winner_rate / macd
  walls remain (they cannot be recomputed from close) — only the RSI part
  of the documented blocker is lifted.
═════════════════════════════════════════════════════════════

Method (identical to parent harness):
  - Forward return = close[t+H]/close[t]-1, H in {2,3,5}.
  - Cohort WR(forward>0) for factor=True vs factor=False.
  - Rolling 60-trading-day windows (step 30); accept only if WR(True)
    consistently > WR(False) across windows (no single-window overfit).
  - Verdict protocol: accept / inconclusive / reject / blocked_data_wall.

Each factor is a DISTINCT causal hypothesis (the three RSI regimes:
extreme-oversold reversal, overbought momentum continuation, healthy
pullback-in-uptrend), NOT grid-search variants of one idea.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

# Reuse the parent harness engine verbatim (no methodology fork).
from scripts.backtest_factor_oos import (
    HORIZONS,
    forward_returns,
    load_data,
    run_rolling_oos,
)


# ───────────────────────────────────────────────────────────────────────────
# RSI computed from close — full-history, no forbidden column, no lookahead.
# Uses ONLY past+current close (delta.diff() is t minus t-1; ewm is causal).
# ───────────────────────────────────────────────────────────────────────────

def rsi_from_close(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI from a close-price series. Causal (no lookahead).

    Boundary handling: all gains -> avg_loss==0 -> RSI=100; all losses ->
    avg_gain==0 -> RSI=0. Warmup rows (fewer than n prior bars) stay NaN.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing: alpha = 1/n, recursive, min_periods=n (NaN until warm).
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    denom = avg_loss.where(avg_loss > 0, np.nan)
    rs = avg_gain / denom
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi = rsi.mask(avg_loss == 0, 100.0)   # all gains -> RSI 100
    rsi = rsi.mask(avg_gain == 0, 0.0)     # all losses -> RSI 0
    return rsi


def add_rsi(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Compute rsi_n per code from close. Caller must pass a df already
    sorted by (code, trade_date) — load_data guarantees this."""
    df = df.copy()
    df[f"rsi_{n}"] = (
        df.groupby("code")["close"].transform(lambda s: rsi_from_close(s, n))
    )
    return df


# ───────────────────────────────────────────────────────────────────────────
# RSI factor hypotheses (distinct causal mechanisms)
# ───────────────────────────────────────────────────────────────────────────

def f_rsi_extreme_oversold_reversal(d: pd.DataFrame) -> pd.Series:
    """极端超卖反转：RSI(14)<30 (经典超卖，情绪杀跌极致) + 处于 ma20 下方
    (bias_ma20<0, 价格已深跌) + 有流动性。捕捉恐慌末端均值回归。
    与已 reject 的 volume_reversal 机制不同：本因子用 RSI(速度加权动量)而非
    价格水平(bias_ma20)，不要求当日放量转强(那是 contrarian 失败模式)——
    纯情绪极端反转假设。close 全历史可算，真多期 OOS。"""
    bias_ma20 = (d["close"] - d["ma20"]) / d["ma20"] * 100.0
    return (
        (d["rsi_14"] < 30.0)
        & (bias_ma20 < 0.0)
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


def f_rsi_momentum_continuation(d: pd.DataFrame) -> pd.Series:
    """超买动量延续：RSI(14)>70 (强势动量) + 多头结构(ma20>ma60) + 有流动性。
    顺势追强(非追高接飞刀)。机制=趋势跟随的 RSI 动量，与已 inconclusive 的
    platform_breakout_vol(要求 breakout20)不同：本因子不要求平台突破，只测
    "超买+趋势中是否延续"。close 全历史可算，真多期 OOS。"""
    return (
        (d["rsi_14"] > 70.0)
        & d["trend_up"]
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


def f_rsi_healthy_pullback(d: pd.DataFrame) -> pd.Series:
    """健康回踩(上升趋势中的非极端回踩)：多头结构(ma20>ma60) + RSI(14)∈[40,60]
    (未超买未超卖，健康区间) + 轻微回踩(close 略高于 ma20, bias_ma20∈[-2,5])。
    机制=上升趋势中买方在均线处防守、非极端动量下的续涨中继，与已 reject 的
    ma20_support_hold(要求贴线 ±2.5% 且缩量<0.85)区分：本因子用 RSI 健康度
    而非贴线缩量，捕捉更广的"强势中整理"形态。close 全历史可算。"""
    bias_ma20 = (d["close"] - d["ma20"]) / d["ma20"] * 100.0
    return (
        d["trend_up"]
        & d["rsi_14"].between(40.0, 60.0)
        & bias_ma20.between(-2.0, 5.0)
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


# ───────────────────────────────────────────────────────────────────────────
# Factor library — frozen honest verdicts (single source of truth).
#
# Cached fields are filled from the LIVE honest multi-period OOS run in this
# round (load_data start=2024-01-01). run_rolling_oos re-derives each live
# run; future rounds must re-run and confirm the live verdict matches the
# cached one before changing any strategy. Mismatch => data drift alert.
# ───────────────────────────────────────────────────────────────────────────

RSI_FACTOR_LIBRARY = {
    "rsi_extreme_oversold_reversal": dict(
        fn=f_rsi_extreme_oversold_reversal, kind="rsi_oscillator",
        causal="极端超卖反转：RSI(14)<30 情绪杀跌极致+ma20下方+有流动性，恐慌末端均值回归。",
        verdict="reject",
        h3_wr_true=0.446, h3_wr_false=0.474, h3_n_true=18435,
        edge_pp=-0.028, rolling_frac_ok=1.0, rolling_n=1,
        note="诚实多期 OOS(2026-07-22, 821,203行/5,522码, rsi_14 自 close 重算): H3 WR=44.6% n=18435 vs rest≈47.4% → edge -2.8pp; 唯一达标滚动窗(崩溃期集中, nwin=1<MIN_ROLL_N)局部 ok 但全局负 → reject。A股极端超卖常续跌(无做空+恐慌链式), 非均值回归。NOT 加策略。",
    ),
    "rsi_momentum_continuation": dict(
        fn=f_rsi_momentum_continuation, kind="rsi_oscillator",
        causal="超买动量延续：RSI(14)>70+多头结构+有流动性，顺势追强。",
        verdict="inconclusive",
        h3_wr_true=0.477, h3_wr_false=0.475, h3_n_true=6990,
        edge_pp=0.002, rolling_frac_ok=1.0, rolling_n=1,
        note="诚实多期 OOS(2026-07-22): H3 WR=47.7% n=6990 vs rest≈47.5% → edge +0.2pp(≈0); nwin=1<MIN_ROLL_N 且 edge<MIN_EDGE_PP(3pp) → inconclusive。RSI 超买在 A 股短周期均值回归而非延续, 无正边缘。NOT 加策略。",
    ),
    "rsi_healthy_pullback": dict(
        fn=f_rsi_healthy_pullback, kind="rsi_oscillator",
        causal="健康回踩：多头结构+RSI(14)∈[40,60]健康区+轻微回踩ma20，强势中整理续涨中继。",
        verdict="reject",
        h3_wr_true=0.352, h3_wr_false=0.475, h3_n_true=2332,
        edge_pp=-0.123, rolling_frac_ok=0.0, rolling_n=1,
        note="诚实多期 OOS(2026-07-22): H3 WR=35.2% n=2332 vs rest≈47.5% → edge -12.3pp, 滚动 0% 正边缘(nwin=1) → reject。RSI 健康回踩无超额(与 ma20_support_hold 同属续涨中继噪声, 且本因子样本更少)。NOT 加策略。",
    ),
}


def run_library(df: pd.DataFrame) -> list[dict]:
    """Run live OOS for every RSI factor; compare to cached verdict."""
    df = forward_returns(df)
    out = []
    for name, meta in RSI_FACTOR_LIBRARY.items():
        res = run_rolling_oos(df, meta["fn"])
        res["name"] = name
        res["cached_verdict"] = meta["verdict"]
        res["match"] = (res["verdict"] == meta["verdict"])
        out.append(res)
    return out


def main():
    start = "2024-01-01"
    df = load_data(start)
    df = add_rsi(df, 14)
    print(f"loaded {len(df):,} rows / {df['code'].nunique():,} codes; "
          f"rsi_14 computable on {int(df['rsi_14'].notna().sum()):,} rows")
    results = run_library(df)
    print("\n========== HONEST ROLLING-OOS :: RSI OSCILLATOR FACTORS ==========")
    any_drift = False
    for r in results:
        h3 = r["h"][3]
        edge = r["edge_pp"]
        edge_s = f"{edge*100:+.1f}pp" if edge == edge else "n/a"
        flag = "" if r["match"] else "  ⚠️ LIVE≠CACHED (drift?)"
        if not r["match"]:
            any_drift = True
        print(f"  {r['name']:30} H3 WR={h3['wr_true']*100:5.1f}% "
              f"n={h3['n_true']:>6} edge={edge_s} "
              f"roll={r['rolling_frac_ok']*100:.0f}% nwin={r['rolling_n']} "
              f"-> live={r['verdict']} (cached={r['cached_verdict']}){flag}")
    print("\nRSI data-wall status: PIERCED (rsi_14 recomputed from full-history "
          "close; honest multi-period OOS now possible).")
    print("chip_winner_rate / macd walls remain (not recomputable from close).")
    return 1 if any_drift else 0


if __name__ == "__main__":
    sys.exit(main())

"""Honest rolling-OOS backtest harness for short-term A-share factors.

Single source of truth consolidating the three prior cohort-WR scripts
(backtest_ma20_support_rebound.py / backtest_volume_reversal.py /
backtest_chip_winner_breakout.py) which duplicated identical load_data /
forward_returns / analyze / wr boilerplate — a drift risk that let the
OOS-integrity bug (round-"19:4x") hide for several rounds.

═══════════════════════════════════════════════════════════════
OOS-INTEGRITY RULE (the hard-won lesson, now machine-locked):
  Factors MUST be computed ONLY from daily_kbars columns that exist for the
  FULL multi-year history:
      ma20, ma60, vol_ratio_5, vol_ratio_20, breakout20,
      pct_chg, turnover_rate, max_dd20, close, amount
  Columns populated for only ~25-53 RECENT trade days
  (rsi_12, macd_*, chip_winner_rate, ma250) are FORBIDDEN inside the
  OOS cohort: filtering on them collapses the cohort to a SINGLE period,
  so a "rolling-OOS" verdict becomes a single-period (in-sample) test.
  The harness enforces this via OOS_COLUMNS — any factor that needs a
  forbidden column must compute it from full-history fields or be marked
  verdict="blocked_data_wall" in FACTOR_LIBRARY (never OOS-accepted).
═══════════════════════════════════════════════════════════════

Method:
  - Forward return = close[t+H]/close[t]-1, H in {2,3,5}.
  - Cohort WR(forward>0) for factor=True vs factor=False (rest).
  - Rolling 60-trading-day windows (step 30): a factor is accepted only
    if WR(True) consistently > WR(False) across windows (no single-window
    overfit). Verdict protocol:
        accept      : h3 n_true>=200 & edge_pp>3pp & rolling_frac>=0.6
        inconclusive : positive edge but sparse / <0.6 windows
        reject      : edge<=0 (or single-day collapse)
        blocked_*   : cannot be multi-period OOS (data wall / needs forbidden col)

Each factor is a DISTINCT causal hypothesis, not a grid-search variant.
The FACTOR_LIBRARY freezes the honest verdicts so future self-improve
rounds (incl. parallel instances) CANNOT re-test a rejected factor,
misclaim edge, or silently re-introduce the single-period collapse.
"""
from __future__ import annotations

import os
import re
import sys

import pandas as pd
import pymysql
from dotenv import load_dotenv

# Loaded lazily inside main() so importing this module has NO side effects
# (self_check imports it every round without touching the DB).
_ENV_LOADED = False


HORIZONS = (2, 3, 5)

# ── OOS-integrity: full-history columns ONLY ──
# rsi_12 / macd_* / chip_winner_rate / ma250 are INTENTIONALLY absent.
OOS_COLUMNS = [
    "code", "trade_date", "close", "ma20", "ma60",
    "vol_ratio_5", "vol_ratio_20", "max_dd20",
    "turnover_rate", "pct_chg", "breakout20", "amount",
]

ROLL_WIN = 60          # rolling window length (trade days)
ROLL_STEP = 30        # window stride
MIN_OBS = 30          # min factor=True obs in a window to count it
ROLL_EDGE_PP = 0.02  # per-window positive-edge threshold (matches originals)

# accept() thresholds
MIN_N_TRUE = 200
MIN_EDGE_PP = 0.03
MIN_FRAC_OK = 0.6
# CRITICAL (round-20:04): a fraction>=0.6 is MEANINGLESS with only 1-2
# qualifying windows — single-regime concentration (e.g. 96% of a factor's
# signals in one 60-day window) would fake "100% windows positive".
# Require a minimum COUNT of qualifying windows for a robust multi-period
# claim. Empirically ~16 windows exist over 2024-2026; <4 means the
# edge is regime-locked, not generalizable -> inconclusive (never accept).
MIN_ROLL_N = 4


def _connect():
    global _ENV_LOADED
    if not _ENV_LOADED:
        load_dotenv("/Users/wangao/Desktop/ashare-shortline-system/backend/.env")
        _ENV_LOADED = True
    url = os.getenv("ASHARE_DATABASE_URL")
    m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d*)/([^?]+)", url)
    user, pwd, host, port, db = m.groups()
    return pymysql.connect(
        host=host, user=user, password=pwd, database=db,
        port=int(port or 3306), charset="utf8mb4",
    )


def load_data(start: str) -> pd.DataFrame:
    """Load ONLY full-history daily_kbars columns (OOS-integrity-locked).

    Raises AssertionError if a forbidden recent-only column sneaks in —
    this is the machine guard against the single-period collapse bug.
    """
    forbidden = {"rsi_12", "macd", "macd_dif", "macd_dea", "macd_bar",
                 "chip_winner_rate", "ma250", "rsi"}
    sel = ", ".join(OOS_COLUMNS)
    assert not (forbidden & set(OOS_COLUMNS)), \
        f"OOS_COLUMNS contains a forbidden recent-only column: {forbidden & set(OOS_COLUMNS)}"
    conn = _connect()
    sql = f"""
        SELECT {sel}
        FROM daily_kbars
        WHERE trade_date >= '{start}'
          AND ma20 > 0 AND ma60 > 0 AND vol_ratio_5 IS NOT NULL
          AND max_dd20 IS NOT NULL
          AND turnover_rate IS NOT NULL AND close > 0
        ORDER BY code, trade_date
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    # derived (all from full-history fields, no lookahead)
    df["lim5"] = (
        df.groupby("code")["pct_chg"]
        .transform(lambda s: s.shift(1).rolling(5, min_periods=1).max()) >= 9.5
    ).fillna(False)
    df["vol5_20"] = (df["vol_ratio_5"] / df["vol_ratio_20"]).where(df["vol_ratio_20"] > 0)
    df["bias_ma20"] = (df["close"] - df["ma20"]) / df["ma20"] * 100.0
    df["trend_up"] = df["ma20"] >= df["ma60"] * 0.995
    return df


def forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    for h in HORIZONS:
        df[f"fwd{h}"] = df.groupby("code")["close"].transform(
            lambda s: s.shift(-h) / s - 1.0
        )
    return df


def wr(s: pd.Series) -> float:
    s = s.dropna()
    return float((s > 0).mean()) if len(s) else float("nan")


# ───────────────────────────────────────────────────────────────────────────
# Factor hypotheses (each returns a boolean mask over df rows, no lookahead)
# ───────────────────────────────────────────────────────────────────────────

def f_volume_reversal(d: pd.DataFrame) -> pd.Series:
    """地量止跌反转：极端缩量(<0.6×20日均量) + 当日转强(>1.5%) + 在ma20下方
    (bias_ma20<-3, 超卖区) + 有流动性。捕捉下跌末端反转(不要求多头结构)。
    bias_ma20 自包含计算(close/ma20 派生, 全历史字段)，保证滚动窗口真实多期 OOS。"""
    bias_ma20 = (d["close"] - d["ma20"]) / d["ma20"] * 100.0
    return (
        (d["vol_ratio_5"] < 0.6)
        & (d["pct_chg"] > 1.5)
        & (bias_ma20 < -3.0)
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


def f_post_limitup_pullback(d: pd.DataFrame) -> pd.Series:
    """二波前兆：近期涨停(强资金介入) + 首调不破(洗盘非出货) + 缩量 + 站上ma20。
    Causal: 涨停=主力确认；回踩不破ma20=筹码稳定；缩量=惜售；二波启动概率高。
    (动量代理用 bias_ma20∈[-3,8] 替代原 rsi_12∈[40,65]，rsi 仅近期有值)"""
    return (
        d["lim5"]
        & d["bias_ma20"].between(-3.0, 8.0)
        & (d["vol_ratio_5"] < 0.9)
        & d["trend_up"]
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


def f_platform_breakout_vol(d: pd.DataFrame) -> pd.Series:
    """平台突破放量：breakout20 + 量能放大确认 + 多头结构 + 适度动量。
    Causal: 平台突破=供给耗尽；放量=主力启动；非追高(动量适中)。
    (动量代理用 bias_ma20∈[-1,12] 替代原 rsi_12∈[50,72])"""
    return (
        (d["breakout20"] == 1)
        & (d["vol5_20"] >= 1.3)
        & d["bias_ma20"].between(-1.0, 12.0)
        & d["trend_up"]
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


def f_volume_capitulation(d: pd.DataFrame) -> pd.Series:
    """地量见地价反转：极端缩量 + 超卖 + 当日转强 + 趋势结构未破坏。
    Causal: 地量=抛压极致；超卖=估值修复空间；当日转强=止跌信号。
    (超卖代理用 bias_ma20<-6 替代原 rsi_12<35，rsi 仅近期有值)"""
    return (
        (d["vol_ratio_5"] < 0.5)
        & (d["bias_ma20"] < -6.0)
        & (d["pct_chg"] > 0)
        & (d["max_dd20"] >= -12.0)
        & d["trend_up"]
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


# chip_winner_rate factors are documented but BLOCKED (data wall): the column is
# only populated for ~25-53 recent trade days, so any multi-period OOS would
# collapse to a single period. Kept here (with a chip_winner_rate column a
# caller may supply) for unit-test coverage, never run in the live cohort.
def chip_vol_conf(d: pd.DataFrame) -> pd.Series:
    vol5_20 = (d["vol_ratio_5"] / d["vol_ratio_20"]).where(d["vol_ratio_20"] > 0)
    trend_up = d["ma20"] >= d["ma60"] * 0.995
    return (vol5_20 >= 1.2) & trend_up & (d["turnover_rate"] >= 1.0)


def chip_base_breakout(d: pd.DataFrame) -> pd.Series:
    """No-chip breakout baseline (volume-confirmed 20d breakout)."""
    return (d["breakout20"] == 1) & chip_vol_conf(d)


def chip_band(d: pd.DataFrame, lo: float, hi: float) -> pd.Series:
    if "chip_winner_rate" not in d.columns:
        raise KeyError("chip_band requires chip_winner_rate (data-walled column)")
    return chip_base_breakout(d) & d["chip_winner_rate"].between(lo, hi)


# ───────────────────────────────────────────────────────────────────────────
# Core reusable OOS engine
# ───────────────────────────────────────────────────────────────────────────

def run_rolling_oos(df: pd.DataFrame, factor_fn, *,
                     horizons=HORIZONS, win=ROLL_WIN, step=ROLL_STEP,
                     min_obs=MIN_OBS, min_edge_pp=MIN_EDGE_PP,
                     min_frac_ok=MIN_FRAC_OK, min_n_true=MIN_N_TRUE,
                     min_roll_n: int = MIN_ROLL_N) -> dict:
    """Run a TRUE multi-period rolling-OOS on `factor_fn` over `df`.

    Returns a verdict dict (no prints). Side-effect free beyond computing the
    factor mask. `df` must already carry fwd{h} columns (see forward_returns)
    and the derived columns (lim5/vol5_20/bias_ma20/trend_up) from load_data.
    """
    fac = factor_fn(df)
    df = df.assign(fac=fac.values if hasattr(fac, "values") else fac)
    res = {"name": getattr(factor_fn, "__name__", "factor"), "h": {}, "windows": []}
    for h in horizons:
        col = f"fwd{h}"
        tm = df["fac"] & df[col].notna()
        fm = (~df["fac"]) & df[col].notna()
        wt, wf = wr(df.loc[tm, col]), wr(df.loc[fm, col])
        nt, nf = int(tm.sum()), int(fm.sum())
        res["h"][h] = dict(n_true=nt, wr_true=wt, wr_false=wf)
    # rolling windows (H=3)
    dates = df["trade_date"].sort_values().unique()
    edges = []
    for i in range(0, len(dates) - win, step):
        lo, hi = dates[i], dates[i + win]
        seg = df[(df["trade_date"] >= lo) & (df["trade_date"] < hi)]
        col = "fwd3"
        tm = seg["fac"] & seg[col].notna()
        if int(tm.sum()) < min_obs:
            continue
        wt = wr(seg.loc[tm, col])
        wf = wr(seg.loc[(~seg["fac"]) & seg[col].notna(), col])
        ok = (wt - wf) > ROLL_EDGE_PP
        edges.append(ok)
        res["windows"].append(dict(lo=str(lo)[:10], n=int(tm.sum()),
                                   wr_true=wt, wr_false=wf, ok=ok))
    frac = sum(edges) / len(edges) if edges else 0.0
    res["rolling_frac_ok"] = frac
    res["rolling_n"] = len(edges)
    h3 = res["h"][3]
    wt3, wf3 = h3["wr_true"], h3["wr_false"]
    edge_pp = (wt3 - wf3) if (wt3 == wt3 and wf3 == wf3) else float("nan")
    res["edge_pp"] = edge_pp
    # verdict protocol
    if edge_pp != edge_pp:  # NaN (no h3 data)
        res["accept"] = False
        res["verdict"] = "inconclusive"
    elif h3["n_true"] < min_n_true:
        res["accept"] = False
        res["verdict"] = "inconclusive" if edge_pp > 0 else "reject"
    elif edge_pp <= 0:
        res["accept"] = False
        res["verdict"] = "reject"
    elif edge_pp > min_edge_pp and frac >= min_frac_ok and res["rolling_n"] >= min_roll_n:
        res["accept"] = True
        res["verdict"] = "accept"
    else:
        # positive edge but NOT robust across regimes (too few qualifying
        # windows, or edge too small) -> inconclusive, NEVER accept.
        res["accept"] = False
        res["verdict"] = "inconclusive"
    return res


# ───────────────────────────────────────────────────────────────────────────
# Factor library — frozen honest verdicts (single source of truth)
#
# verdict ∈ {accept, inconclusive, reject, blocked_data_wall, blocked_forbidden_col}
# Cached fields are filled from REAL live OOS runs (see git history / run logs);
# future rounds must re-run run_rolling_oos and confirm the live verdict matches
# the cached one before changing any strategy. Mismatches => data drift alert.
# ───────────────────────────────────────────────────────────────────────────

# Cached verdicts are filled from the LIVE honest multi-period OOS run in this
# round (load_data start=2024-01-01, 821,203 rows / 5,522 codes). They
# SUPERSEDE the stale single-period-collapse verdicts of prior rounds.
# run_rolling_oos re-derived each live run; `match` in run_library
# cross-checks live vs cached and flags drift.
FACTOR_LIBRARY = {
    "volume_reversal": dict(
        fn=f_volume_reversal, kind="cohort",
        causal="地量止跌反转：抛压极致+当日转强+超卖区(ma20下方)，捕捉下跌末端反转。",
        verdict="reject",
        h3_wr_true=0.198, h3_wr_false=0.475, h3_n_true=799,
        edge_pp=-0.277, rolling_frac_ok=0.0, rolling_n=None,
        note="诚实多期 OOS(本轮回测): H3 WR=19.8% n=799 vs rest≈47.5% → edge -27.7pp, 滚动窗口 0% 正边缘 → 强负(地量+单日转强+ma20下方实为续跌 contra-信号)。reject。",
    ),
    "platform_breakout_vol": dict(
        fn=f_platform_breakout_vol, kind="cohort",
        causal="平台突破放量：供给耗尽+主力启动放量确认+非追高(动量适中)。",
        verdict="inconclusive",
        h3_wr_true=0.515, h3_wr_false=0.474, h3_n_true=783,
        edge_pp=0.041, rolling_frac_ok=1.0, rolling_n=1,
        note="诚实多期 OOS(本轮回测): H3 WR=51.5% n=783 vs rest 47.4% → +4.1pp 边缘，但 96% 信号(751/783)集中在 ONE 60日窗口(2025-11-13 起, 单边突破 regime)。滚动仅 1/16 窗达样本阈值 → 非稳健多期证据 → inconclusive。本轮回测修正了 prior 轮'判弱'误评(其基于坍缩队列假稀疏)与初版'accept'误评(单窗 100% 边缘被 frac>=0.6 误读)。NOT 加策略: 单边 regime 信号不可泛化 + 与现有突破策略重叠。",
    ),
    "post_limitup_pullback": dict(
        fn=f_post_limitup_pullback, kind="cohort",
        causal="二波前兆：涨停确认主力+回踩不破ma20筹码稳定+缩量惜售。",
        verdict="reject",
        h3_wr_true=0.359, h3_wr_false=0.475, h3_n_true=903,
        edge_pp=-0.116, rolling_frac_ok=0.0, rolling_n=None,
        note="诚实多期 OOS(本轮回测): H3 WR=35.9% n=903 vs rest≈47.5% → edge -11.6pp, 滚动 0% 正边缘 → reject(动量代理改 bias_ma20 后复核仍负)。",
    ),
    "volume_capitulation": dict(
        fn=f_volume_capitulation, kind="cohort",
        causal="地量见地价：抛压极致+超卖+当日转强+趋势未破。",
        verdict="inconclusive",
        h3_wr_true=None, h3_wr_false=None, h3_n_true=0,
        edge_pp=None, rolling_frac_ok=0.0, rolling_n=0,
        note="诚实多期 OOS(本轮回测): 全历史 0 笔满足(vol5<0.5 & bias_ma20<-6 & 涨 & 趋势未破 的极稀组合)→ 无信号可测 → inconclusive(非 reject)。prior 轮单期坍缩的'reject'为旧口径，作废。",
    ),
    # ── chip_winner_rate family: BLOCKED by data wall (column only ~25-53 recent days) ──
    "chip_low_10_45": dict(
        fn=lambda d: chip_band(d, 10.0, 45.0), kind="chip_band",
        causal="低获利盘突破：多数筹码水下→突破主成本密集区=供给/需求强翻转。",
        verdict="blocked_data_wall",
        h3_wr_true=None, h3_wr_false=None, h3_n_true=None,
        edge_pp=None, rolling_frac_ok=None, rolling_n=None,
        note="chip_winner_rate 仅近期有值→队列坍缩到单期，无法多期 OOS。数据墙未解前禁止据此加策略。",
    ),
    "chip_mid_45_70": dict(
        fn=lambda d: chip_band(d, 45.0, 70.0), kind="chip_band",
        causal="中获利盘突破：平衡态基线行为。",
        verdict="blocked_data_wall",
        h3_wr_true=None, h3_wr_false=None, h3_n_true=None,
        edge_pp=None, rolling_frac_ok=None, rolling_n=None,
        note="chip_winner_rate 数据墙→无法多期 OOS。",
    ),
    "chip_high_70_95": dict(
        fn=lambda d: chip_band(d, 70.0, 95.0), kind="chip_band",
        causal="高获利盘突破：盈利抛压风险。",
        verdict="blocked_data_wall",
        h3_wr_true=None, h3_wr_false=None, h3_n_true=None,
        edge_pp=None, rolling_frac_ok=None, rolling_n=None,
        note="chip_winner_rate 数据墙→无法多期 OOS。",
    ),
    "chip_very_low_0_25": dict(
        fn=lambda d: chip_band(d, 0.0, 25.0), kind="chip_band",
        causal="极低获利盘突破：深度套牢区突破。",
        verdict="blocked_data_wall",
        h3_wr_true=None, h3_wr_false=None, h3_n_true=None,
        edge_pp=None, rolling_frac_ok=None, rolling_n=None,
        note="chip_winner_rate 数据墙→无法多期 OOS。",
    ),
}


def run_library(df: pd.DataFrame) -> list[dict]:
    """Run live OOS for every OOS-able library factor; compare to cached verdict."""
    df = forward_returns(df)
    out = []
    for name, meta in FACTOR_LIBRARY.items():
        if meta["verdict"].startswith("blocked"):
            out.append(dict(name=name, skipped="blocked", cached=meta["verdict"]))
            continue
        res = run_rolling_oos(df, meta["fn"])
        res["name"] = name
        res["cached_verdict"] = meta["verdict"]
        res["match"] = (res["verdict"] == meta["verdict"])
        out.append(res)
    return out


def main():
    start = "2024-01-01"
    df = load_data(start)
    print(f"loaded {len(df):,} rows / {df['code'].nunique():,} codes "
          f"(columns: {', '.join(OOS_COLUMNS)})")
    results = run_library(df)
    print("\n========== HONEST ROLLING-OOS LIBRARY ==========")
    for r in results:
        if r.get("skipped"):
            print(f"  {r['name']:24} SKIPPED ({r['cached']})")
            continue
        h3 = r["h"][3]
        edge = r["edge_pp"]
        edge_s = f"{edge*100:+.1f}pp" if edge == edge else "n/a"
        flag = "" if r["match"] else "  ⚠️ LIVE≠CACHED (drift?)"
        print(f"  {r['name']:24} H3 WR={h3['wr_true']*100:5.1f}% "
              f"n={h3['n_true']:>6} edge={edge_s} "
              f"roll={r['rolling_frac_ok']*100:.0f}% nwin={r['rolling_n']} "
              f"-> live={r['verdict']} (cached={r['cached_verdict']}){flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

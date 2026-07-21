"""Honest rolling-OOS backtest: volume-reversal (地量止跌反转) short-term factor.

This is a DISTINCT causal hypothesis from prior rounds:
  - backtest_ma20_support_rebound.py tested post_limitup_pullback /
    platform_breakout_vol / volume_capitulation (all rejected).
  - volume_capitulation required trend_up + max_dd20 + rsi<35.
Our hypothesis DIFFERS: capture the END of a downtrend (not require uptrend
structure) via extreme volume shrinkage + same-day turn + oversold-but-not
-extreme. Causal: 地量=抛压耗尽；当日转强=止跌；超卖=修复空间。

Method (round-17 discipline):
  - Only raw daily_kbars columns (full multi-year history: vol_ratio_5/20,
    rsi_12, pct_chg, turnover_rate, ma20/60, close).
  - Forward return = close[t+H]/close[t]-1, H in {2,3,5}.
  - Rolling 60-trading-day windows; accept only if WR(True) consistently > WR(False).
"""
from __future__ import annotations

import os
import re
import sys

import pandas as pd
import pymysql
from dotenv import load_dotenv

load_dotenv("/Users/wangao/Desktop/ashare-shortline-system/backend/.env")

HORIZONS = (2, 3, 5)


def _connect():
    url = os.getenv("ASHARE_DATABASE_URL")
    m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d*)/([^?]+)", url)
    user, pwd, host, port, db = m.groups()
    return pymysql.connect(
        host=host, user=user, password=pwd, database=db,
        port=int(port or 3306), charset="utf8mb4",
    )


def load_data(start: str) -> pd.DataFrame:
    # CRITICAL OOS-INTEGRITY NOTE (round-?? T1 methodology fix):
    #   ma60 / rsi_12 / macd / chip_winner_rate are ONLY populated for recent
    #   ~25-53 trade days (backfill gap). Filtering on them (as prior rounds'
    #   scaffolds did with `ma60>0` / `rsi_12 IS NOT NULL`) collapses the cohort
    #   to a SINGLE period, so "rolling-OOS" was actually a single-period test.
    #   We use ONLY full-history fields (ma20, vol_ratio_5/20, pct_chg,
    #   turnover_rate, close) and derive bias_ma20 from close+ma20 so the rolling
    #   windows below are genuinely multi-year out-of-sample.
    conn = _connect()
    sql = f"""
        SELECT code, trade_date, close, ma20, vol_ratio_5, vol_ratio_20,
               turnover_rate, pct_chg
        FROM daily_kbars
        WHERE trade_date >= '{start}'
          AND ma20 > 0 AND vol_ratio_5 IS NOT NULL
          AND turnover_rate IS NOT NULL AND close > 0
        ORDER BY code, trade_date
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["vol5_20"] = (df["vol_ratio_5"] / df["vol_ratio_20"]).where(df["vol_ratio_20"] > 0)
    df["bias_ma20"] = (df["close"] - df["ma20"]) / df["ma20"] * 100.0
    return df


def forward_returns(df: pd.DataFrame):
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    for h in HORIZONS:
        df[f"fwd{h}"] = df.groupby("code")["close"].transform(
            lambda s: s.shift(-h) / s - 1.0
        )
    return df


def f_volume_reversal(d: pd.DataFrame) -> pd.Series:
    """地量止跌反转：极端缩量(<0.6×20日均量) + 当日转强(>1.5%) + 在ma20下方(bias_ma20<-3,
    超卖区) + 有流动性。不要求多头结构(捕捉下跌末端反转)，区别于 volume_capitulation。
    NOTE: 用 bias_ma20(close/ma20 派生, 全历史字段) 替代 rsi_12(仅近期有值)，
    以保证滚动窗口是真实多期 OOS。bias_ma20 在此自包含计算(不依赖 load_data)。"""
    bias_ma20 = (d["close"] - d["ma20"]) / d["ma20"] * 100.0
    return (
        (d["vol_ratio_5"] < 0.6)
        & (d["pct_chg"] > 1.5)
        & (bias_ma20 < -3.0)
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


FACTORS = {
    "volume_reversal": f_volume_reversal,
}


def wr(s: pd.Series) -> float:
    s = s.dropna()
    return float((s > 0).mean()) if len(s) else float("nan")


def analyze(df: pd.DataFrame, name: str, fn) -> dict:
    fac = fn(df)
    df = df.assign(fac=fac)
    res = {"name": name, "windows": []}
    print(f"\n##### {name} #####")
    for h in HORIZONS:
        col = f"fwd{h}"
        tm = df["fac"] & df[col].notna()
        wt = wr(df.loc[tm, col])
        nt = int(tm.sum())
        fm = (~df["fac"]) & df[col].notna()
        wf = wr(df.loc[fm, col])
        print(f"  H={h}d n={nt:>6} WR={wt*100:5.1f}%  vs rest n={int(fm.sum()):>6} WR={wf*100:5.1f}%  edge={(wt-wf)*100:+.1f}pp")
        res.setdefault("h", {})[h] = dict(n_true=nt, wr_true=wt, wr_false=wf)
    dates = df["trade_date"].sort_values().unique()
    win = 60
    edges = []
    for i in range(0, len(dates) - win, win // 2):
        lo, hi = dates[i], dates[i + win]
        seg = df[(df["trade_date"] >= lo) & (df["trade_date"] < hi)]
        col = "fwd3"
        tm = seg["fac"] & seg[col].notna()
        if tm.sum() < 30:
            continue
        wt = wr(seg.loc[tm, col])
        wf = wr(seg.loc[(~seg["fac"]) & seg[col].notna(), col])
        ok = (wt - wf) > 0.02
        edges.append(ok)
        res["windows"].append(dict(lo=str(lo)[:10], n=int(tm.sum()), wr_true=wt, wr_false=wf, ok=ok))
    frac = sum(edges) / len(edges) if edges else 0
    print(f"  rolling windows w/ positive edge: {sum(edges)}/{len(edges)} ({frac*100:.0f}%)")
    res["rolling_frac_ok"] = frac
    res["rolling_n"] = len(edges)
    h3 = res["h"][3]
    res["accept"] = (h3["n_true"] >= 200 and (h3["wr_true"] - h3["wr_false"]) > 0.03 and frac >= 0.6)
    print(f"  DECISION: {'ACCEPT' if res['accept'] else 'reject'}")
    return res


def main():
    start = "2024-01-01"
    df = load_data(start)
    print(f"loaded {len(df):,} rows / {df['code'].nunique():,} codes")
    df = forward_returns(df)
    results = [analyze(df, name, fn) for name, fn in FACTORS.items()]
    print("\n========== SUMMARY ==========")
    for r in results:
        h3 = r["h"][3]
        print(f"  {r['name']:22} H3 WR={h3['wr_true']*100:5.1f}% n={h3['n_true']:>6} "
              f"edge={(h3['wr_true']-h3['wr_false'])*100:+.1f}pp roll={r['rolling_frac_ok']*100:.0f}% "
              f"-> {'ACCEPT' if r['accept'] else 'reject'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

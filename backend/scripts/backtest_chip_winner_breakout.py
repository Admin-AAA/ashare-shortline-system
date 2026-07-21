"""Honest rolling-OOS backtest: chip_winner_rate (获利盘比例) as a breakout-quality
filter — is the % of chips in profit at a breakout predictive of forward edge?

Method (round-17 discipline, mirrors backtest_ma20_support_rebound.py):
  - Factor computed ONLY from raw daily_kbars columns (chip_winner_rate,
    breakout20/60, vol_ratio_5/20, rsi_12, ma20/60, turnover_rate, pct_chg).
  - Forward return = close[t+H]/close[t]-1, H in {2,3,5}.
  - Cohort WR(forward>0) for factor=True vs a no-chip breakout baseline, so we
    measure ADDED edge, not mere correlation with breakout.
  - Rolling 60-trading-day windows: accept only if WR(True) consistently > baseline
    across windows (no single-window overfit).

Causal hypotheses (distinct from chip-single-peak-breakout which uses chip
distribution SHAPE = chip_concentration; here we use chip PROFIT STATUS):
  - low winner_rate at breakout  -> most holders underwater -> breaking through the
    main cost cluster; if it holds, decisive supply/demand flip (洗盘充分引爆).
  - high winner_rate at breakout -> profit-taking supply risk.
  - mid winner_rate              -> balanced; baseline behaviour.
Each band is tested independently; only the band with honest OOS edge is a candidate.
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
    conn = _connect()
    sql = f"""
        SELECT code, trade_date, close, ma20, ma60, vol_ratio_5, vol_ratio_20,
               rsi_12, turnover_rate, pct_chg, breakout20, breakout60,
               chip_winner_rate
        FROM daily_kbars
        WHERE trade_date >= '{start}'
          AND ma20 > 0 AND ma60 > 0 AND vol_ratio_5 IS NOT NULL
          AND rsi_12 IS NOT NULL AND turnover_rate IS NOT NULL
          AND chip_winner_rate IS NOT NULL AND close > 0
          AND breakout20 IS NOT NULL
        ORDER BY code, trade_date
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["vol5_20"] = (df["vol_ratio_5"] / df["vol_ratio_20"]).where(df["vol_ratio_20"] > 0)
    df["bias_ma20"] = (df["close"] - df["ma20"]) / df["ma20"] * 100.0
    df["trend_up"] = df["ma20"] >= df["ma60"] * 0.995
    # breakout requires volume confirmation (shared by all cohorts)
    df["vol_conf"] = (df["vol5_20"] >= 1.2) & df["trend_up"] & (df["turnover_rate"] >= 1.0)
    return df


def forward_returns(df: pd.DataFrame):
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    for h in HORIZONS:
        df[f"fwd{h}"] = df.groupby("code")["close"].transform(
            lambda s: s.shift(-h) / s - 1.0
        )
    return df


def _vol_conf(d: pd.DataFrame) -> pd.Series:
    """Self-contained volume-confirmation gate (no dependency on load_data
    having precomputed a `vol_conf` column)."""
    vol5_20 = (d["vol_ratio_5"] / d["vol_ratio_20"]).where(d["vol_ratio_20"] > 0)
    trend_up = d["ma20"] >= d["ma60"] * 0.995
    return (vol5_20 >= 1.2) & trend_up & (d["turnover_rate"] >= 1.0)


def base_breakout(d: pd.DataFrame) -> pd.Series:
    """No-chip breakout baseline (volume-confirmed 20d breakout)."""
    return (d["breakout20"] == 1) & _vol_conf(d)


def chip_band(d: pd.DataFrame, lo: float, hi: float) -> pd.Series:
    return base_breakout(d) & d["chip_winner_rate"].between(lo, hi)


FACTORS = {
    "baseline_breakout": ("baseline", base_breakout),
    "chip_low_10_45": ("band", lambda d: chip_band(d, 10.0, 45.0)),
    "chip_mid_45_70": ("band", lambda d: chip_band(d, 45.0, 70.0)),
    "chip_high_70_95": ("band", lambda d: chip_band(d, 70.0, 95.0)),
    "chip_verylow_0_25": ("band", lambda d: chip_band(d, 0.0, 25.0)),
}


def wr(s: pd.Series) -> float:
    s = s.dropna()
    return float((s > 0).mean()) if len(s) else float("nan")


def analyze(df: pd.DataFrame, name: str, fn, baseline_wr: dict | None = None) -> dict:
    fac = fn(df)
    df = df.assign(fac=fac)
    res = {"name": name, "windows": []}
    print(f"\n##### {name} #####")
    for h in HORIZONS:
        col = f"fwd{h}"
        tm = df["fac"] & df[col].notna()
        wt = wr(df.loc[tm, col])
        nt = int(tm.sum())
        line = f"  H={h}d n={nt:>6} WR={wt*100:5.1f}%"
        if baseline_wr and h in baseline_wr:
            wb = baseline_wr[h]
            line += f"  (baseline n={wb['n']:>6} WR={wb['wr']*100:5.1f}% edge={(wt-wb['wr'])*100:+.1f}pp)"
        print(line)
        res.setdefault("h", {})[h] = dict(n_true=nt, wr_true=wt,
                                          wr_false=baseline_wr[h]["wr"] if baseline_wr and h in baseline_wr else None)
    # rolling windows (H=3) vs baseline
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
        # baseline in same window
        bm = seg["breakout20"] == 1
        wb = wr(seg.loc[bm & seg[col].notna(), col])
        ok = (wt - wb) > 0.02
        edges.append(ok)
        res["windows"].append(dict(lo=str(lo)[:10], n=int(tm.sum()),
                                   wr_true=wt, wr_base=wb, ok=ok))
    frac = sum(edges) / len(edges) if edges else 0
    print(f"  rolling windows w/ positive edge vs baseline: {sum(edges)}/{len(edges)} ({frac*100:.0f}%)")
    res["rolling_frac_ok"] = frac
    res["rolling_n"] = len(edges)
    h3 = res["h"][3]
    res["accept"] = (h3["n_true"] >= 200 and (baseline_wr and (h3["wr_true"] - baseline_wr[3]["wr"]) > 0.03)
                     and frac >= 0.6)
    print(f"  DECISION: {'ACCEPT' if res['accept'] else 'reject'}")
    return res


def main():
    start = "2024-01-01"
    df = load_data(start)
    print(f"loaded {len(df):,} rows / {df['code'].nunique():,} codes")
    print(f"chip_winner_rate range: "
          f"{df['chip_winner_rate'].min():.1f}..{df['chip_winner_rate'].max():.1f} "
          f"median {df['chip_winner_rate'].median():.1f}")
    df = forward_returns(df)
    # baseline first
    bres = analyze(df, "baseline_breakout", FACTORS["baseline_breakout"][1])
    baseline_wr = {h: dict(n=bres["h"][h]["n_true"], wr=bres["h"][h]["wr_true"]) for h in HORIZONS}
    results = [bres]
    for name, (kind, fn) in FACTORS.items():
        if name == "baseline_breakout":
            continue
        results.append(analyze(df, name, fn, baseline_wr))
    print("\n========== SUMMARY ==========")
    for r in results:
        h3 = r["h"][3]
        extra = ""
        if r["name"] != "baseline_breakout":
            extra = f"edge_vs_base={(h3['wr_true']-baseline_wr[3]['wr'])*100:+.1f}pp"
        print(f"  {r['name']:22} H3 WR={h3['wr_true']*100:5.1f}% "
              f"n={h3['n_true']:>6} roll={r['rolling_frac_ok']*100:.0f}% {extra} "
              f"-> {'ACCEPT' if r['accept'] else 'reject'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

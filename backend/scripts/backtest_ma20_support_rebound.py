"""Honest rolling-OOS backtest for several causally-distinct short-term factors.

Method (round-17 discipline):
  - Factor computed ONLY from raw daily_kbars columns that are also in the
    evaluator ALLOWED_NAMES whitelist (so a strategy `when` can use them).
  - Forward return = close[t+H]/close[t]-1, H in {2,3,5}.
  - Cohort WR(forward>0) for factor=True vs factor=False.
  - Rolling 60-trading-day windows: a factor is accepted only if
    WR(True) consistently > WR(False) across windows (no single-window overfit).

Each factor is a DISTINCT causal hypothesis, not a grid-search variant.
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
               rsi_12, max_dd20, turnover_rate, pct_chg, breakout20
        FROM daily_kbars
        WHERE trade_date >= '{start}'
          AND ma20 > 0 AND ma60 > 0 AND vol_ratio_5 IS NOT NULL
          AND rsi_12 IS NOT NULL AND max_dd20 IS NOT NULL
          AND turnover_rate IS NOT NULL AND close > 0
        ORDER BY code, trade_date
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # prior limit-up within 5 sessions (>=9.5% daily move)
    df["lim5"] = (
        df.groupby("code")["pct_chg"]
        .transform(lambda s: s.shift(1).rolling(5, min_periods=1).max()) >= 9.5
    ).fillna(False)
    # 5/20 volume ratio (whitelisted name vol5_20)
    df["vol5_20"] = (df["vol_ratio_5"] / df["vol_ratio_20"]).where(df["vol_ratio_20"] > 0)
    df["bias_ma20"] = (df["close"] - df["ma20"]) / df["ma20"] * 100.0
    df["trend_up"] = df["ma20"] >= df["ma60"] * 0.995
    return df


def forward_returns(df: pd.DataFrame):
    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)
    for h in HORIZONS:
        df[f"fwd{h}"] = df.groupby("code")["close"].transform(
            lambda s: s.shift(-h) / s - 1.0
        )
    return df


# ── Factor hypotheses ──
def f_post_limitup_pullback(d: pd.DataFrame) -> pd.Series:
    """二波前兆：近期涨停(强资金介入) + 首调不破(洗盘非出货) + 缩量 + 站上ma20。
    Causal: 涨停=主力确认；回踩不破ma20=筹码稳定；缩量=惜售；二波启动概率高。"""
    return (
        d["lim5"]
        & d["bias_ma20"].between(-6.0, 2.0)
        & (d["vol_ratio_5"] < 0.9)
        & d["rsi_12"].between(40, 65)
        & d["trend_up"]
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


def f_platform_breakout_vol(d: pd.DataFrame) -> pd.Series:
    """平台突破放量：breakout20 + 量能放大确认 + 多头结构 + 适度动量。
    Causal: 平台突破=供给耗尽；放量=主力启动；非追高(动量适中)。"""
    return (
        (d["breakout20"] == 1)
        & (d["vol5_20"] >= 1.3)
        & d["rsi_12"].between(50, 72)
        & d["trend_up"]
        & d["bias_ma20"].between(-3.0, 8.0)
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


def f_volume_capitulation(d: pd.DataFrame) -> pd.Series:
    """地量见地价反转：极端缩量 + 超卖 + 当日转强 + 趋势结构未破坏。
    Causal: 地量=抛压极致；超卖=估值修复空间；当日转强=止跌信号。"""
    return (
        (d["vol_ratio_5"] < 0.5)
        & (d["rsi_12"] < 35)
        & (d["pct_chg"] > 0)
        & (d["max_dd20"] >= -12.0)
        & d["trend_up"]
        & (d["turnover_rate"] >= 1.0)
    ).fillna(False)


FACTORS = {
    "post_limitup_pullback": f_post_limitup_pullback,
    "platform_breakout_vol": f_platform_breakout_vol,
    "volume_capitulation": f_volume_capitulation,
}


def wr(s: pd.Series) -> float:
    s = s.dropna()
    return float((s > 0).mean()) if len(s) else float("nan")


def analyze(df: pd.DataFrame, name: str, fn) -> dict:
    fac = fn(df)
    df = df.assign(fac=fac)
    res = {"name": name, "windows": []}
    print(f"\n##### {name} #####")
    edges = []
    for h in HORIZONS:
        col = f"fwd{h}"
        tm = df["fac"] & df[col].notna()
        fm = (~df["fac"]) & df[col].notna()
        wt, wf = wr(df.loc[tm, col]), wr(df.loc[fm, col])
        nt, nf = int(tm.sum()), int(fm.sum())
        print(f"  H={h}d True n={nt:>6} WR={wt*100:5.1f}%  "
              f"False n={nf:>6} WR={wf*100:5.1f}%  edge={(wt-wf)*100:+.1f}pp")
        res.setdefault("h", {})[h] = dict(n_true=nt, wr_true=wt, wr_false=wf)
    # rolling windows (H=3)
    dates = df["trade_date"].sort_values().unique()
    win = 60
    for i in range(0, len(dates) - win, win // 2):
        lo, hi = dates[i], dates[i + win]
        seg = df[(df["trade_date"] >= lo) & (df["trade_date"] < hi)]
        col = "fwd3"
        tm = seg["fac"] & seg[col].notna()
        fm = (~seg["fac"]) & seg[col].notna()
        if tm.sum() < 30:
            continue
        wt, wf = wr(seg.loc[tm, col]), wr(seg.loc[fm, col])
        ok = (wt - wf) > 0.02
        edges.append(ok)
        res["windows"].append(dict(lo=str(lo)[:10], n=int(tm.sum()),
                                   wr_true=wt, wr_false=wf, ok=ok))
    frac = sum(edges) / len(edges) if edges else 0
    print(f"  rolling windows w/ positive edge: {sum(edges)}/{len(edges)} ({frac*100:.0f}%)")
    res["rolling_frac_ok"] = frac
    res["rolling_n"] = len(edges)
    h3 = res["h"][3]
    res["accept"] = (h3["n_true"] >= 200 and (h3["wr_true"] - h3["wr_false"]) > 0.03
                     and frac >= 0.6)
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
        print(f"  {r['name']:24} H3 WR={h3['wr_true']*100:5.1f}% "
              f"n={h3['n_true']:>6} edge={(h3['wr_true']-h3['wr_false'])*100:+.1f}pp "
              f"roll={r['rolling_frac_ok']*100:.0f}% -> {'ACCEPT' if r['accept'] else 'reject'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""外部指数慢趋势门控三窗口探索（只读）。
生产 regime(_market_base) 只用当日横截面 up_ratio，不含指数价格趋势；本脚本检验
独立的慢趋势（000905 中证500 收盘 vs MA、MA 斜率、多日指数收益、回撤）能否在
冻结/原前瞻/第三窗【同向】区分 v2 选中票的净收益。信号日 T 收盘已知 T 指数，无未来函数。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"


def db():
    for line in open(B / ".env"):
        if line.startswith("ASHARE_DATABASE_URL="):
            return create_engine(line.split("=", 1)[1].strip().strip('"').strip("'"))


def index_feat(ts="000905.SH"):
    q = text("""SELECT trade_date dt, close, pct_chg FROM index_daily
                WHERE ts_code=:t ORDER BY trade_date""")
    ix = pd.read_sql(q, db(), params={"t": ts})
    ix["dt"] = ix.dt.astype(str); ix = ix.sort_values("dt").reset_index(drop=True)
    c = ix.close
    for w in (5, 10, 20, 60):
        ix[f"ma{w}"] = c.rolling(w).mean()
    ix["dist20"] = c / ix.ma20 - 1            # 收盘距 MA20
    ix["dist60"] = c / ix.ma60 - 1
    ix["ma20_slope5"] = ix.ma20 / ix.ma20.shift(5) - 1   # MA20 五日斜率
    ix["ix_ret5"] = c.pct_change(5); ix["ix_ret10"] = c.pct_change(10); ix["ix_ret20"] = c.pct_change(20)
    ix["ix_dd20"] = c / c.rolling(20).max() - 1          # 20日回撤
    ix["above20"] = (c > ix.ma20).astype(int)
    ix["above60"] = (c > ix.ma60).astype(int)
    # 慢趋势状态：价格在 MA20 下 且 MA20 下行 = 下行趋势
    ix["downtrend"] = ((c < ix.ma20) & (ix.ma20_slope5 < 0)).astype(int)
    ix["strong_down"] = ((c < ix.ma20) & (c < ix.ma60)).astype(int)
    return ix[["dt", "dist20", "dist60", "ma20_slope5", "ix_ret5", "ix_ret10", "ix_ret20",
               "ix_dd20", "above20", "above60", "downtrend", "strong_down"]]


def load(name):
    d = pd.read_pickle(OUT / name)
    d["dt"] = d.dt.astype(str)
    return d[["dt", "code", "net", "key"]]


def block(df, col):
    g = df.groupby(col).net.agg(n="size", avg="mean", win=lambda x: (x > 0).mean(),
                                ndays=lambda x: df.loc[x.index, "dt"].nunique())
    return g.round(3)


def main():
    ix = index_feat()
    win = {"FROZEN": load("canon_frozen_v2_selected.pkl"),
           "FORWARD": load("canon_forward_v2_selected.pkl"),
           "RUN3": load("run3_v2_selected.pkl")}
    for w, d in win.items():
        m = d.merge(ix, on="dt", how="left")
        print(f"\n========== {w}  n={len(m)}  指数缺失{m.dist20.isna().sum()} ==========")
        for flag in ["above20", "above60", "downtrend", "strong_down"]:
            print(f"-- {flag} --"); print(block(m, flag).to_string())
        # 连续变量三分位
        for cc in ["dist20", "ma20_slope5", "ix_ret5", "ix_ret10", "ix_dd20"]:
            x = m.dropna(subset=[cc]).copy()
            x["q"] = pd.qcut(x[cc], 3, labels=["Q0低", "Q1中", "Q2高"], duplicates="drop")
            t = x.groupby("q", observed=True).net.agg(n="size", avg="mean", win=lambda z: (z > 0).mean())
            print(f"-- {cc} 三分位 --"); print(t.round(3).to_string())
        m.to_pickle(OUT / f"ixgate_{w.lower()}.pkl")


if __name__ == "__main__":
    main()

"""daily_kbars 中 LESSONS 未明确测过的残余日线列三窗全市场快筛（只读，补 L-065 漏网）。

列：chip_winner_rate 获利盘比例、profit_efficiency 盈利效率、upper_shadow_count20 20日上影计数、
holder_pct 股东持股、atr。复用 newfactor_fullmarket.pkl 的毛 fwd5，三窗五分位/逐月同号/Spearman。
结果（2026-09 跑）：全部不达 L-065 逐月同号≥0.8 门槛——chip 非单调 rho≈0；profit_efficiency 全空；
upper_shadow_count20 冻结/run3 常量、仅前瞻有值；holder_pct 三窗同号仅 0.5 且前瞻覆盖 33%；atr 全空。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
WINS = [("冻结", "2025-01-15", "2026-01-16"),
        ("前瞻", "2026-02-09", "2026-08-05"),
        ("run3", "2026-08-07", "2026-09-02")]
COLS = ["chip_winner_rate", "profit_efficiency", "upper_shadow_count20", "holder_pct", "atr"]


def main():
    d = pd.read_pickle(OUT / "newfactor_fullmarket.pkl")[["code", "trade_date", "fwd5"]].copy()
    d["code"] = d.code.astype(str); d["trade_date"] = d.trade_date.astype(str)
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in (B / ".env").read_text().splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    e = create_engine(url)
    with e.connect() as c:
        k = pd.read_sql(text(f"SELECT code,trade_date,{','.join(COLS)} FROM daily_kbars "
                             "WHERE trade_date BETWEEN '2025-01-01' AND '2026-09-11'"), c)
    k["code"] = k.code.astype(str); k["trade_date"] = k.trade_date.astype(str)
    m = d.merge(k, on=["code", "trade_date"], how="inner")
    for col in COLS:
        print(f"\n== {col} (非空率{m[col].notna().mean():.2f} nunique={m[col].nunique()}) ==")
        for wn, a, b in WINS:
            x = m[(m.trade_date >= a) & (m.trade_date <= b) & m[col].notna() & m.fwd5.notna()].copy()
            if len(x) < 50000:
                print(f"  {wn} n={len(x)} 不足/常量"); continue
            try:
                x["qb"] = x.groupby("trade_date")[col].transform(
                    lambda s: pd.qcut(s, 5, labels=False, duplicates="drop"))
            except Exception:
                print("  qcut 失败"); continue
            if x.qb.nunique() < 2:
                print(f"  {wn} 单档常量"); continue
            g = x.groupby("qb").fwd5.mean()
            mm = x.groupby([x.trade_date.str[:7], "qb"]).fwd5.mean().unstack()
            dff = mm.iloc[:, -1] - mm.iloc[:, 0]
            same = (np.sign(dff) == np.sign(g.iloc[-1] - g.iloc[0])).mean()
            rho = x.groupby("trade_date").apply(
                lambda t: t[[col, "fwd5"]].corr(method="spearman").iloc[0, 1], include_groups=False).mean()
            print(f"  {wn} n{len(x)} Qhi-Qlo={g.iloc[-1]-g.iloc[0]:+.4f} 同号{same:.2f} rho={rho:+.4f} | "
                  + " ".join(f"{v:+.3f}" for v in g.values))


if __name__ == "__main__":
    main()

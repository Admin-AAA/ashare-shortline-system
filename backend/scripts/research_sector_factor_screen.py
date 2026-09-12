"""板块/行业横截面因子三窗全市场快筛（L-065 门槛：逐月同号≥0.8 才许进 canonical，只读）。

复用 newfactor_fullmarket.pkl（T+1open→T+5close 毛 fwd5，全市场，2025-01~2026-09），
code→industry 取 stocks 表（112 行业全覆盖）。构造：
  sec_today   行业当日等权均涨幅
  sec_upfrac  行业当日上涨家数占比（广度）
  sec_nlu     行业当日涨停家数（拥挤/高潮）
  sec_ret5    行业近5日等权动量（成员个股 ret5 的行业均值）
  sec_ret20   行业近20日动量
  sec_rank5   当日各行业 sec_ret5 的横截面分位（板块轮动位次）
  rs5         个股 ret5 - 行业 ret5（板块内相对强弱）
三窗（冻结/前瞻/run3）：日截面五分位 Qhi-Qlo、逐月同号、Spearman。毛收益只用于快杀因子。
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


def eng():
    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in (B / ".env").read_text().splitlines()
           if l.startswith("ASHARE_DATABASE_URL=")][0]
    return create_engine(url)


def screen(d, col, nm, q=5):
    for wn, a, b in WINS:
        x = d[(d.trade_date >= a) & (d.trade_date <= b) & d[col].notna() & d.fwd5.notna()]
        if len(x) < 50000:
            print(f"  {wn}: 样本不足 n={len(x)}"); continue
        x = x.copy(); x["qb"] = x.groupby("trade_date")[col].transform(lambda s: pd.qcut(s, q, labels=False, duplicates="drop"))
        g = x.groupby("qb").fwd5.mean(); lo, hi = g.iloc[0], g.iloc[-1]
        # 逐月同号：每月 Qhi-Qlo 的符号
        m = x.groupby([x.trade_date.str[:7], "qb"]).fwd5.mean().unstack()
        mdiff = (m.iloc[:, -1] - m.iloc[:, 0]); same = (np.sign(mdiff) == np.sign(hi - lo)).mean() if hi != lo else np.nan
        rho = x.groupby("trade_date").apply(lambda t: t[[col, "fwd5"]].corr(method="spearman").iloc[0, 1], include_groups=False).mean()
        mono = "单调" if g.is_monotonic_increasing or g.is_monotonic_decreasing else "非单调"
        print(f"  {wn} n={len(x)}: Qhi-Qlo={hi-lo:+.4f} 逐月同号{same:.2f} rho={rho:+.4f} {mono}")
        print(f"      分位均: " + " ".join(f"{v:+.3f}" for v in g.values))


def main():
    d = pd.read_pickle(OUT / "newfactor_fullmarket.pkl")
    d["code"] = d.code.astype(str); d["trade_date"] = d.trade_date.astype(str)
    d = d.sort_values(["code", "trade_date"])
    with eng().connect() as c:
        ind = pd.read_sql(text("SELECT code,industry FROM stocks"), c)
    ind["code"] = ind.code.astype(str); d = d.merge(ind, on="code", how="left")
    print("industry 覆盖率:", d.industry.notna().mean().round(3), "行业数", d.industry.nunique())
    # 个股 trailing 动量
    d["close"] = d.close.astype(float)
    d["ret5"] = d.groupby("code").close.pct_change(5)
    d["ret20"] = d.groupby("code").close.pct_change(20)
    # 行业日聚合
    grp = d.groupby(["trade_date", "industry"])
    sec = grp.agg(sec_today=("pct_chg", "mean"),
                  sec_upfrac=("pct_chg", lambda s: (s > 0).mean()),
                  sec_nlu=("sealed_up", "sum"),
                  sec_n=("code", "count"),
                  sec_ret5=("ret5", "mean"),
                  sec_ret20=("ret20", "mean")).reset_index()
    # 行业间当日横截面分位
    sec["sec_rank5"] = sec.groupby("trade_date").sec_ret5.rank(pct=True)
    sec["sec_rank_today"] = sec.groupby("trade_date").sec_today.rank(pct=True)
    sec["sec_lu_frac"] = sec.sec_nlu / sec_n_members(sec)
    d = d.merge(sec, on=["trade_date", "industry"], how="left")
    d["rs5"] = d.ret5 - d.sec_ret5
    feats = [("sec_today", "行业当日均涨幅"), ("sec_upfrac", "行业当日上涨广度"),
             ("sec_nlu", "行业当日涨停家数"), ("sec_ret5", "行业近5日动量"),
             ("sec_ret20", "行业近20日动量"), ("sec_rank5", "行业动量当日位次"),
             ("sec_rank_today", "行业当日强度位次"), ("rs5", "板块内相对强弱rs5")]
    for col, nm in feats:
        print(f"\n===== {nm} ({col}) =====")
        screen(d, col, nm)
    d[["code", "trade_date", "industry", "fwd5"] + [c for c, _ in feats] + ["rs5"]].to_pickle(
        OUT / "sector_factor_panel.pkl")
    print("\nsaved sector_factor_panel.pkl")


def sec_n_members(sec):
    return sec.sec_n.clip(lower=1)


if __name__ == "__main__":
    main()

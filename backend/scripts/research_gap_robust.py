"""L-081 gap 候选稳健性（只读）：控制 pct_chg/chg20 后是否仍排序；正负拆分；逐月同号；阈值。"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"


def boot(d, draws=5000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(d, float); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return tuple(np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5]))


def build():
    panel = pd.read_pickle(OUT / "sel_daily_feature_panel.pkl")
    panel = panel.sort_values(["code", "dt"])
    panel["close20"] = panel.groupby("code").close.shift(20)
    panel["chg20c"] = panel.close / panel.close20 - 1
    out = {}
    for name, f in [("FROZEN", "canon_frozen_v2_selected.pkl"),
                    ("FORWARD", "canon_forward_v2_selected.pkl"),
                    ("RUN3", "run3_v2_selected.pkl")]:
        sel = pd.read_pickle(OUT / f)[["dt", "code", "net"]]
        sel["dt"] = sel.dt.astype(str); sel["code"] = sel.code.astype(str)
        m = sel.merge(panel[["dt", "code", "gap", "pct_chg", "chg20c", "clr"]], on=["dt", "code"], how="left")
        out[name] = m
    return out


def terc(d, col, label="net"):
    x = d.dropna(subset=[col]).copy()
    x["q"] = pd.qcut(x[col].rank(method="first"), 3, labels=["Q1", "Q2", "Q3"])
    return x.groupby("q", observed=True)[label].agg(["size", "mean", lambda s: (s > 0).mean()]).round(3)


def main():
    W = build()
    for name, d in W.items():
        print(f"\n========== {name} n={len(d)} ==========")
        print("corr gap/pct_chg=%.3f gap/chg20=%.3f pct_chg/chg20=%.3f" % (
            d[["gap", "pct_chg"]].corr().iloc[0, 1], d[["gap", "chg20c"]].corr().iloc[0, 1],
            d[["pct_chg", "chg20c"]].corr().iloc[0, 1]))
        # 1) gap 正负拆分
        for cond, lab in [(d.gap > 0, "gap>0 高开"), (d.gap <= 0, "gap<=0 平/低开")]:
            x = d[cond]
            print(f"  {lab:12s} n={len(x):4d} 均{x.net.mean():+.3f}/胜{(x.net>0).mean():.3f}/天{x.dt.nunique()}")
        # 2) 控制 pct_chg 后 gap 是否仍排序（双重排序：先按 pct_chg 三分位，组内再分 gap 高低）
        x = d.dropna(subset=["gap", "pct_chg"]).copy()
        x["pc_q"] = pd.qcut(x.pct_chg.rank(method="first"), 3, labels=["pc1", "pc2", "pc3"])
        x["gap_hi"] = x.groupby("pc_q", observed=True).gap.transform(lambda s: s > s.median())
        piv = x.groupby(["pc_q", "gap_hi"], observed=True).net.agg(["size", "mean"]).round(3)
        print("  -- 控制pct_chg: 每个涨幅档内 gap高/低 的净收益 --"); print(piv.to_string())
        # 3) 控制 chg20 后
        y = d.dropna(subset=["gap", "chg20c"]).copy()
        y["c_q"] = pd.qcut(y.chg20c.rank(method="first"), 3, labels=["c1", "c2", "c3"])
        y["gap_hi"] = y.groupby("c_q", observed=True).gap.transform(lambda s: s > s.median())
        piv2 = y.groupby(["c_q", "gap_hi"], observed=True).net.agg(["size", "mean"]).round(3)
        print("  -- 控制chg20: 每个chg20档内 gap高/低 --"); print(piv2.to_string())
        # 4) gap 逐月同号（gap>0 vs <=0 的日cohort差）
        x = d.dropna(subset=["gap"]).copy(); x["mo"] = x.dt.str[:7]
        hi = x[x.gap > 0].groupby("mo").net.mean(); lo = x[x.gap <= 0].groupby("mo").net.mean()
        j = pd.concat([hi.rename("hi"), lo.rename("lo")], axis=1)
        j["hi_better"] = j.hi > j.lo
        print("  逐月 高开优于平低开: %d/%d 月" % (j.hi_better.sum(), j.hi_better.notna().sum()))


if __name__ == "__main__":
    main()

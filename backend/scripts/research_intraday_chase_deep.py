"""日内追高/躁动复合因子深挖（只读探索）。
- 三因子相关与 z 复合；五分位单调性；毒尾（顶档）两窗净收益/胜率
- 是否只是 v2 已拦高潮的残留（按 base/sent 分解）
- 外科否决：v2 选中里剔除复合顶档，配对日 cohort bootstrap CI、月度同号、单日聚集
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
from scipy import stats
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
SUB = ["pm_ret", "rvol", "close_off_high"]


def zscore(s):
    s = pd.to_numeric(s, errors="coerce")
    return (s - s.mean()) / s.std(ddof=0)


def boot_ci(diff, draws=5000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(diff); n = len(d)
    if n < 3: return (np.nan, np.nan)
    means = [d[rng.integers(0, n, n)].mean() for _ in range(draws)]
    return np.percentile(means, [2.5, 97.5])


def quint(df, feat, q=5):
    x = df[[feat, "net"]].dropna()
    try:
        x["g"] = pd.qcut(x[feat], q, labels=False, duplicates="drop")
    except Exception:
        return
    t = x.groupby("g").net.agg(["mean", "count", lambda y: (y > 0).mean()])
    t.columns = ["mean", "n", "win"]
    print(f"  {feat:16s} 五分位: " + " | ".join(
        f"Q{i}:{r['mean']:+.2f}/w{r['win']:.2f}/n{int(r['n'])}" for i, r in t.iterrows()))


def main():
    p = pd.read_pickle(OUT / "intraday_selected_panel.pkl")
    fz, fw = p[p.win == "frozen"].copy(), p[p.win == "forward"].copy()
    print("=== 三因子相关（frozen / forward）===")
    print(" frozen:\n", fz[SUB + ["net"]].corr(method="spearman").round(3).loc[SUB, SUB + ["net"]])
    print(" forward:\n", fw[SUB + ["net"]].corr(method="spearman").round(3).loc[SUB, SUB + ["net"]])

    # 复合：三因子窗内 z 求和（方向都为"越大越追高→越差"，close_off_high 已同向）
    for d in (fz, fw):
        d["comp"] = sum(zscore(d[f]) for f in SUB) / len(SUB)
        d["base"] = d.key.str.split("·").str[0]
        d["sent"] = d.key.str.split("·").str[1]
    print("\n=== 五分位单调性（Q0 低追高 → Q4 高追高）===")
    print("-- frozen --"); [quint(fz, f) for f in SUB + ["comp"]]
    print("-- forward --"); [quint(fw, f) for f in SUB + ["comp"]]

    # 复合顶档（追高最甚）按 regime 分解，确认非 v2 高潮残留
    print("\n=== 复合 comp 顶五分位 Q4 按 base 分解（净收益/笔数）===")
    for name, d in [("frozen", fz), ("forward", fw)]:
        x = d.dropna(subset=["comp"]); hi = x[x.comp >= x.comp.quantile(0.8)]
        lo = x[x.comp <= x.comp.quantile(0.2)]
        print(f" {name}: Q4均{hi.net.mean():+.3f}/w{(hi.net>0).mean():.2f}/n{len(hi)}  "
              f"Q0均{lo.net.mean():+.3f}/n{len(lo)}  全均{x.net.mean():+.3f}")
        for base, g in hi.groupby("base"):
            print(f"    Q4 {base}: {g.net.mean():+.3f}/w{(g.net>0).mean():.2f}/n{len(g)}")

    # 外科否决：剔除"追高"顶 q 分位，配对日 cohort
    print("\n=== 外科否决：v2 选中剔除追高顶档（日 cohort 等权，空仓记0）===")
    fzsel = pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl"); fzsel["dt"] = fzsel.dt.astype(str)
    fwsel = pd.read_pickle(OUT / "canon_forward_v2_selected.pkl"); fwsel["dt"] = fwsel.dt.astype(str)
    all_dates = {"frozen": sorted(fzsel.dt.unique()), "forward": sorted(fwsel.dt.unique())}
    for feat in ["pm_ret", "comp"]:
        print(f" ---- 因子 {feat} ----")
        for qcut in (0.1, 0.2, 0.33):
            for name, d in [("frozen", fz), ("forward", fw)]:
                x = d.dropna(subset=[feat]).copy()
                thr = x[feat].quantile(1 - qcut)
                keep = x[x[feat] < thr]
                base_co = x.groupby("dt").net.mean().reindex(all_dates[name]).fillna(0)
                new_co = keep.groupby("dt").net.mean().reindex(all_dates[name]).fillna(0)
                touched = x.groupby("dt")[feat].apply(lambda s: (s >= thr).any())
                chg_days = (new_co - base_co)[touched[touched].index]
                lo, hi = boot_ci(chg_days.values)
                cut = x[x[feat] >= thr]
                md = (new_co - base_co); md.index = md.index.astype(str).str[:7]
                ms = md.groupby(level=0).sum(); msign = max((ms > 0).mean(), (ms < 0).mean())
                maxday = cut.groupby("dt").size().max() if len(cut) else 0
                print(f"  {name:7s} 顶{int(qcut*100)}%: 砍{len(cut)}笔均{cut.net.mean():+.3f}/w{(cut.net>0).mean():.2f} "
                      f"日{base_co.mean():+.4f}->{new_co.mean():+.4f} 变化日{len(chg_days)} CI[{lo:+.3f},{hi:+.3f}] 月同号{msign:.2f} 最大单日砍{maxday}")


if __name__ == "__main__":
    main()

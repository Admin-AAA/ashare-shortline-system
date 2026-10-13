"""尾盘追高效应稳健性终审（只读）：
1) 窗内标准化后池化连续 IC（按日 block bootstrap）
2) 日内相对分位否决（每天剔 pm 最高且>T）配对 CI
3) 候选配置留一月稳定性（逐月剔除后日改善符号）
4) 最优可辩护配置的累计/MDD（风险调整）
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"
SLEEVE = 1 / 6


def boot_ci(diff, draws=8000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(diff); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5])


def cum_mdd(co):
    eq = (1 + co * SLEEVE / 100).cumprod()
    return round((eq.iloc[-1] - 1) * 100, 2), round(((eq / eq.cummax() - 1) * 100).min(), 2)


def main():
    p = pd.read_pickle(OUT / "intraday_selected_panel.pkl")
    fzd = sorted(pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl").dt.astype(str).unique())
    fwd_d = sorted(pd.read_pickle(OUT / "canon_forward_v2_selected.pkl").dt.astype(str).unique())
    # 窗内 z 标准化
    p["pmz"] = p.groupby("win").pm_ret.transform(lambda s: (s - s.mean()) / s.std(ddof=0))

    print("=== 1) 池化连续 IC（按日 Spearman，block bootstrap by day）===")
    for win, d in [("frozen", p[p.win == "frozen"]), ("forward", p[p.win == "forward"]),
                   ("pooled", p)]:
        daily = d.dropna(subset=["pmz"]).groupby("dt").apply(
            lambda g: pd.Series({"ic": g[["pmz", "net"]].corr(method="spearman").iloc[0, 1]}) if len(g) >= 4 else pd.Series({"ic": np.nan}),
            include_groups=False).dropna()
        ic = daily.ic.values
        lo, hi = boot_ci(ic)
        t = ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic)))
        print(f" {win:8s} 日IC均{ic.mean():+.4f} t={t:+.2f} CI[{lo:+.4f},{hi:+.4f}] 负IC日占比{(ic<0).mean():.2f} n_days={len(ic)}")

    print("\n=== 2) 日内相对否决：每天剔 pmz 最高且 pm_ret>T，配对 CI ===")
    for T in (1.0, 1.5):
        for win, d, ad in [("frozen", p[p.win == "frozen"], fzd), ("forward", p[p.win == "forward"], fwd_d)]:
            x = d.dropna(subset=["pm_ret"]).copy()
            idx_hi = x.groupby("dt").pm_ret.idxmax()
            drop_keys = x.loc[idx_hi].query("pm_ret > @T").set_index(["dt", "code"]).index
            x["k"] = list(zip(x.dt, x.code))
            keep = x[~x.k.isin(drop_keys)]
            b = x.groupby("dt").net.mean().reindex(ad).fillna(0)
            n = keep.groupby("dt").net.mean().reindex(ad).fillna(0)
            touched = sorted({dt for dt, _ in drop_keys})
            chg = (n - b).loc[touched]; lo, hi = boot_ci(chg.values)
            cut = x[x.k.isin(drop_keys)]
            print(f" T={T} {win:7s} 剔{len(cut)}笔均{cut.net.mean():+.3f}/w{(cut.net>0).mean():.2f} 日{b.mean():+.4f}->{n.mean():+.4f} CI[{lo:+.3f},{hi:+.3f}]")

    print("\n=== 3) 候选配置[弱活+强高潮, T=1.5] 留一月稳定性 ===")
    cells = ["弱势·活跃", "强牛·高潮"]
    for win, d, ad in [("frozen", p[p.win == "frozen"], fzd), ("forward", p[p.win == "forward"], fwd_d)]:
        x = d.dropna(subset=["pm_ret"]).copy(); x["m"] = x.dt.str[:7]
        tox = x.key.isin(cells) & (x.pm_ret > 1.5)
        b = x.groupby("dt").net.mean().reindex(ad).fillna(0)
        n = x[~tox].groupby("dt").net.mean().reindex(ad).fillna(0)
        md = (n - b); md.index = md.index.astype(str).str[:7]
        per = md.groupby(level=0).sum()
        print(f" {win}: 逐月日改善 -> ", {m: round(v, 2) for m, v in per.items() if abs(v) > 1e-9})
        print(f"    正贡献月{(per>0).sum()}/负贡献月{(per<0).sum()}")
        cb, mb = cum_mdd(b); cn, mn = cum_mdd(n)
        print(f"    cum {cb:+.2f}->{cn:+.2f}  MDD {mb:+.2f}->{mn:+.2f}")

    print("\n=== 4) 该配置逐笔/胜率/PF 两窗 ===")
    for win, d in [("frozen", p[p.win == "frozen"]), ("forward", p[p.win == "forward"])]:
        x = d.dropna(subset=["pm_ret"]).copy(); tox = x.key.isin(cells) & (x.pm_ret > 1.5)
        for nm, g in [("baseline", x), ("veto", x[~tox])]:
            w, l = g.net[g.net > 0].sum(), abs(g.net[g.net < 0].sum())
            print(f" {win} {nm:8s} n{len(g):>3} 逐笔{g.net.mean():+.4f} win{(g.net>0).mean():.3f} PF{w/l:.3f}")


if __name__ == "__main__":
    main()

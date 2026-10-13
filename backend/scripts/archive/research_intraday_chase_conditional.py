"""条件化尾盘追高否决：仅在两窗皆毒的格内、对高 pm_ret 票否决（只读探索）。
遍历 格集合 × pm 阈值，报告两窗：砍票均/胜、日 cohort 变化、配对日 CI、月同号、单日聚集。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"


def boot_ci(diff, draws=5000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(diff); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5])


def main():
    p = pd.read_pickle(OUT / "intraday_selected_panel.pkl")
    fzd = sorted(pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl").dt.astype(str).unique())
    fwd_d = sorted(pd.read_pickle(OUT / "canon_forward_v2_selected.pkl").dt.astype(str).unique())
    fz, fw = p[p.win == "frozen"].copy(), p[p.win == "forward"].copy()

    def run(d, cells, T, ad):
        x = d.dropna(subset=["pm_ret"]).copy()
        toxic = x.key.isin(cells) & (x.pm_ret > T)
        keep = x[~toxic]; cut = x[toxic]
        b = x.groupby("dt").net.mean().reindex(ad).fillna(0)
        n = keep.groupby("dt").net.mean().reindex(ad).fillna(0)
        touched = sorted(set(cut.dt))
        chg = (n - b).loc[touched]
        lo, hi = boot_ci(chg.values)
        md = (n - b); md.index = md.index.astype(str).str[:7]
        ms = md.groupby(level=0).sum(); msign = max((ms > 0).mean(), (ms < 0).mean())
        maxday = cut.groupby("dt").size().max() if len(cut) else 0
        return len(cut), cut.net.mean(), (cut.net > 0).mean(), b.mean(), n.mean(), len(chg), lo, hi, msign, maxday

    cell_sets = {
        "三毒格(弱活/强活/强高潮)": ["弱势·活跃", "强牛·活跃", "强牛·高潮"],
        "弱活+强高潮": ["弱势·活跃", "强牛·高潮"],
        "仅弱势活跃": ["弱势·活跃"],
        "全部格(对照)": None,
    }
    for csn, cells in cell_sets.items():
        print(f"\n#### {csn} ####")
        for T in (1.0, 1.5, 2.0):
            line = f" T={T}: "
            for name, d, ad in [("fz", fz, fzd), ("fw", fw, fwd_d)]:
                cc = d.key.unique() if cells is None else cells
                nc, cm, cw, b, nn, nd, lo, hi, ms, md_ = run(d, cc, T, ad)
                line += (f"{name}砍{nc:>3}均{cm:+.3f}/w{cw:.2f} 日{b:+.4f}->{nn:+.4f} "
                         f"Δ日{nd} CI[{lo:+.3f},{hi:+.3f}] 月{ms:.2f} 最大日{md_}  ||  ")
            print(line)


if __name__ == "__main__":
    main()

"""尾盘追高否决：细网格稳健高原扫描（只读）。
格集合 × 固定 pm 阈值，标记两窗是否同时：砍票为负、逐笔改善、配对日CI下界>0、
前瞻全月正贡献。看通过区是否连续（非孤立刀锋）。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"


def boot_ci(diff, draws=4000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(diff); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5])


def eval_cfg(d, cells, T, ad):
    x = d.dropna(subset=["pm_ret"]).copy()
    tox = (x.key.isin(cells)) & (x.pm_ret > T)
    cut, keep = x[tox], x[~tox]
    if len(cut) < 8: return None
    b = x.groupby("dt").net.mean().reindex(ad).fillna(0)
    n = keep.groupby("dt").net.mean().reindex(ad).fillna(0)
    touched = sorted(set(cut.dt)); chg = (n - b).loc[touched]; lo, hi = boot_ci(chg.values)
    md = (n - b); md.index = md.index.astype(str).str[:7]; per = md.groupby(level=0).sum()
    return dict(ncut=len(cut), cutm=cut.net.mean(), base=x.net.mean(), veto=keep.net.mean(),
                lo=lo, hi=hi, mpos=(per > 0).sum(), mneg=(per < 0).sum())


def main():
    p = pd.read_pickle(OUT / "intraday_selected_panel.pkl")
    fzd = sorted(pd.read_pickle(OUT / "canon_frozen_v2_selected.pkl").dt.astype(str).unique())
    fwd_d = sorted(pd.read_pickle(OUT / "canon_forward_v2_selected.pkl").dt.astype(str).unique())
    fz, fw = p[p.win == "frozen"], p[p.win == "forward"]
    cell_grid = {
        "弱活": ["弱势·活跃"],
        "弱活+强高潮": ["弱势·活跃", "强牛·高潮"],
        "弱活+强活+强高潮": ["弱势·活跃", "强牛·活跃", "强牛·高潮"],
        "弱活+强活": ["弱势·活跃", "强牛·活跃"],
        "强高潮": ["强牛·高潮"],
        "非偏多非震荡(弱+强牛)": ["弱势·活跃", "弱势·平淡", "强牛·活跃", "强牛·高潮"],
    }
    Ts = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]
    print(f"{'cells':22s} {'T':5s} | FZ 砍均/逐笔Δ/CI下界/月+- | FW 砍均/逐笔Δ/CI下界/月+- | PASS")
    for cn, cells in cell_grid.items():
        for T in Ts:
            rz, rw = eval_cfg(fz, cells, T, fzd), eval_cfg(fw, cells, T, fwd_d)
            if not rz or not rw: continue
            pass_ = (rz["cutm"] < 0 and rw["cutm"] < 0 and rz["veto"] > rz["base"] and
                     rw["veto"] > rw["base"] and rz["lo"] > 0 and rw["lo"] > 0)
            print(f"{cn:22s} {T:5.2f} | {rz['cutm']:+.2f}/{rz['veto']-rz['base']:+.3f}/{rz['lo']:+.3f}/{rz['mpos']}-{rz['mneg']} "
                  f"| {rw['cutm']:+.2f}/{rw['veto']-rw['base']:+.3f}/{rw['lo']:+.3f}/{rw['mpos']}-{rw['mneg']} | {'✓PASS' if pass_ else ''}")
        print()


if __name__ == "__main__":
    main()

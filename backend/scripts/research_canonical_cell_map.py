"""双窗口 canonical 选中层 16 格图谱（只读）。

对 v1/v2 分别用生产同构口径取"实际选中交易"，按 regime·情绪 16 格汇总
逐笔等权 avg_net/胜率/PF/n，并跨冻结/前瞻两窗口对齐，找：
 (A) 两窗口选中层都为负、且当前仍在出票的格 → 外科否决候选；
 (B) 当前被拦但两窗口为正的格 → 误杀候选（谨慎）。
选中帧落盘缓存，避免重复跑 _run_arm。
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy
from scripts.research_canonical_two_window import (
    frozen_selected, forward_selected, build_forward_canonical, build_market_snapshots, trade_metrics)

OUT = B / "output" / "research"


def cell_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for k, g in df.groupby("key"):
        m = trade_metrics(g)
        rows.append({"key": k, "n": m["n"], "avg": m["avg_net"], "win": m["win"], "pf": m["pf"]})
    t = pd.DataFrame(rows).set_index("key").sort_index()
    return t


def main():
    v1 = load_production_selection_policy(version=1)
    v2 = load_production_selection_policy(version=2)
    # frozen
    fz1, fz2 = frozen_selected(v1), frozen_selected(v2)
    fz1.to_pickle(OUT / "canon_frozen_v1_selected.pkl"); fz2.to_pickle(OUT / "canon_frozen_v2_selected.pkl")
    # forward
    fw = build_forward_canonical()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]; fw = fw[fw.dt.isin(have)]
    fw1, fw2 = forward_selected(fw, snaps, v1), forward_selected(fw, snaps, v2)
    fw1.to_pickle(OUT / "canon_forward_v1_selected.pkl"); fw2.to_pickle(OUT / "canon_forward_v2_selected.pkl")

    tf1, tf2 = cell_table(fz1), cell_table(fz2)
    wf1, wf2 = cell_table(fw1), cell_table(fw2)
    print("=" * 100)
    print("FROZEN 选中层（_run_arm canonical）  v1 → v2")
    jf = tf1.join(tf2, lsuffix="_v1", rsuffix="_v2", how="outer").fillna("–")
    print(jf.to_string())
    print("=" * 100)
    print("FORWARD 选中层（evaluate_selection_policy 重放 + canonical 净收益） v1 → v2")
    jw = wf1.join(wf2, lsuffix="_v1", rsuffix="_v2", how="outer").fillna("–")
    print(jw.to_string())
    print("=" * 100)
    # 对齐：v2 下仍出票的格，两窗口选中层表现
    print("\n>>> v2 下仍出票的格 × 两窗口选中层 avg（找两窗口都为负的残留毒格）")
    keys = sorted(set(fz2.key) | set(fw2.key))
    for k in keys:
        a = fz2[fz2.key == k]; b = fw2[fw2.key == k]
        av = a.net.mean() if len(a) else None; bv = b.net.mean() if len(b) else None
        def fmt(x, n):
            return f"{x:+.3f}/n{n}" if x is not None else "  –  "
        flag = ""
        if av is not None and bv is not None and av < 0 and bv < 0:
            flag = "  <<< 两窗口皆负(候选否决)"
        print(f"  {k:9s} frozen {fmt(av,len(a)):>14s}  forward {fmt(bv,len(b)):>14s}{flag}")


if __name__ == "__main__":
    main()

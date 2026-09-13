"""冲突格内部【分数带】+ 每日出票数 output_count 的双窗口检验（只读，canonical 数据）。

A: 偏多·活跃/强牛·活跃/弱势·活跃 等跨窗冲突格，整体冻结为正、前瞻为负；看其内部
   分数带是否存在"两窗口都负"的低分边缘带（可只抬该带，不整格否决）。
D: daily_output_count（选几层出票上限，非仓位）在两窗口的最优区间，要求两窗口同向。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.aggregator import _market_base, _market_sentiment
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots, SLEEVE, _bootstrap_ci

OUT = B / "output" / "research"
BANDS = [(60, 68), (68, 72), (72, 76), (76, 82), (82, 200)]


def band_table(df, cells):
    for cell in cells:
        sub = df[df.key == cell]
        if not len(sub):
            print(f"  {cell}: 无"); continue
        line = f"  {cell:9s} n{len(sub):>4} 均{sub.net.mean():+.3f} | "
        for lo, hi in BANDS:
            z = sub[(sub.score >= lo) & (sub.score < hi)]
            if len(z):
                line += f"[{lo}-{hi if hi<200 else '+'}){z.net.mean():+.2f}/w{(z.net>0).mean():.2f}/n{len(z)} "
        print(line)


def cum(co):
    eq = (1 + co * SLEEVE / 100).cumprod()
    return round((eq.iloc[-1] - 1) * 100, 2), round(((eq / eq.cummax() - 1) * 100).min(), 2)


def main():
    fz_all = pd.read_pickle(OUT / "canon_frozen_all_candidates.pkl")
    # forward 全候选（score>=70）+ base/sent
    fw = build_forward_canonical(); dates = sorted(fw.dt.unique())
    snaps = build_market_snapshots(dates); have = [d for d in dates if d in snaps]
    fw = fw[fw.dt.isin(have)].copy()
    fw["base"] = fw.dt.map(lambda d: _market_base(snaps[d])); fw["sent"] = fw.dt.map(lambda d: _market_sentiment(snaps[d]))
    fw["key"] = fw.base + "·" + fw.sent
    conflict = ["偏多·活跃", "强牛·活跃", "弱势·活跃", "强牛·高潮", "弱势·平淡"]
    print("===== A. 冲突格内部分数带（冻结=全候选可成交；前瞻=全候选 score>=70）=====")
    print("-- frozen 全候选 --"); band_table(fz_all, conflict)
    print("-- forward 全候选 --"); band_table(fw, conflict)
    # 也看 v2 已否决格在冻结全候选的分数带（佐证硬排除）
    print("-- frozen 全候选【高潮格】分数带（应多数为负，佐证 v2）--")
    band_table(fz_all, ["偏多·高潮", "震荡·高潮", "弱势·高潮"])

    # D: output_count —— 用 v2 选中帧，按每日 final_score 排序取 topK
    print("\n===== D. 每日出票上限 topK（v2 选中帧，日 cohort 等权，空日记0）=====")
    for name, path, all_dates in [("frozen", "canon_frozen_v2_selected.pkl", None),
                                  ("forward", "canon_forward_v2_selected.pkl", have)]:
        sel = pd.read_pickle(OUT / path)
        dates_ = sorted(sel.dt.unique()) if all_dates is None else all_dates
        rows = []
        for K in (2, 3, 4, 5, 6, 8, 10):
            top = sel.sort_values("score", ascending=False).groupby("dt").head(K)
            co = top.groupby("dt").net.mean().reindex(dates_).fillna(0.0)
            c, m = cum(co)
            tr = top.net
            wsum, lsum = tr[tr > 0].sum(), abs(tr[tr < 0].sum())
            print(f"  {name:7s} topK={K:<2d} days{len(co):>3} 出票{(co!=0).sum():>3} cum{c:+.2f} MDD{m:+.2f} 逐笔均{tr.mean():+.4f} win{(tr>0).mean():.3f} PF{round(wsum/lsum,3) if lsum else None}")
        print()


if __name__ == "__main__":
    main()

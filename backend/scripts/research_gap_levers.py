"""L-081 gap 杠杆日cohort定量（只读手写cohort，用于决定杠杆形态；正式结论仍走 canonical）。
对比 baseline vs 多种 gap 处理：逐笔/日cum(SLEEVE)/新增空仓天/被砍票机会成本/配对日差CI。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
OUT = B / "output" / "research"; SLEEVE = 1 / 6


def boot(d, draws=5000, seed=7):
    rng = np.random.default_rng(seed); d = np.asarray(d, float); n = len(d)
    if n < 3: return (np.nan, np.nan)
    return tuple(np.percentile([d[rng.integers(0, n, n)].mean() for _ in range(draws)], [2.5, 97.5]))


def build():
    panel = pd.read_pickle(OUT / "sel_daily_feature_panel.pkl")
    out = {}
    for name, f in [("FROZEN", "canon_frozen_v2_selected.pkl"),
                    ("FORWARD", "canon_forward_v2_selected.pkl"),
                    ("RUN3", "run3_v2_selected.pkl")]:
        sel = pd.read_pickle(OUT / f)[["dt", "code", "net"]]
        sel["dt"] = sel.dt.astype(str); sel["code"] = sel.code.astype(str)
        out[name] = sel.merge(panel[["dt", "code", "gap"]], on=["dt", "code"], how="left")
    return out


def daily(df, mask, all_dates):
    kept = df[mask]
    coh = kept.groupby("dt").net.mean().reindex(all_dates).fillna(0)
    return kept, coh


def report(name, df):
    dates = sorted(df.dt.unique())
    base, cb = daily(df, [True] * len(df), dates)
    variants = {
        "veto gap<=0": df.gap > 0,
        "veto gap<-1%": df.gap >= -0.01,
        "veto gap<-2%": df.gap >= -0.02,
        "veto 最低gap三分位": df.gap >= df.gap.quantile(1/3),
    }
    print(f"\n===== {name}  baseline 逐笔{base.net.mean():+.4f} 日cum{cb.sum()*SLEEVE:+.2f} 出票天{(base.groupby('dt').size()>0).sum()} =====")
    for vn, mk in variants.items():
        kept, ck = daily(df, mk.fillna(False), dates)
        removed = df[~mk.fillna(False)]
        touched = sorted(set(base.dt) - set(kept.dt))
        chg = (ck - cb).loc[touched]
        lo, hi = boot(chg.values)
        empty_added = len(touched)
        print(f" {vn:18s} 逐笔{kept.net.mean():+.4f}(n{len(kept)}) 日cum{ck.sum()*SLEEVE:+.2f} "
              f"砍{len(removed)}笔均{removed.net.mean():+.3f}/胜{(removed.net>0).mean():.2f} "
              f"新增空仓天{empty_added} 配对Δ{chg.mean():+.4f}CI[{lo:+.3f},{hi:+.3f}]")


def rerank_report(name, df, keep_n=10):
    """模拟 top-N 截断：每日按 final 排序取前 keep_n；gap 惩罚=低gap后移，看边际替换收益。
    无 final_score 时用：baseline 取全部（v2已≤10），gap-aware 每日剔除 gap 最低的溢出票。
    这里只统计"当日候选数>keep_n 时，被 gap 规则挤掉/换入"的边际差。"""
    cnt = df.groupby("dt").size()
    crowded = cnt[cnt > keep_n].index
    d = df[df.dt.isin(crowded)].copy()
    if d.empty:
        print(f" {name}: 无>{keep_n}的拥挤日，重排序杠杆 inert"); return
    print(f" {name}: 拥挤(>{keep_n})日 {len(crowded)} 天，涉及{len(d)}笔")


def main():
    W = build()
    for name, d in W.items():
        report(name, d)
        rerank_report(name, d)


if __name__ == "__main__":
    main()

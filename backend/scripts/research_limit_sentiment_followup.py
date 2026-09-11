"""追踪检验（只读）：在主检验为阴性后，严谨排除"构造错误"。

A. 构念效度：涨停情绪特征能否预测【全市场等权】未来 1 日 / 5 日收益？
   - 若连全市场都预测不了 → 特征本身无前瞻信息（非选择效应问题）。
   - 若能预测全市场、却预测不了选中 cohort → L-064 选择效应，对选股系统仍无效。
B. regime 条件化：在每个现有 market_base 内，炸板率/连板高度三分位是否存在稳定单调格子
   （L-060 的 regime×情绪二维；主检验 by_regime 提示偏多·毒日为负、强牛/震荡为正）。
C. 滞后（T-1）情绪对 T+1 cohort 的预测，排除"当日混杂、滞后才干净"。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_limit_sentiment_panel import (  # type: ignore
    load_raw, build_daily_flags, OUT, PANEL)

FEATS = ["broken_rate_ma3", "broken_rate_strict_ma3", "seal_rate_ma3", "height_ma3",
         "d_max_height", "ladder2", "d_lu_n", "lu_ld_ratio", "ld_n", "d_broken_rate"]
DIRN = {"broken_rate_ma3": -1, "broken_rate_strict_ma3": -1, "seal_rate_ma3": 1, "height_ma3": 1,
        "d_max_height": 1, "ladder2": 1, "d_lu_n": 1, "lu_ld_ratio": 1, "ld_n": -1, "d_broken_rate": -1}


def market_forward():
    raw = load_raw("2025-01-01")
    fl = build_daily_flags(raw)
    fl = fl[fl.tradable_univ]
    mkt = fl.groupby("trade_date")["pct_chg"].mean().sort_index()
    fwd1 = mkt.shift(-1)
    fwd5 = sum(mkt.shift(-k) for k in range(1, 6))  # T+1..T+5 累计等权收益
    panel = pd.read_pickle(PANEL).set_index("trade_date")
    m = pd.DataFrame({"mkt_t": mkt, "fwd1": fwd1, "fwd5": fwd5}).join(panel, how="inner").dropna(subset=["fwd1"])
    return m


def construct_validity(m: pd.DataFrame):
    dates = sorted(m.index)
    split = dates[int(len(dates) * 0.6)]
    print(f"[A] 全市场前瞻  split={split} n={len(m)}")
    print(f"{'feature':24s} {'dir':>3} {'IC_fwd1':>8} {'IC_fwd5':>8} {'mo1':>5} {'oosHL_1':>8} {'oosHL_5':>8}")
    for f in FEATS:
        d = m.dropna(subset=[f]).copy()
        tr, oo = d[d.index <= split], d[d.index > split]
        ql, qh = tr[f].quantile(1/3), tr[f].quantile(2/3)
        dirn = DIRN[f]
        def hl(df, tgt):
            hi = df[df[f] >= qh][tgt]; lo = df[df[f] <= ql][tgt]
            return dirn * (hi.mean() - lo.mean()) if len(hi) and len(lo) else np.nan
        ic1 = dirn * d[f].corr(d["fwd1"], method="spearman")
        ic5 = dirn * d[f].corr(d["fwd5"], method="spearman")
        d["month"] = d.index.str[:7]
        mo = []
        for _, gp in d.groupby("month"):
            hi = gp[gp[f] >= qh]["fwd1"]; lo = gp[gp[f] <= ql]["fwd1"]
            if len(hi) and len(lo): mo.append(dirn*(hi.mean()-lo.mean()))
        cons = np.mean(np.array(mo) > 0) if mo else np.nan
        print(f"{f:24s} {dirn:>3} {ic1:>8.3f} {ic5:>8.3f} {cons:>5.2f} {hl(oo,'fwd1'):>8.3f} {hl(oo,'fwd5'):>8.3f}")


def regime_conditional():
    coh = pd.read_pickle(OUT / "limit_sentiment_cohort.pkl").dropna(subset=["basket"])
    print("\n[B] regime 条件化：各 market_base × 特征三分位 的 cohort 均净收益%（n）")
    for f in ["broken_rate_ma3", "height_ma3", "d_lu_n", "ld_n"]:
        print(f"\n  特征 {f}（方向 {DIRN[f]:+d}）")
        for rg in ["强牛", "偏多", "震荡", "弱势"]:
            sub = coh[coh.market_base == rg].dropna(subset=[f])
            if len(sub) < 15:
                continue
            try:
                q = pd.qcut(sub[f], 3, labels=["L", "M", "H"], duplicates="drop")
            except Exception:
                continue
            cells = sub.groupby(q, observed=True)["basket"].agg(["mean", "count"])
            line = "    " + f"{rg:4s} "
            for idx in cells.index:
                line += f"{idx}:{cells.loc[idx,'mean']:+.2f}(n{int(cells.loc[idx,'count'])})  "
            print(line)


def lagged():
    coh = pd.read_pickle(OUT / "limit_sentiment_cohort.pkl").dropna(subset=["basket"]).sort_values("signal_date")
    for f in ["broken_rate_ma3", "height_ma3", "d_lu_n"]:
        coh[f+"_lag1"] = coh[f].shift(1)
    dates = sorted(coh.signal_date.unique()); split = dates[int(len(dates)*0.6)]
    print("\n[C] T-1 滞后情绪 → cohort（训练/OOS 方向化 HL）")
    for f in ["broken_rate_ma3_lag1", "height_ma3_lag1", "d_lu_n_lag1"]:
        d = coh.dropna(subset=[f]); tr, oo = d[d.signal_date<=split], d[d.signal_date>split]
        ql, qh = tr[f].quantile(1/3), tr[f].quantile(2/3); dirn = DIRN[f.replace("_lag1","")]
        def hl(x):
            hi=x[x[f]>=qh].basket; lo=x[x[f]<=ql].basket
            return dirn*(hi.mean()-lo.mean()) if len(hi) and len(lo) else np.nan
        print(f"  {f:22s} trainHL {hl(tr):+.3f}  oosHL {hl(oo):+.3f}  IC {dirn*d[f].corr(d.basket,method='spearman'):+.3f}")


if __name__ == "__main__":
    m = market_forward()
    construct_validity(m)
    regime_conditional()
    lagged()

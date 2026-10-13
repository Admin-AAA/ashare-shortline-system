"""外科手术式分数线审计：只找在【训练段 & OOS & 逐月同号≥80%】三口径下都一致的格子。

避免 Pstar 那种单窗口过拟合（训练最优、OOS 退化）。对每个 regime×情绪格子、每条候选线 L，
统计 final_score>=L 的票：训练/OOS 均净、逐月为正比例、样本；并与现行有效线对比，输出：
- TIGHTEN 候选：现行线会放行、但该分域在两窗口都为负且逐月多数为负（稳健毒区，应收紧）；
- RELAX 候选：现行线之上无票/票少、而 L..现行线 之间的边际票在两窗口都为正且逐月≥80%。
"""
from __future__ import annotations
import json, sys, pickle
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
OUT = Path(__file__).resolve().parents[1] / "output" / "research"
TR = pd.read_pickle(OUT / "regime_scoreline_trades.pkl")
DAYCTX = pickle.loads((OUT / "regime_scoreline_dayctx.pkl").read_bytes())
CURRENT_LINE = {}
seen = {}
for d, c in DAYCTX.items():
    seen[f"{c['market_base']}·{c['sentiment']}"] = c["eff_line_current"]
CURRENT_LINE = seen
LINES = [60, 64, 68, 72, 76, 80, 85]
dates = sorted(TR.signal_date.unique()); SPLIT = dates[int(len(dates)*0.6)-1]


def month_pos_frac(x):
    m = x.groupby(x.signal_date.str[:7]).net.mean()
    return float((m > 0).mean()), int(len(m))


def audit():
    rows = []
    for key, sub in TR.groupby(TR.market_base + "·" + TR.sentiment):
        cur = CURRENT_LINE.get(key, 72.0)
        for L in LINES:
            above = sub[sub.final_score >= L]
            if len(above) < 12:
                continue
            trx = above[above.signal_date <= SPLIT]; oos = above[above.signal_date > SPLIT]
            mpf, nmon = month_pos_frac(above)
            rows.append({"key": key, "line": L, "current": cur, "n": len(above),
                         "tr_mean": round(trx.net.mean(), 3) if len(trx) else None,
                         "oos_mean": round(oos.net.mean(), 3) if len(oos) else None,
                         "all_mean": round(above.net.mean(), 3),
                         "month_pos_frac": round(mpf, 2), "months": nmon,
                         "wr": round((above.net > 0).mean(), 3)})
    df = pd.DataFrame(rows)
    print(f"split={SPLIT}  现行线:", {k: v for k, v in sorted(CURRENT_LINE.items())})
    print("\n===== 每格 × 候选线（>=L 的票）  tr/oos/all 均净 | 逐月正比例 =====")
    for key in sorted(df.key.unique()):
        d = df[df.key == key]
        print(f"\n {key}  (现行线 {CURRENT_LINE.get(key)})")
        for _, r in d.iterrows():
            flag = " <==现行" if abs(r.line - CURRENT_LINE.get(key, 72)) < 0.01 else ""
            print(f"   >={r.line:>2}: n{r.n:>4} tr {str(r.tr_mean):>7} oos {str(r.oos_mean):>7} all {r.all_mean:+6.3f} 月正{r.month_pos_frac:.2f}({r.months}m) WR{r.wr:.2f}{flag}")

    # 自动找稳健信号
    print("\n===== 稳健信号（两窗口同向 + 逐月同号>=0.75）=====")
    for _, r in df.iterrows():
        if r.tr_mean is None or r.oos_mean is None:
            continue
        both_neg = r.tr_mean < 0 and r.oos_mean < 0 and r.month_pos_frac <= 0.30
        both_pos = r.tr_mean > 0 and r.oos_mean > 0 and r.month_pos_frac >= 0.75
        if both_neg:
            print(f"  [稳健为负] {r.key} >={r.line} n{r.n} tr{r.tr_mean} oos{r.oos_mean} 月正{r.month_pos_frac}")
        if both_pos:
            print(f"  [稳健为正] {r.key} >={r.line} n{r.n} tr{r.tr_mean} oos{r.oos_mean} 月正{r.month_pos_frac}")
    df.to_csv(OUT / "regime_scoreline_audit.csv", index=False)
    return df


if __name__ == "__main__":
    audit()

"""出场候选按「最近 12 个月」口径复判。
评估窗 2025-10-01 ~ 2026-10-09；参数只在评估窗之前（2025-01-15 ~ 2025-09-22，留 6 天禁区）选择。
另报 round1 候选（持有 7 天、trailing 激活 2%）在评估窗的表现，以及评估窗内逐季度结果。"""
import sys, json, itertools, dataclasses as dc
import numpy as np
sys.path.insert(0, "scripts/walkforward")
from exitsim import sim, days, EP
from harness import metrics, fmt, decide

def variant(sl, tp, hz, act, lock, gap):
    return dc.replace(EP, horizon_days=hz,
        stop_loss=dc.replace(EP.stop_loss, default_loss_pct=sl, maximum_loss_pct=sl + 0.2),
        take_profit=dc.replace(EP.take_profit, enabled=tp is not None, default_gain_pct=tp or EP.take_profit.default_gain_pct),
        trailing=dc.replace(EP.trailing, activation_gain_pct=act, minimum_lock_gain_pct=lock),
        gap_risk=dc.replace(EP.gap_risk, max_entry_gap_up_pct=gap))

dates = sorted(days)
EVAL0, EVAL1 = "2025-10-01", "2026-10-10"
pre = [d for d in dates if d < EVAL0][:-6]
ev = [d for d in dates if EVAL0 <= d < EVAL1]
SEL = [(dt, c) for dt in dates for c in days[dt]["selected"]]
def m(k, ds):
    s = set(ds); ep = variant(*k)
    return metrics([(dt, sim(dt, c, ep)) for dt, c in SEL if dt in s])
BASE = (5.5, None, 5, 0.8, 2.5, 6.0)
CAND = (5.5, None, 7, 2.0, 2.5, 6.0)
GRID = list(itertools.product((4.0, 5.5, 7.0), (None, 5.0, 8.0, 12.0), (3, 5, 7), (0.8, 2.0, 4.0), (1.5, 2.5), (4.0, 6.0)))
bp = m(BASE, pre); best, bm = BASE, bp
for k in GRID:
    if k == BASE: continue
    x = m(k, pre)
    if x and x["n"] >= 0.95 * bp["n"] and x["wr"] >= bp["wr"] and x["avg"] > bm["avg"]:
        best, bm = k, x
out = {}
for name, k in (("基线 v5.0.0", BASE), ("评估窗前选中", best), ("round1 候选", CAND)):
    e = m(k, ev)
    out[name] = {"params": k, "pre": m(k, pre), "eval": e}
    q = []
    for lo, hi in (("2025-10-01","2026-01-01"),("2026-01-01","2026-04-01"),("2026-04-01","2026-07-01"),("2026-07-01","2026-10-10")):
        q.append(m(k, [d for d in ev if lo <= d < hi]))
    out[name]["quarters"] = q
    print(name, k, "| 前段", fmt(out[name]["pre"]), "| 近12月", fmt(e))
    print("   季度:", [round(x["avg"], 2) if x else None for x in q], [round(x["wr"]*100, 1) if x else None for x in q])
for name in ("评估窗前选中", "round1 候选"):
    print(name, "vs 基线 近12月:", decide(out["基线 v5.0.0"]["eval"], out[name]["eval"]).decision)
json.dump(out, open("output/research/holdout/exit_last12m.json", "w"), ensure_ascii=False, indent=1, default=str)

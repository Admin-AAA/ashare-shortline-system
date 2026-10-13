import json, math, sys, itertools, dataclasses as dc
from collections import defaultdict
import numpy as np
sys.path.insert(0, "scripts/walkforward")
from exitsim import sim, days, EP
from harness import metrics, decide
dates = sorted(days)
FOLDS = [("2025Q1",  "2025-01-01","2025-04-01"),("2025Q2","2025-04-01","2025-07-01"),("2025Q3","2025-07-01","2025-10-01"),("2025Q4","2025-10-01","2026-01-01"),
         ("2026Q1","2026-01-01","2026-04-01"),("2026Q2","2026-04-01","2026-07-01"),("2026Q3*","2026-07-01","2026-10-10")]
def variant(sl, tp, hz, act, lock, gap):
    ep = dc.replace(EP, horizon_days=hz,
        stop_loss=dc.replace(EP.stop_loss, default_loss_pct=sl, maximum_loss_pct=max(sl + 0.2, EP.stop_loss.maximum_loss_pct) if sl >= EP.stop_loss.default_loss_pct else sl + 0.2),
        take_profit=dc.replace(EP.take_profit, enabled=tp is not None, default_gain_pct=tp or EP.take_profit.default_gain_pct),
        trailing=dc.replace(EP.trailing, activation_gain_pct=act, minimum_lock_gain_pct=lock),
        gap_risk=dc.replace(EP.gap_risk, max_entry_gap_up_pct=gap))
    return ep
GRID = list(itertools.product((4.0, 5.5, 7.0), (None, 5.0, 8.0, 12.0), (3, 5, 7), (0.8, 2.0, 4.0), (1.5, 2.5), (4.0, 6.0)))
BASEK = (5.5, None, 5, 0.8, 2.5, 6.0)
SEL = [(dt, c) for dt in dates for c in days[dt]["selected"]]
res = {}
for i, k in enumerate(GRID):
    ep = variant(*k)
    try:
        res[k] = {(dt, c): sim(dt, c, ep) for dt, c in SEL}
    except Exception as e:
        res[k] = None
    if i % 40 == 0: print("grid", i, len(GRID), flush=True)
base = {(dt, c): days[dt]["net_by_code"].get(c) for dt, c in SEL}
assert all((res[BASEK][x] is None and base[x] is None) or abs((res[BASEK][x] or 0) - (base[x] or 0)) < 1e-6 for x in SEL)
def m_for(k, ds):
    s = set(ds); r = res[k]
    return metrics([(dt, r[(dt, c)]) for dt, c in SEL if dt in s])
out = {"folds": [], "consistency": {}}
for name, lo, hi in FOLDS[2:]:
    tr = [d for d in dates if d < lo][:-6]; te = [d for d in dates if lo <= d < hi]
    bm = m_for(BASEK, tr); best, bmm = BASEK, bm
    for k in GRID:
        if res[k] is None or k == BASEK: continue
        m = m_for(k, tr)
        if m and m["n"] >= 0.95 * bm["n"] and m["wr"] >= bm["wr"] and m["avg"] > bmm["avg"]:
            best, bmm = k, m
    tb, tn = m_for(BASEK, te), m_for(best, te)
    rng = np.random.default_rng(7)
    s = set(te)
    diffs = np.array([(res[best][(dt, c)] or 0) - (res[BASEK][(dt, c)] or 0) for dt, c in SEL if dt in s])
    ci = [float(np.percentile([diffs[rng.integers(0, len(diffs), len(diffs))].mean() for _ in range(2000)], q)) for q in (2.5, 97.5)] if len(diffs) else [0, 0]
    dec = str(decide(tb, tn).decision) if best != BASEK else "BASE"
    out["folds"].append({"fold": name, "chosen": best, "train_base": bm, "train_new": bmm, "test_base": tb, "test_new": tn, "avg_diff_ci": ci, "decision": dec})
    f = lambda m: f"n={m['n']} wr={m['wr']*100:.1f} avg={m['avg']:+.2f} sum={m['sum']:+.1f} mdd={m['mdd']:+.1f}"
    print(name, best, "| base", f(tb), "| new", f(tn), "| Δavg CI", [round(x, 2) for x in ci], dec, flush=True)
for k in GRID:
    if res[k] is None or k == BASEK: continue
    per = []
    for name, lo, hi in FOLDS:
        te = [d for d in dates if lo <= d < hi]
        tb, tn = m_for(BASEK, te), m_for(k, te)
        per.append((round(tn["wr"] - tb["wr"], 4), round(tn["avg"] - tb["avg"], 3), tn["n"] - tb["n"]))
    good = sum(1 for p in per if p[0] > 0 and p[1] > 0 and p[2] >= -2)
    out["consistency"][str(k)] = {"per_fold": per, "good": good, "all": m_for(k, dates)}
top = sorted(out["consistency"].items(), key=lambda kv: (-kv[1]["good"], -kv[1]["all"]["avg"]))[:10]
for k, v in top: print("CONS", k, v["good"], "/7", v["per_fold"], "all avg", round(v["all"]["avg"], 3), "wr", round(v["all"]["wr"]*100, 1), "n", v["all"]["n"])
print("BASE all", m_for(BASEK, dates))
json.dump(out, open("output/research/holdout/exit_wf.json", "w"), ensure_ascii=False, indent=1, default=str)

"""§352 评估。只读 output/research/s352/trades.json。"""
from __future__ import annotations
import json, sys
from pathlib import Path
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B))
sys.path.insert(0, str(B / "scripts/midterm"))
from s310_short_combo_compare import metrics
import numpy as np

T = json.loads((B / "output/research/s352/trades.json").read_text())
WIN = {"R12": ["12m"], "N3": ["12m", "EXT"], "EXT": ["EXT"], "W0": ["W0"], "W00": ["W00"]}

def hd(t):
    a, b = t["en"], t["ex"]
    return max(1, int(np.busday_count(f"{a[:4]}-{a[4:6]}-{a[6:]}", f"{b[:4]}-{b[4:6]}-{b[6:]}")))

base = {w: metrics([t for p in WIN[w] for t in T["BASE"][p]]) for w in ("R12", "N3")}
out = {}
for v in T:
    r = {}
    for w in WIN:
        ts = [t for p in WIN[w] for t in T[v][p]]
        if not ts:
            r[w] = None
            continue
        m = metrics(ts)
        m["hold"] = round(float(np.mean([hd(t) for t in ts])), 2)
        m["per_day"] = round(m["avg"] / m["hold"], 2) if m["hold"] else None
        r[w] = m
    R, N = r["R12"], r["N3"]
    c = {}
    if R and N:
        c["1_avg_pos"] = R["avg"] > 0 and N["avg"] > 0
        c["2_pf"] = R["pf"] >= 1.3 and N["pf"] >= 1.3
        c["3_mdd"] = R["mdd"] >= -35 and N["mdd"] >= -35
        pos = sum(1 for w in ("EXT", "R12", "W0", "W00") if r[w] and r[w]["avg"] > 0)
        c["4_phases"] = pos >= 3 and all(r[w] and r[w]["avg"] >= -5 for w in ("W0", "W00"))
        c["5_n"] = R["n"] >= 30 and N["n"] >= 30
        c["6_robust"] = all((x.get("trim_both5") if x.get("trim_both5") is not None else -1) > 0 and x["median"] > 0 for x in (R, N))
        if v == "OS_PCT5":
            c["7_vs_base"] = R["avg"] >= base["R12"]["avg"] and N["avg"] >= base["N3"]["avg"]
        elif v == "BASE":
            c["7_vs_base"] = True
        else:
            c["7_vs_base"] = R["avg"] >= base["R12"]["avg"] + 0.3 and N["avg"] >= base["N3"]["avg"] + 0.3
        r["pos_windows"] = pos
    else:
        c["5_n"] = False
    r["criteria"] = c
    r["pass"] = bool(c) and all(c.values())
    out[v] = r
    print(v, "PASS" if r["pass"] else "FAIL", "".join("✓" if x else "✗" for x in c.values()), c)
    for w in ("R12", "N3", "EXT", "W0", "W00"):
        if r[w]:
            print("   ", w, {k: r[w].get(k) for k in ("n", "avg", "pf", "mdd", "median", "trim_both5", "top10_share", "hold")})
(B / "output/research/s352/eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
adopt = [v for v in out if v != "BASE" and out[v].get("pass")]
print("ADOPT", adopt)

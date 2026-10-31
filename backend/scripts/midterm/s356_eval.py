"""§356 评估：A=§282 短线五条；B=R12/N3 每笔 ≥ BASE+0.3pp 且 N3 回撤不更深。"""
from __future__ import annotations
import json, sys
from pathlib import Path
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
from s310_short_combo_compare import metrics
T = json.loads((B / "output/research/s356/trades.json").read_text())
WIN = {"R12": ["12m"], "N3": ["12m", "EXT"], "EXT": ["EXT"], "W0": ["W0"], "W00": ["W00"]}
M = {v: {w: (metrics([t for p in WIN[w] for t in T[v].get(p, [])]) if any(T[v].get(p) for p in WIN[w]) else None) for w in WIN} for v in T}
out = {}
for v, r in M.items():
    R, N = r["R12"], r["N3"]
    pos = sum(1 for w in ("EXT", "R12", "W0", "W00") if r[w] and r[w]["avg"] > 0)
    A = bool(R and N and R["avg"] > 0 and N["avg"] > 0 and R["pf"] >= 1.3 and N["pf"] >= 1.3 and R["mdd"] >= -35 and N["mdd"] >= -35
             and pos >= 3 and all(r[w] and r[w]["avg"] >= -5 for w in ("W0", "W00")) and R["n"] >= 30 and N["n"] >= 30)
    b = M["BASE"]
    Bc = v != "BASE" and bool(R and N and b["R12"] and b["N3"]
                              and R["avg"] >= b["R12"]["avg"] + 0.3 and N["avg"] >= b["N3"]["avg"] + 0.3
                              and N["mdd"] >= b["N3"]["mdd"])
    out[v] = {"A": A, "B": Bc, "pass": (A and Bc) if v != "BASE" else None, **{w: r[w] for w in WIN}}
    print(v, "A", A, "B", Bc, "PASS" if out[v]["pass"] else ("BASE" if v == "BASE" else "REJECT"))
    for w in WIN:
        if r[w]: print("   ", w, {k: r[w].get(k) for k in ("n", "avg", "pf", "mdd", "median", "trim_both5")})
(B / "output/research/s356/eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
print("written eval.json")

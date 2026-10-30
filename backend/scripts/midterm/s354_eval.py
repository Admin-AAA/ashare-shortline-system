"""§354 评估：A=§282 短线五条；B=R12/N3 每笔 ≥ BASE 且 N3 回撤不更深。"""
from __future__ import annotations
import json, sys
from pathlib import Path
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
from s310_short_combo_compare import metrics
T = json.loads((B / "output/research/s354/trades.json").read_text())
WIN = {"R12": ["12m"], "N3": ["12m", "EXT"], "EXT": ["EXT"], "W0": ["W0"], "W00": ["W00"]}
out = {}
for sid, vs in T.items():
    M = {v: {w: (metrics([t for p in WIN[w] for t in vs[v].get(p, [])]) if any(vs[v].get(p) for p in WIN[w]) else None) for w in WIN} for v in vs}
    for v, r in M.items():
        R, N = r["R12"], r["N3"]
        pos = sum(1 for w in ("EXT", "R12", "W0", "W00") if r[w] and r[w]["avg"] > 0)
        A = bool(R and N and R["avg"] > 0 and N["avg"] > 0 and R["pf"] >= 1.3 and N["pf"] >= 1.3 and R["mdd"] >= -35 and N["mdd"] >= -35
                 and pos >= 3 and all(r[w] and r[w]["avg"] >= -5 for w in ("W0", "W00")) and R["n"] >= 30 and N["n"] >= 30)
        b = M["BASE"]
        Bc = v != "BASE" and bool(R and N and R["avg"] >= b["R12"]["avg"] and N["avg"] >= b["N3"]["avg"] and N["mdd"] >= b["N3"]["mdd"])
        out.setdefault(sid, {})[v] = {"A": A, "B": Bc, "pass": A and Bc, **{w: r[w] for w in WIN}}
        print(sid, v, "A", A, "B", Bc, "PASS" if (A and Bc) else "")
        for w in WIN:
            if r[w]: print("   ", w, {k: r[w].get(k) for k in ("n", "avg", "pf", "mdd", "median", "trim_both5")})
(B / "output/research/s354/eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))

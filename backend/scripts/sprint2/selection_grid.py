"""Sprint2: 单策略 每日上限(1-3) × 分数线 网格；W1+W2 选、W3 留出检验。净收益取面板现行离场 net。"""
from __future__ import annotations
import json, os, sys
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from app.services.standalone_strategies import standalone_selection_policy
SIDS = ["chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "high-range-breakout-momentum", "oversold-capital-reversal", "sector-relative-strength-leader"]
SPLIT = "2026-05-01"

def st(v):
    if not v: return {"n": 0, "avg": None, "wr": None}
    return {"n": len(v), "avg": round(sum(v) / len(v), 2), "wr": round(100 * sum(x > 0 for x in v) / len(v), 1)}

def main():
    base = load_production_selection_policy(); mod.OUTPUT_COUNT, mod.SCORE_LINE = 3, 72.0
    out = {}
    for sid in SIDS:
        panels = [json.loads((B / f"output/research/{r}/{sid}/panel/entry_panel_WX.json").read_text()) for r in ("sprint2/solo_ext", "solo")]
        res = {}
        for k in (1, 2, 3):
            pol = standalone_selection_policy(base, sid, k)
            for line in (60, 64, 68, 72, 76, 80):
                w = {"W1": [], "W2": [], "W3": []}
                for i, p in enumerate(panels):
                    _register_only(p)
                    _, rows = run_rule(RULES[f"only:{sid}"], p, pol, {"score_line": line, "output_count": k}, apply_global_weights=False)
                    for r in rows:
                        w["W1" if i == 0 else ("W2" if r["dt"] < SPLIT else "W3")].append(r["net"])
                res[(k, line)] = {x: st(v) for x, v in w.items()} | {"W12": st(w["W1"] + w["W2"])}
        b = res[(3, 72)]
        print(sid, "baseline", b)
        for key, v in sorted(res.items()):
            print("  ", key, " ".join(f"{x}:{v[x]['n']}/{v[x]['avg']}" for x in ("W1", "W2", "W3", "W12")))
        out[sid] = {f"{k}_{l}": v for (k, l), v in res.items()}
    (B / "output/research/sprint2/selection_grid.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()

"""Sprint2：单策略「某些市况日停出」评估（不改生产）。
用面板现行离场净收益（run_rule 的 net，与 compute_standalone_metrics 同口径），
比较全部 vs 剔除指定市况日的 n/avg/WR/PF，按窗口 W0/W1/W2/W3 分列。
用法：python scripts/sprint2/regime_skip.py --sid high-range-breakout-momentum --skip 震荡,震荡·活跃
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(B))
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy

PANELS = [("W0", "output/research/sprint2/solo_w0"), ("W1", "output/research/sprint2/solo_ext"), ("W23", "output/research/solo")]
SPLIT = "2026-05-01"

def stats(v):
    if not v: return {"n": 0}
    g = sum(x for x in v if x > 0); l = -sum(x for x in v if x < 0)
    return {"n": len(v), "avg": round(sum(v) / len(v), 3), "wr": round(100 * sum(x > 0 for x in v) / len(v), 1), "pf": round(g / l, 3) if l else None, "sum": round(sum(v), 2)}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sid", default="high-range-breakout-momentum")
    ap.add_argument("--skip", action="append", default=None, help="逗号分隔市况集合，可多次给出（多个变体）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    variants = a.skip or ["震荡,震荡·活跃"]
    base_pol = load_production_selection_policy(); mod.OUTPUT_COUNT, mod.SCORE_LINE = 3, 72.0
    rows_by_win = {}
    for wname, root in PANELS:
        f = B / root / a.sid / "panel" / "entry_panel_WX.json"
        if not f.exists(): print(wname, "no panel", f); continue
        p = json.loads(f.read_text()); _register_only(p)
        reg = {(d["signal_date"], str(c["code"])): (c.get("market_regime") or "?") for d in p["days"] for c in d["cards"]}
        _, rows = run_rule(RULES[f"only:{a.sid}"], p, standalone_selection_policy(base_pol, a.sid, 3), apply_global_weights=False)
        for r in rows:
            if r.get("net") is None: continue
            w = wname if wname != "W23" else ("W2" if r["dt"] < SPLIT else "W3")
            rows_by_win.setdefault(w, []).append((r["dt"], reg[(r["dt"], str(r["code"]))], float(r["net"])))
    res = {"sid": a.sid, "windows": {}}
    groups = {**{w: rows_by_win.get(w, []) for w in ("W0", "W1", "W2", "W3")},
              "W1+W2": rows_by_win.get("W1", []) + rows_by_win.get("W2", []),
              "W0+W1+W2": rows_by_win.get("W0", []) + rows_by_win.get("W1", []) + rows_by_win.get("W2", []),
              "recent12m(W2+W3)": rows_by_win.get("W2", []) + rows_by_win.get("W3", []),
              "ALL": sum(rows_by_win.values(), [])}
    for g, rs in groups.items():
        cell = {"base": stats([x[2] for x in rs])}
        for v in variants:
            skip = set(v.split(","))
            cell[f"skip[{v}]"] = stats([x[2] for x in rs if x[1] not in skip])
            cell[f"only[{v}]"] = stats([x[2] for x in rs if x[1] in skip])
        res["windows"][g] = cell
        print(g); [print(f"   {k:32s} {s}") for k, s in cell.items()]
    out = B / (a.out or f"output/research/sprint2/regime_skip_{a.sid}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1)); print("->", out)

if __name__ == "__main__":
    main()

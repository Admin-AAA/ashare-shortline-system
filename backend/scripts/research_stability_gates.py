"""Sprint3 研究：单策略面板上试日级市场闸门/前N/分数线/近期表现闸门，看 12m 与留出窗口的 avg/PF/MDD。
用法：.venv/bin/python scripts/research_stability_gates.py --sids a,b --out output/research/sprint3/gates.json
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only

R = B / "output" / "research"
def panels(sid):
    c = {"12m": R / "solo" / sid, "EXT": R / "sprint2" / "solo_ext" / sid,
         "W0": R / "sprint2" / "solo_w0" / sid, "W00": R / "sprint2" / "solo_w00" / sid}
    for w in ("W00", "W0"):
        h = R / "sprint2" / "tdx_fill" / w.lower() / sid
        if (h / "replay.exit").exists() and (h / "replay.exit").read_text().strip() == "0":
            c[w] = h
    out = {}
    for w, d in c.items():
        f = d / "panel" / "entry_panel_WX.json"
        if f.exists() and (d / "replay.exit").exists() and (d / "replay.exit").read_text().strip() == "0":
            out[w] = f
    return out

def ur(ms):
    b = ms.get("breadth") or {}
    r = b.get("up_ratio")
    if r is None:
        u, d = float(ms.get("up_count") or 0), float(ms.get("down_count") or 0)
        r = u / (u + d) if u + d else 0.5
    return float(r)

def gate(pred):
    def g(cards, day):
        return [] if pred(day["market_snapshot"]) else cards
    return g

VARIANTS = {
    "base": None,
    "skip_ur35_50": lambda ms: 0.35 <= ur(ms) < 0.50,
    "skip_ur_lt35": lambda ms: ur(ms) < 0.35,
    "skip_ur_lt50": lambda ms: ur(ms) < 0.50,
    "skip_ur_lt40": lambda ms: ur(ms) < 0.40,
    "skip_ur_gt75": lambda ms: ur(ms) > 0.75,
    "skip_ld_ge20": lambda ms: float(ms.get("limit_down_count") or 0) >= 20,
    "skip_ld_ge10": lambda ms: float(ms.get("limit_down_count") or 0) >= 10,
    "skip_lu_lt40": lambda ms: float(ms.get("limit_up_count") or 0) < 40,
    "skip_lu_lt60": lambda ms: float(ms.get("limit_up_count") or 0) < 60,
    "skip_ludiff_lt30": lambda ms: float(ms.get("limit_up_count") or 0) - float(ms.get("limit_down_count") or 0) < 30,
    "skip_weak_regime": lambda ms: "弱" in str((ms.get("breadth") or {}).get("regime") or ""),
    "skip_choppy_regime": lambda ms: "震荡" in str((ms.get("breadth") or {}).get("regime") or ""),
}
LU = lambda ms: float(ms.get("limit_up_count") or 0)
LD = lambda ms: float(ms.get("limit_down_count") or 0)
VARIANTS2 = {
    "base": None,
    "skip_ur35_50": lambda ms: 0.35 <= ur(ms) < 0.50,
    "skip_ur33_50": lambda ms: 0.33 <= ur(ms) < 0.50,
    "skip_ur38_50": lambda ms: 0.38 <= ur(ms) < 0.50,
    "skip_ur35_55": lambda ms: 0.35 <= ur(ms) < 0.55,
    "skip_ur35_45": lambda ms: 0.35 <= ur(ms) < 0.45,
    "skip_ludiff_lt20": lambda ms: LU(ms) - LD(ms) < 20,
    "skip_ludiff_lt25": lambda ms: LU(ms) - LD(ms) < 25,
    "skip_ludiff_lt30": lambda ms: LU(ms) - LD(ms) < 30,
    "skip_ludiff_lt35": lambda ms: LU(ms) - LD(ms) < 35,
    "skip_ludiff_lt40": lambda ms: LU(ms) - LD(ms) < 40,
    "skip_lu_lt30": lambda ms: LU(ms) < 30,
    "skip_ur35_50|gt75": lambda ms: 0.35 <= ur(ms) < 0.50 or ur(ms) > 0.75,
    "skip_ur35_50|ludiff30": lambda ms: 0.35 <= ur(ms) < 0.50 or LU(ms) - LD(ms) < 30,
    "skip_ur35_50|ld20": lambda ms: 0.35 <= ur(ms) < 0.50 or LD(ms) >= 20,
    "skip_choppy_regime": lambda ms: "震荡" in str((ms.get("breadth") or {}).get("regime") or ""),
}
CTX = {"top2": {"output_count": 2}, "top1": {"output_count": 1}, "line75": {"score_line": 75.0}, "line78": {"score_line": 78.0}}

def summarize(r):
    if r is None: return {"n": 0}
    return {"n": r["n"], "wr": round(r["wr"] * 100, 1), "avg": round(r["avg"], 2), "pf": round(r["pf"], 2), "mdd": round(r["mdd"], 1), "sum": round(r["sum"], 1)}

def perf_gate(rows, days, lookback=10, lag=6, thr=0.0):
    """近期表现闸门：用 lag 个交易日以前已结算的最近 lookback 笔均值 < thr 就停发。"""
    idx = {d: i for i, d in enumerate(days)}
    kept = []
    hist = sorted(rows, key=lambda x: x["dt"])
    for r in hist:
        i = idx[r["dt"]]
        settled = [x["net"] for x in hist if idx[x["dt"]] <= i - lag][-lookback:]
        if len(settled) >= lookback and sum(settled) / len(settled) < thr:
            continue
        kept.append(r)
    return kept

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sids", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--set", default="1"); a = ap.parse_args()
    V = VARIANTS2 if a.set == "2" else VARIANTS
    base_pol = load_production_selection_policy()
    res = {}
    for sid in a.sids.split(","):
        pol = standalone_selection_policy(base_pol, sid, 3)
        res[sid] = {}
        for w, f in panels(sid).items():
            p = json.loads(f.read_text()); _register_only(p)
            days = [d["signal_date"] for d in p["days"]]
            base = RULES[f"only:{sid}"]
            out = {}
            for name, pred in V.items():
                rule = base if pred is None else (lambda c, d, _b=base, _g=gate(pred): _g(_b(c, d), d))
                r, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                out[name] = summarize(r)
                if name == "base":
                    for lb, thr in ((10, 0.0), (20, 0.0), (10, -1.0)):
                        out[f"perf_lb{lb}_t{thr}"] = summarize(_metrics(perf_gate(rows, days, lb, 6, thr), days))
            for name, ctx in (CTX.items() if a.set == "1" else []):
                c = {"output_count": 3, "score_line": 72.0}; c.update(ctx)
                r, _ = run_rule(base, p, pol, c, apply_global_weights=False)
                out[name] = summarize(r)
            res[sid][w] = out
            print(sid, w, json.dumps(out["base"]), flush=True)
            Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
if __name__ == "__main__":
    main()

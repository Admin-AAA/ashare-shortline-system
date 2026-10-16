"""Sprint3 §72：单策略面板上「线72+闸门后候选卡」各特征与因果净收益的 rank IC（逐窗）。"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from scipy.stats import spearmanr
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_entry_rule_panel import RULES
from scripts.research_stability_gates import panels, VARIANTS2

def fv(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    if v is None: v = c.get(k)
    try: return float(v)
    except (TypeError, ValueError): return None

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sid", required=True); ap.add_argument("--gate", default="base"); ap.add_argument("--min-score", type=float, default=72)
    a = ap.parse_args(); pred = VARIANTS2.get(a.gate); res = {}
    for w, f in panels(a.sid).items():
        p = json.loads(f.read_text()); _register_only(p); base = RULES[f"only:{a.sid}"]
        recs = []
        for d in p["days"]:
            if pred and pred(d["market_snapshot"]): continue
            for c in base([dict(x) for x in d["cards"]], d):
                net = d["net_by_code"].get(str(c.get("code")))
                if net is None or float(c.get("final_score") or 0) < a.min_score: continue
                recs.append((c, float(net)))
        keys = sorted({k for c, _ in recs for k, v in (c.get("feature_snapshot") or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)} | {"final_score"})
        out = {}
        for k in keys:
            xs = [(fv(c, k), n) for c, n in recs]; xs = [(x, n) for x, n in xs if x is not None]
            if len(xs) < 40 or len({x for x, _ in xs}) < 5: continue
            r = spearmanr([x for x, _ in xs], [n for _, n in xs]).statistic
            out[k] = (round(float(r), 3), len(xs))
        res[w] = {"n": len(recs), "ic": out}
    ws = list(res)
    allk = sorted(set().union(*[res[w]["ic"] for w in ws]), key=lambda k: -abs(res.get("12m", {}).get("ic", {}).get(k, (0,))[0]))
    print("n:", {w: res[w]["n"] for w in ws})
    for k in allk[:45]:
        print(f"{k:28s}", " ".join(f"{w}:{res[w]['ic'].get(k, ('-',))[0]:>6}" for w in ws))
    Path(f"output/research/sprint3/ic_{a.sid}.json").write_text(json.dumps(res, ensure_ascii=False))
if __name__ == "__main__":
    main()

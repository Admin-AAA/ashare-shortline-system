"""Sprint3 §72：单策略面板上选股侧变体（分数线、每日张数、卡级/日级过滤）对比，前3/线72 为 base。"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels, VARIANTS2

def fv(c, k, d=None):
    v = (c.get("feature_snapshot") or {}).get(k)
    if v is None: v = c.get(k)
    try: return float(v)
    except (TypeError, ValueError): return d
def ms(d, k): 
    try: return float(d["market_snapshot"].get(k) or 0)
    except (TypeError, ValueError): return 0.0
def card(pred): return lambda cs, d: [c for c in cs if pred(c)]
def day(pred): return lambda cs, d: [] if pred(d) else cs

V = {
 "base": (None, {}),
 "line75": (None, {"score_line": 75}), "line78": (None, {"score_line": 78}), "line80": (None, {"score_line": 80}),
 "top2": (None, {"output_count": 2}), "top1": (None, {"output_count": 1}),
 "chg20_lt40": (card(lambda c: (fv(c, "chg20", 0) or 0) < 40), {}),
 "chg20_lt30": (card(lambda c: (fv(c, "chg20", 0) or 0) < 30), {}),
 "chg60_lt60": (card(lambda c: (fv(c, "chg60", 0) or 0) < 60), {}),
 "overheat_lt50": (card(lambda c: (fv(c, "limit_up_overheat_score", 0) or 0) < 50), {}),
 "lud20_le3": (card(lambda c: (fv(c, "limit_up_days20", 0) or 0) <= 3), {}),
 "dist60_lt30": (card(lambda c: (fv(c, "dist60", 0) or 0) < 30), {}),
 "bias5_lt8": (card(lambda c: (fv(c, "bias_ma5", 0) or 0) < 8), {}),
 "bias5_lt5": (card(lambda c: (fv(c, "bias_ma5", 0) or 0) < 5), {}),
 "turn_lt20": (card(lambda c: (fv(c, "turnover_rate", 0) or 0) < 20), {}),
 "turn_gt5": (card(lambda c: (fv(c, "turnover_rate", 0) or 0) > 5), {}),
 "notlimitup": (card(lambda c: not (c.get("feature_snapshot") or {}).get("is_limit_up")), {}),
 "limitup": (card(lambda c: bool((c.get("feature_snapshot") or {}).get("is_limit_up"))), {}),
 "lu_lt80_day": (day(lambda d: ms(d, "limit_up_count") >= 80), {}),
 "ld_ge3_day": (day(lambda d: ms(d, "limit_down_count") < 3), {}),
 "weak_top1": ("weak", {}),
 "skip_ur_lt35": ("urlt35", {}), "skip_ur_lt30": ("urlt30", {}), "skip_ur_lt25": ("urlt25", {}),
 "weak_top1_score": ("weakscore", {}),
}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sid", required=True); ap.add_argument("--gate", default="base"); ap.add_argument("--only", default="")
    a = ap.parse_args(); pol = standalone_selection_policy(load_production_selection_policy(), a.sid, 3)
    g = VARIANTS2.get(a.gate); res = {}
    names = a.only.split(",") if a.only else list(V)
    for w, f in panels(a.sid).items():
        p = json.loads(f.read_text()); _register_only(p); base = RULES[f"only:{a.sid}"]; res[w] = {}
        for n in names:
            extra, ctx = V[n]
            def rule(cs, d, _e=extra):
                if g and g(d["market_snapshot"]): return []
                cs = base(cs, d)
                if isinstance(_e, str) and _e.startswith("urlt"):
                    from scripts.research_stability_gates import ur
                    return [] if ur(d["market_snapshot"]) < int(_e[4:]) / 100 else cs
                if _e == "weakscore":
                    from scripts.research_stability_gates import ur
                    if ur(d["market_snapshot"]) < 0.5:
                        cs = [c for c in cs if float(c.get("final_score") or 0) >= 85]
                    return cs
                if _e == "weak":
                    from scripts.research_stability_gates import ur
                    if ur(d["market_snapshot"]) < 0.5:
                        cs = sorted(cs, key=lambda c: -float(c.get("final_score") or 0))[:1]
                    return cs
                return _e(cs, d) if _e else cs
            m, _ = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0, **ctx}, apply_global_weights=False)
            res[w][n] = {"n": m["n"], "avg": round(m["avg"], 2), "pf": round(m["pf"], 2), "mdd": round(m["mdd"], 1)} if m else {"n": 0}
    for n in names:
        print(f"{n:14s}", " | ".join(f"{w}:{res[w][n].get('n'):>4} {res[w][n].get('avg','-'):>5} {res[w][n].get('pf','-'):>4} {res[w][n].get('mdd','-'):>6}" for w in res))
    Path(f"output/research/sprint3/sel_{a.sid}.json").write_text(json.dumps(res, ensure_ascii=False))
if __name__ == "__main__":
    main()

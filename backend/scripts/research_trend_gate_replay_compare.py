import os, sys, json
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
sys.path.insert(0, ".")
from app.engine.production_policy import load_production_selection_policy
import scripts.research_entry_rule_panel as mod
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from app.services.standalone_strategies import standalone_selection_policy
base_pol = load_production_selection_policy()
mod.OUTPUT_COUNT, mod.SCORE_LINE = 3, 72.0
res = {}
for sid in ["chao-gu-yang-jia-skill", "sector-relative-strength-leader"]:
    for tag, path in [("gate", f"output/research/sprint4/r12trend/panel/{sid}/entry_panel_WX.json"),
                      ("base", f"output/research/sprint4/r12trend_base/panel/{sid}/entry_panel_WX.json"),
                      ("live", f"output/research/solo/{sid}/panel/entry_panel_WX.json")]:
        p = json.load(open(path)); _register_only(p)
        pol = standalone_selection_policy(base_pol, sid, 3)
        days = [d["signal_date"] for d in p["days"]]
        _, rows = run_rule(RULES[f"only:{sid}"], p, pol, apply_global_weights=False)
        out = {}
        for nm, lo, hi in [("12m", "0", "9"), ("recent", "2026-05-01", "9"), ("H1", "0", "2026-04-01"), ("H2", "2026-04-01", "9")]:
            r = _metrics([x for x in rows if lo <= x["dt"] < hi], [x for x in days if lo <= x < hi])
            out[nm] = None if r is None else dict(n=int(r["n"]), wr=round(r["wr"]*100,1), avg=round(r["avg"],2), pf=None if r["pf"] is None else round(r["pf"],2), mdd=round(r["mdd"],1))
        res[f"{sid[:8]}_{tag}"] = out
        print(sid[:8], tag, days[0], days[-1], out, flush=True)
json.dump(res, open("output/research/sprint4/r12trend_cmp.json", "w"), ensure_ascii=False, indent=1)

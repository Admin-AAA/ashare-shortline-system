"""现役策略轻量过滤调参：25Q4~26Q1 选参（须同时提高均值且胜率不降、票数≥基线70%），26Q2~Q3 检验。
过滤项只用股票级可部署字段（可写进 strategy.yaml prefilter）。
口径与 research_round2_single_skill_eval 一致（only:<sid> + v27 + 6 张 + 72 分）。"""
import itertools, json, os, sys
from pathlib import Path
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy
import scripts.research_round2_single_skill_eval as ev

NEG = sys.argv[1].split(",")
panel = json.loads(Path(B, "output/research/round3/panel/entry_panel_WX.json").read_text())
pol = load_production_selection_policy()
SEL0, SEL1 = "2025-10-01", "2026-04-01"
TEST0, TEST1 = "2026-04-01", "2026-10-10"

def f(c, k):
    v = c.get(k)
    if v is None:
        v = (c.get("feature_snapshot") or {}).get(k)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def has(c, sid):
    return any(str(s.get("strategy_id")) == sid and not s.get("hard_veto")
               for s in (c.get("strategy_signals") or []) if isinstance(s, dict))

FILTERS = {
    "none": lambda c: True,
    "turn<=8": lambda c: (f(c, "turnover_rate") or 0) <= 8,
    "turn<=5": lambda c: (f(c, "turnover_rate") or 0) <= 5,
    "pct<=5": lambda c: (f(c, "pct") or 0) <= 5,
    "pct<=3": lambda c: (f(c, "pct") or 0) <= 3,
    "chg20<=15": lambda c: (f(c, "chg20") or 0) <= 15,
    "chg20>=0": lambda c: (f(c, "chg20") or 0) >= 0,
    "ma5>ma20": lambda c: (f(c, "ma5") or 0) > (f(c, "ma20") or 0),
    "rsi6<=70": lambda c: (f(c, "rsi_6") or 50) <= 70,
    "main_inflow>0": lambda c: (f(c, "main_net_inflow_yi") or 0) > 0,
    "turn<=12": lambda c: (f(c, "turnover_rate") or 0) <= 12,
    "pct<=7": lambda c: (f(c, "pct") or 0) <= 7,
    "chg20<=25": lambda c: (f(c, "chg20") or 0) <= 25,
    "rsi6<=80": lambda c: (f(c, "rsi_6") or 50) <= 80,
}
COMBOS = [(a,) for a in FILTERS] + [tuple(x) for x in itertools.combinations([k for k in FILTERS if k != "none"], 2)]

def rule_for(sid, combo):
    return lambda cards, _d: [c for c in cards if has(c, sid) and all(FILTERS[k](c) for k in combo)]

def sl(lo, hi):
    return {**panel, "days": [d for d in panel["days"] if lo <= d["signal_date"] < hi]}

P_SEL, P_TEST = sl(SEL0, SEL1), sl(TEST0, TEST1)
P_ALL = sl(SEL0, TEST1)
out = {}
for sid in NEG:
    base_sel, _ = ev._eval_rule(rule_for(sid, ("none",)), P_SEL, pol, 6, 72.0)
    best = None
    if base_sel:
        for combo in COMBOS[1:]:
            m, _ = ev._eval_rule(rule_for(sid, combo), P_SEL, pol, 6, 72.0)
            if not m or m["n"] < max(8, 0.7 * base_sel["n"]):
                continue
            if m["avg"] > base_sel["avg"] and m["wr"] >= base_sel["wr"] and (best is None or m["avg"] > best[1]["avg"]):
                best = (combo, m)
    pick = lambda m: None if not m else {k: round(m[k], 3) for k in ("n", "wr", "avg", "pf", "mdd")}
    bt, _ = ev._eval_rule(rule_for(sid, ("none",)), P_TEST, pol, 6, 72.0)
    res = {"base_sel": pick(base_sel), "base_test": pick(bt), "selected": None}
    if best:
        t, _ = ev._eval_rule(rule_for(sid, best[0]), P_TEST, pol, 6, 72.0)
        res.update(selected=list(best[0]), sel=pick(best[1]), test=pick(t))
    out[sid] = res
    print(sid, json.dumps(res, ensure_ascii=False), flush=True)
Path(B, "output/research/round3/tune_positive.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))

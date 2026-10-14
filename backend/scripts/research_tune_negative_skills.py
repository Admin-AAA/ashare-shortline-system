"""对单策略近12月平均为负的策略做轻量过滤调参。
选参只用 25Q4（2025-10-01~2025-12-31，面板最早一段）；在 26Q1~26Q3 上检验，并报全窗。
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
SEL0, SEL1 = "2025-10-01", "2026-01-01"
TEST0, TEST1 = "2026-01-01", "2026-10-10"

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
    "ivol<=0.7": lambda c: (c.get("ivol20_pct") is None) or float(c["ivol20_pct"]) <= 0.7,
    "regime!=弱势": lambda c: "弱势" not in str(c.get("market_regime") or ""),
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
    best = None
    for combo in COMBOS:
        m, _ = ev._eval_rule(rule_for(sid, combo), P_SEL, pol, 6, 72.0)
        if not m or m["n"] < 8:
            continue
        if best is None or m["avg"] > best[1]["avg"]:
            best = (combo, m)
    res = {"selected_on_25Q4": None}
    if best:
        combo = best[0]
        t, _ = ev._eval_rule(rule_for(sid, combo), P_TEST, pol, 6, 72.0)
        a, _ = ev._eval_rule(rule_for(sid, combo), P_ALL, pol, 6, 72.0)
        base, _ = ev._eval_rule(rule_for(sid, ("none",)), P_ALL, pol, 6, 72.0)
        pick = lambda m: None if not m else {k: round(m[k], 3) for k in ("n", "wr", "avg", "pf", "mdd")}
        res = {"selected_on_25Q4": list(combo), "sel": pick(best[1]), "test_26Q1_Q3": pick(t), "full_12m": pick(a), "base_full_12m": pick(base)}
    out[sid] = res
    print(sid, json.dumps(res, ensure_ascii=False), flush=True)
Path(B, "output/research/round3/tune_negative.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))

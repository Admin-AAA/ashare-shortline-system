"""Sprint4 §87：给尚无出场覆盖的线上短线策略（chen-xiaoqun、high-range-breakout-momentum）试用
已在其他策略验证过的出场覆盖，口径同 §86（base + 20 次随机平手，21 次选股平均）。
预注册变体：lock4_r03（chao-gu 同款）、act3_r03（oversold 同款）、lock4（sector 同款）、ma5（收盘破 MA5 离场）。
采纳标准：21 次平均每笔均值在 ≥3/4 窗高于默认，12m 不低于默认，且 4 窗平均提升 ≥0.15pp；多个达标取最差窗口提升最大者。
"""
from __future__ import annotations
import dataclasses as dc, json, statistics, sys
from collections import defaultdict
from pathlib import Path
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
import scripts.research_exit_override_robust as R
import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

VARS = {"def": ({}, 0), "lock4_r03": ({"minimum_lock_gain_pct": 4.0, "profit_lock_ratio": 0.3}, 0),
        "act3_r03": ({"activation_gain_pct": 3.0, "profit_lock_ratio": 0.3}, 0), "lock4": ({"minimum_lock_gain_pct": 4.0}, 0), "ma5": ({}, 5)}

def set_var(v):
    ov, ma = VARS[v]
    PE.apply_exit_overrides = (lambda pol, sid, _o=ov: dc.replace(pol, trailing=dc.replace(pol.trailing, **_o))) if ov else (lambda pol, sid: pol)
    PE.ma_break_window_for = lambda sid, _m=ma: _m

def main():
    sids = sys.argv[1] if len(sys.argv) > 1 else "chen-xiaoqun-skill,high-range-breakout-momentum"
    outp = Path("output/research/sprint4/exit_override_new.json"); res = {}
    base_pol = load_production_selection_policy(); ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(20)]
    with SessionLocal() as db:
        for sid in sids.split(","):
            pol = standalone_selection_policy(base_pol, sid, 3); res[sid] = {}
            for w, f in panels(sid).items():
                p = json.loads(f.read_text()); _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]; allrows = {}; set_var("def")
                for v in names:
                    def rule(cs, d, _v=v):
                        if LIVE_DAY[sid](d["market_snapshot"]): return []
                        cs = base(cs, d)
                        if sid in CARD: cs = [c for c in cs if CARD[sid](c)]
                        return tiebreak(cs, _v, d["signal_date"])
                    _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                    allrows[v] = rows
                need = defaultdict(set)
                for rows in allrows.values():
                    for r in rows: need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need); out = {}
                for m in VARS:
                    set_var(m); cache = {}; avgs = []; pfs = []; mdds = []; ns = []
                    for v, rows in allrows.items():
                        nrows = []
                        for r in rows:
                            k = (r["dt"], r["code"])
                            if k not in cache:
                                day = Day(); day.signal_date = r["dt"]; day.outcome_bars = {r["code"]: tuple(bars[r["dt"]].get(r["code"], ())[:lim])}
                                tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                                cache[k] = float(tr.net_return_pct) if tr and tr.executed else None
                            if cache[k] is not None: nrows.append({"dt": r["dt"], "code": r["code"], "net": cache[k]})
                        mm = _metrics(nrows, days)
                        if mm: avgs.append(mm["avg"]); pfs.append(mm["pf"]); mdds.append(mm["mdd"]); ns.append(mm["n"])
                    out[m] = {"mean_avg": round(statistics.mean(avgs), 3) if avgs else None, "mean_pf": round(statistics.mean(pfs), 2) if pfs else None,
                              "mean_mdd": round(statistics.mean(mdds), 1) if mdds else None, "mean_n": round(statistics.mean(ns), 1) if ns else 0}
                set_var("def"); res[sid][w] = out
                print(sid, w, out, flush=True); outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
if __name__ == "__main__":
    main()

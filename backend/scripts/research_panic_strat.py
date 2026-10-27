"""Sprint4 §123 P_strat：现行短线策略在「市场恐慌日」信号（T 收盘恐慌 → T+1 开盘买）vs 正常日的表现。
口径同 §121 V0（research_climax_line）：v27 + standalone_selection_policy、LIVE_DAY / CARD 闸门、base + 20 次随机平手（21 次平均），
_simulate_card 按现行 exit_overrides 真实成交重模拟。恐慌日历取自 research_panic_rebound.py 输出（_panic_calendar）。
预注册写于 /workspace/strategy-research.md §123（2026-10-19 10:56 CST）：唯一可落地动作 = 恐慌日暂停出票，需同时满足
a) 恐慌日卡 R12 与 N3' 每笔 < 0 且 PF < 1；b) 恐慌日卡 N3' 笔数 ≥ 30；c) 去掉后整体 R12 / N3' 总收益 ≥ 现行且 MDD 不变差；
d) W0 / W00 去掉后每笔不降超过 0.3。
"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.production_policy import (SelectionPolicyContext, evaluate_selection_policy,
                                          load_production_selection_policy)
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD
from scripts.research_cooldown_filter import _m, _mean

ORDER = ("EXT", "12m", "W0", "W00")


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chao-gu-yang-jia-skill,chen-xiaoqun-skill,oversold-capital-reversal,"
                                      "sector-relative-strength-leader")
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--cal", default="output/research/sprint4/panic_rebound_A.json,output/research/sprint4/panic_rebound_B.json")
    ap.add_argument("--out", default="output/research/sprint4/panic_strat.json")
    a = ap.parse_args()
    panic = set()
    for f in a.cal.split(","):
        panic |= {_d(k) for k in json.loads(Path(f).read_text()).get("_panic_calendar", {})}
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_panic_days_loaded": len(panic)}
    with SessionLocal() as db:
        for sid in a.sids.split(","):
            res[sid] = {}
            pol = standalone_selection_policy(base_pol, sid, 3)
            pw = panels(sid)
            keep = {}
            for w in ORDER:
                if w not in pw:
                    continue
                p = json.loads(pw[w].read_text())
                _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                npanic_days = sum(1 for d in days if _d(d) in panic)
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]
                sel = {}
                for v in names:
                    def rule(cs, d, _v=v):
                        if LIVE_DAY[sid](d["market_snapshot"]):
                            return []
                        cs = base(cs, d)
                        if sid in CARD:
                            cs = [c for c in cs if CARD[sid](c)]
                        return tiebreak(cs, _v, d["signal_date"])
                    rows = []
                    for day in p["days"]:
                        cs = rule([dict(c) for c in day["cards"]], day)
                        if not cs:
                            continue
                        r = evaluate_selection_policy(cs, SelectionPolicyContext(
                            score_line=72.0, output_count=3, market_snapshot=dict(day["market_snapshot"]), risk_template="balanced",
                            same_day_industry_counts=day["same_day_industry_counts"],
                            same_day_recommendation_count=day["same_day_recommendation_count"]), pol)
                        for c in r.recommendations:
                            code = str(c.get("code"))
                            if day["net_by_code"].get(code) is None:
                                continue
                            rows.append({"dt": day["signal_date"], "code": code})
                    sel[v] = rows
                need = defaultdict(set)
                for rows in sel.values():
                    for r in rows:
                        need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need)
                del p
                sim = {}

                def sim_of(dt, code):
                    k = (dt, code)
                    if k not in sim:
                        day = Day(); day.signal_date = dt
                        day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                        tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                        sim[k] = ((float(tr.net_return_pct), _d(tr.entry_date), _d(tr.exit_date))
                                  if tr and tr.executed and tr.exit_date else None)
                    return sim[k]

                allr, pan, nor = {}, {}, {}
                for v in names:
                    rows = []
                    for r in sel[v]:
                        s = sim_of(r["dt"], r["code"])
                        if s is not None:
                            rows.append({"dt": r["dt"], "code": r["code"], "net": s[0], "en": s[1], "ex": s[2],
                                         "panic": _d(r["dt"]) in panic})
                    allr[v] = rows
                    pan[v] = [r for r in rows if r["panic"]]
                    nor[v] = [r for r in rows if not r["panic"]]
                keep[w] = {"days": days, "all": allr, "pan": pan, "nor": nor}
                out = {"_days": len(days), "_panic_days_in_panel": npanic_days,
                       "_panic_days_with_trade_base": len({r["dt"] for r in pan["base"]}),
                       "all": _mean({v: _m(allr[v], days) for v in names}, names),
                       "panic": _mean({v: _m(pan[v], days) for v in names}, names),
                       "normal(=pause_on_panic)": _mean({v: _m(nor[v], days) for v in names}, names),
                       "panic_trades_base": [[r["dt"], r["code"], round(r["net"], 2)] for r in pan["base"]]}
                res[sid][w] = out
                print(sid, w, json.dumps({k: out[k] for k in out if k != "panic_trades_base"}, ensure_ascii=False), flush=True)
                outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            if "12m" in keep and "EXT" in keep:
                days = sorted(set(keep["12m"]["days"]) | set(keep["EXT"]["days"]))
                res[sid]["N3"] = {k: _mean({v: _m(keep["12m"][key][v] + keep["EXT"][key][v], days) for v in names}, names)
                                  for k, key in (("all", "all"), ("panic", "pan"), ("normal(=pause_on_panic)", "nor"))}
                print(sid, "N3", json.dumps(res[sid]["N3"], ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

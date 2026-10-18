"""Sprint4 §88：单策略出票数 / 分数线收紧（21 次选股平均口径：base 代码升序 + 20 次 hash 随机平手）。
预注册变体（output_count, score_line）：cur=(3,72) 现行；oc2=(2,72)；oc1=(1,72)；l74=(3,74)；l76=(3,76)。
出场按现行 exit_overrides / ma_break_exit 逐票重模拟（与 §85/§86 同口径）。
预注册采纳标准（相对 cur，均用 21 次平均）：
  1) 每笔均值 ≥3/4 窗高于 cur；2) 4 窗平均提升 ≥0.15pp；3) 12m 总收益（n×每笔均值之和）≥ cur 的 85%；
  4) 12m PF 不低于 cur。多个变体同时达标时取 4 窗平均提升最大者。
主检验对象 chen；chao-gu/sector/oversold 同口径附带检验（同一标准）。
"""
from __future__ import annotations
import argparse, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
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

VARIANTS = {"cur": (3, 72.0), "oc2": (2, 72.0), "oc1": (1, 72.0), "l74": (3, 74.0), "l76": (3, 76.0)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chen-xiaoqun-skill,chao-gu-yang-jia-skill,sector-relative-strength-leader,oversold-capital-reversal")
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/output_line.json")
    a = ap.parse_args(); base_pol = load_production_selection_policy(); res = {}
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    with SessionLocal() as db:
        for sid in a.sids.split(","):
            res[sid] = {}
            for w, f in panels(sid).items():
                p = json.loads(f.read_text()); _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]; allrows = {}
                for vn, (oc, sl) in VARIANTS.items():
                    pol = standalone_selection_policy(base_pol, sid, oc)
                    for v in names:
                        def rule(cs, d, _v=v):
                            if LIVE_DAY[sid](d["market_snapshot"]): return []
                            cs = base(cs, d)
                            if sid in CARD: cs = [c for c in cs if CARD[sid](c)]
                            return tiebreak(cs, _v, d["signal_date"])
                        _, rows = run_rule(rule, p, pol, {"output_count": oc, "score_line": sl}, apply_global_weights=False)
                        allrows[(vn, v)] = rows
                need = defaultdict(set)
                for rows in allrows.values():
                    for r in rows: need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need); cache = {}
                def net(dt, code):
                    if (dt, code) not in cache:
                        day = Day(); day.signal_date = dt; day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                        tr = (PE._simulate_card(day, cards[(dt, code)], ep) or {}).get("trade")
                        cache[(dt, code)] = float(tr.net_return_pct) if tr and tr.executed else None
                    return cache[(dt, code)]
                out = {}
                for vn in VARIANTS:
                    per = {}
                    for v in names:
                        rows = allrows[(vn, v)]
                        nrows = [{"dt": r["dt"], "code": r["code"], "net": x} for r in rows if (x := net(r["dt"], r["code"])) is not None]
                        mm = _metrics(nrows, days)
                        per[v] = {"n": mm["n"], "avg": mm["avg"], "pf": mm["pf"], "mdd": mm["mdd"], "wr": mm["wr"] * 100} if mm else {"n": 0, "avg": 0, "pf": 0, "mdd": 0, "wr": 0}
                    mean = lambda k: statistics.mean(per[v][k] for v in names)
                    out[vn] = {"n": round(mean("n"), 1), "avg": round(mean("avg"), 3), "pf": round(mean("pf"), 2), "mdd": round(mean("mdd"), 1),
                               "wr": round(mean("wr"), 1), "total": round(statistics.mean(per[v]["n"] * per[v]["avg"] for v in names), 1),
                               "paired_gt_cur": None}
                    out[vn]["_avgs"] = [round(per[v]["avg"], 3) for v in names]
                for vn in VARIANTS:
                    out[vn]["paired_gt_cur"] = round(sum(x > y for x, y in zip(out[vn]["_avgs"], out["cur"]["_avgs"])) / len(names), 2)
                res[sid][w] = out
                print(sid, w, {vn: {k: out[vn][k] for k in ("n", "avg", "pf", "mdd", "wr", "total", "paired_gt_cur")} for vn in VARIANTS}, flush=True)
                Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    # verdict
    verdict = {}
    for sid, ws in res.items():
        verdict[sid] = {}
        for vn in VARIANTS:
            if vn == "cur": continue
            wins = sum(ws[w][vn]["avg"] > ws[w]["cur"]["avg"] for w in ws)
            lift = statistics.mean(ws[w][vn]["avg"] - ws[w]["cur"]["avg"] for w in ws)
            r12 = ws.get("12m") or next(iter(ws.values()))
            ok_total = r12[vn]["total"] >= 0.85 * r12["cur"]["total"]
            ok_pf = r12[vn]["pf"] >= r12["cur"]["pf"]
            verdict[sid][vn] = {"wins": f"{wins}/{len(ws)}", "lift": round(lift, 3), "total_ok": ok_total, "pf_ok": ok_pf,
                                "pass": wins >= 3 and lift >= 0.15 and ok_total and ok_pf}
    res["_verdict"] = verdict
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)
if __name__ == "__main__":
    main()

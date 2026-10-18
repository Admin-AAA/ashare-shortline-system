"""Sprint4 §89：合格卡片内的排序特征单调性（日内横截面 rank IC + 日内三分位）。
做法：每策略每窗，用现行规则（LIVE_DAY 停手 + only:sid + CARD 过滤）取 score_line=72 下全部合格卡片
（output_count=50，即不截断），逐票按现行 exit_overrides 重模拟净收益。仅统计合格卡片 ≥4 张的日子。
对每个特征：日内 Spearman rank IC（特征 vs 净收益），取日均 IC；日内按特征排序取前 1/3 与后 1/3 的每笔均值。
特征（预注册，方向由 4 窗符号决定，不事后挑方向）：final_score、turnover_rate、primary_excess20、bias_ma5、
chg20、pos20、vol5_20、free_mv_yi、amount_yi、ivol20_pct、rsi_6、off_high60_pct、main_net_inflow_ratio、
eff20、max_dd20、shortline_leader_score、pct。
预注册"可进入排序检验"标准：日均 IC 4 窗同号且 |4 窗平均 IC| ≥0.05，且 (top1/3 − bot1/3) 价差 ≥3/4 窗同号、
4 窗平均 ≥0.5pp。（17 个特征多重检验，达标也只进入下一步预注册的选股检验，不直接上线。）
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
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, fv
from scripts.research_trend_regime_gate import CARD

FEATS = ["final_score", "turnover_rate", "primary_excess20", "bias_ma5", "chg20", "pos20", "vol5_20", "free_mv_yi",
         "amount_yi", "ivol20_pct", "rsi_6", "off_high60_pct", "main_net_inflow_ratio", "eff20", "max_dd20",
         "shortline_leader_score", "pct"]

def ranks(xs):
    o = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs); i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and xs[o[j + 1]] == xs[o[i]]: j += 1
        for k in range(i, j + 1): r[o[k]] = (i + j) / 2
        i = j + 1
    return r

def spearman(x, y):
    rx, ry = ranks(x), ranks(y); n = len(x); mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry)); sx = sum((a - mx) ** 2 for a in rx) ** .5; sy = sum((b - my) ** 2 for b in ry) ** .5
    return sxy / (sx * sy) if sx > 0 and sy > 0 else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chen-xiaoqun-skill,chao-gu-yang-jia-skill,sector-relative-strength-leader,oversold-capital-reversal")
    ap.add_argument("--out", default="output/research/sprint4/feature_ic.json")
    a = ap.parse_args(); base_pol = load_production_selection_policy(); res = {}
    ep = variant(); lim = ep.entry_lookahead_days + ep.horizon_days + 2
    with SessionLocal() as db:
        for sid in a.sids.split(","):
            res[sid] = {}; pol = standalone_selection_policy(base_pol, sid, 50)
            for w, f in panels(sid).items():
                p = json.loads(f.read_text()); _register_only(p)
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]
                def rule(cs, d):
                    if LIVE_DAY[sid](d["market_snapshot"]): return []
                    cs = base(cs, d)
                    if sid in CARD: cs = [c for c in cs if CARD[sid](c)]
                    return cs
                _, rows = run_rule(rule, p, pol, {"output_count": 50, "score_line": 72.0}, apply_global_weights=False)
                need = defaultdict(set)
                for r in rows: need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need); byday = defaultdict(list)
                for r in rows:
                    dt, code = r["dt"], r["code"]
                    day = Day(); day.signal_date = dt; day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                    tr = (PE._simulate_card(day, cards[(dt, code)], ep) or {}).get("trade")
                    if tr and tr.executed: byday[dt].append((cards[(dt, code)], float(tr.net_return_pct)))
                days = {d: v for d, v in byday.items() if len(v) >= 4}
                out = {"_days": len(days), "_cards": sum(len(v) for v in days.values()),
                       "_avg_all": round(statistics.mean(x for v in days.values() for _, x in v), 3) if days else None}
                for ft in FEATS:
                    ics, top, bot = [], [], []
                    for v in days.values():
                        pts = [(fv(c, ft) if ft != "final_score" else float(c.get("final_score") or 0), x) for c, x in v]
                        pts = [(q, x) for q, x in pts if q is not None]
                        if len(pts) < 4: continue
                        ic = spearman([q for q, _ in pts], [x for _, x in pts])
                        if ic is not None: ics.append(ic)
                        pts.sort(key=lambda t: t[0]); k = max(1, len(pts) // 3)
                        bot += [x for _, x in pts[:k]]; top += [x for _, x in pts[-k:]]
                    out[ft] = {"ic": round(statistics.mean(ics), 4) if ics else None, "ic_pos": round(sum(i > 0 for i in ics) / len(ics), 2) if ics else None,
                               "nd": len(ics), "top": round(statistics.mean(top), 3) if top else None, "bot": round(statistics.mean(bot), 3) if bot else None,
                               "spread": round(statistics.mean(top) - statistics.mean(bot), 3) if top and bot else None}
                res[sid][w] = out
                print(sid, w, out["_days"], out["_cards"], out["_avg_all"], {ft: (out[ft]["ic"], out[ft]["spread"]) for ft in FEATS}, flush=True)
                Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    verdict = {}
    for sid, ws in res.items():
        verdict[sid] = {}
        for ft in FEATS:
            ics = [ws[w][ft]["ic"] for w in ws if ws[w].get(ft, {}).get("ic") is not None]
            sps = [ws[w][ft]["spread"] for w in ws if ws[w].get(ft, {}).get("spread") is not None]
            if len(ics) < 4 or len(sps) < 4: verdict[sid][ft] = {"pass": False, "note": "insufficient"}; continue
            mic, msp = statistics.mean(ics), statistics.mean(sps); sg = 1 if mic > 0 else -1
            same_ic = all(i * sg > 0 for i in ics); sp_same = sum(s * sg > 0 for s in sps)
            verdict[sid][ft] = {"ic": [round(i, 3) for i in ics], "spread": [round(s, 2) for s in sps], "mean_ic": round(mic, 3), "mean_spread": round(msp, 2),
                                "pass": same_ic and abs(mic) >= 0.05 and sp_same >= 3 and msp * sg >= 0.5}
    res["_verdict"] = verdict
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("PASS", {sid: [ft for ft, v in vs.items() if v["pass"]] for sid, vs in verdict.items()}, flush=True)
if __name__ == "__main__":
    main()

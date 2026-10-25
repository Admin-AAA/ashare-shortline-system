"""§282 短线现役4技能新标准重评：只跑 BASE（生产选股 + EL_C 离场，base + 20 随机排序抽样 = 21 次），存逐笔并算收益来源指标。
框架同 research_mkt_gap_entry_veto.py §276（BASE 复现 R12 n≈500.9 +1.469% PF1.99；N3 n≈753.6 +1.147% PF1.80）。预注册见 docs §282。
窗口：EXT / 12m(=R12) / W0 / W00；N3 = 12m + EXT。每次抽样各自算指标再取平均（逐技能 + 4 技能合并）。"""
from __future__ import annotations
import gc, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path
os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts/midterm"))
import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.production_policy import SelectionPolicyContext, evaluate_selection_policy, load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD
from scripts.research_cooldown_filter import _m
from scripts.midterm.s282_quality import tail_metrics
SIDS = ("chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader")
NSEED = 20


def _d(x): return str(x)[:10].replace("-", "")


def one(rows, days):
    rows = [r for r in rows if r["net"] is not None]
    if not rows: return None
    m = _m(rows, days)
    seq = [r["net"] for r in sorted(rows, key=lambda r: (r["ex"], r["dt"], r["code"]))]
    t = tail_metrics(seq)
    return {"n": m["n"], "avg": m["avg"], "pf": m["pf"], "mdd": m["mdd"], "streak_m": m["streak"], "median": t["median"],
            "trim5": t["trim5"], "top10_share": t["top10_share"], "robust": t["robust"], "streak": t["streak"]}


def avg_draws(per_draw):
    per_draw = [x for x in per_draw if x]
    if not per_draw: return None
    out = {}
    for k in ("n", "avg", "pf", "mdd", "streak_m", "median", "trim5", "streak"):
        out[k] = round(statistics.fmean(x[k] for x in per_draw if x[k] is not None), 3)
    sh = [x["top10_share"] for x in per_draw if x["top10_share"] is not None]
    out["top10_share"] = round(statistics.fmean(sh), 3) if sh else None
    out["top10_red"] = bool(out["top10_share"] is not None and out["top10_share"] > 0.5)
    out["robust"] = bool(out["trim5"] > 0 and out["median"] > 0)
    out["robust_draw_share"] = round(sum(1 for x in per_draw if x["robust"]) / len(per_draw), 2)
    return out


def main():
    out_p = Path(os.environ.get("S282_OUT", str(B / "output/research/s282/short_quality.json")))
    out_p.parent.mkdir(parents=True, exist_ok=True)
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(20)]
    keep = {}
    with SessionLocal() as db:
        for w in ("EXT","12m","W0","W00"):
            picks = {v: [] for v in names}
            days_by_sid: dict = {}
            for sid in SIDS:
                pw = panels(sid)
                if w not in pw:
                    print("MISSING", sid, w, flush=True); continue
                pol = standalone_selection_policy(base_pol, sid, 3)
                p = json.loads(pw[w].read_text())
                _register_only(p)
                days_by_sid[sid] = [d["signal_date"] for d in p["days"]]
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]
                rows_by = {}
                for v in names:
                    rows = []
                    for day in p["days"]:
                        if LIVE_DAY[sid](day["market_snapshot"]):
                            continue
                        cs = base([dict(c) for c in day["cards"]], day)
                        if sid in CARD:
                            cs = [c for c in cs if CARD[sid](c)]
                        cs = tiebreak(cs, v, day["signal_date"])
                        if not cs:
                            continue
                        r = evaluate_selection_policy(cs, SelectionPolicyContext(
                            score_line=72.0, output_count=3, market_snapshot=dict(day["market_snapshot"]),
                            risk_template="balanced", same_day_industry_counts=day["same_day_industry_counts"],
                            same_day_recommendation_count=day["same_day_recommendation_count"]), pol)
                        for c in r.recommendations:
                            code = str(c.get("code"))
                            if day["net_by_code"].get(code) is None:
                                continue
                            rows.append({"sid": sid, "dt": day["signal_date"], "code": code})
                    rows_by[v] = rows
                need: dict = defaultdict(set)
                for rows in rows_by.values():
                    for r in rows:
                        need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need)
                del p
                sim: dict = {}
                for v in names:
                    for r in rows_by[v]:
                        k = (r["dt"], r["code"])
                        if k not in sim:
                            day = Day(); day.signal_date = r["dt"]
                            day.outcome_bars = {r["code"]: tuple(bars[r["dt"]].get(r["code"], ())[:lim])}
                            tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                            sim[k] = ((float(tr.net_return_pct), _d(tr.entry_date), _d(tr.exit_date))
                                      if tr and tr.executed and tr.exit_date else None)
                        s = sim[k]
                        r["net"], r["en"], r["ex"] = (s if s else (None, None, None))
                        picks[v].append(r)
                del cards, bars, sim, rows_by
                gc.collect()
            keep[w] = {"days_by_sid": days_by_sid, "tr": {v: [r for r in picks[v] if r["net"] is not None] for v in names}}
            print(w, "done", {sid: len(keep[w]["tr"]["base"]) for sid in ["base"]}, flush=True)
            del picks; gc.collect()
    keep["N3"] = {"days_by_sid": {s: sorted(set(keep["12m"]["days_by_sid"].get(s, [])) | set(keep["EXT"]["days_by_sid"].get(s, []))) for s in SIDS},
                  "tr": {v: keep["12m"]["tr"][v] + keep["EXT"]["tr"][v] for v in names}}
    res = {}
    for w in ("EXT", "12m", "W0", "W00", "N3"):
        k = keep[w]; res[w] = {}
        alld = sorted({d for ds in k["days_by_sid"].values() for d in ds})
        res[w]["ALL"] = avg_draws([one(k["tr"][v], alld) for v in names])
        for sid in SIDS:
            res[w][sid] = avg_draws([one([r for r in k["tr"][v] if r["sid"] == sid], k["days_by_sid"].get(sid, alld)) for v in names])
        print(w, "ALL", res[w]["ALL"], flush=True)
    json.dump(res, open(out_p, "w"), ensure_ascii=False, indent=1)
    json.dump({w: [r for r in keep[w]["tr"]["base"]] for w in ("12m", "EXT", "W0", "W00")}, open(out_p.with_name("short_trades_base.json"), "w"), ensure_ascii=False)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

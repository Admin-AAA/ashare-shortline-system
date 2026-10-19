"""Sprint4 §127：跨策略 / 策略内 同日同行业集中度上限。21 次选股平均，口径同 §104 / §121。

预注册写于 /workspace/strategy-research.md §127（2026-10-19 11:56 CST，结果出来前写定，无网格、不调参）。
  BASE 现行（4 策略各自出票）
  V1   跨 4 策略同日每行业最多 1 只（final_score 高者留；平手 = 同一抽样平手键，再按策略 id）
  V2   每策略内同日每行业最多 1 只
  V3   跨 4 策略同日每行业最多 2 只
  CTRL 每个变体一套：同一抽样、同一（策略, 日）删同样张数，随机删（20 种子）
参考：sector 先按 §104 C3 冷却剔除冷却日再做上限（_cd，不进判定）。
采纳（全部满足，最多 1 个，多个取 N3 每笔提升最大）：1) 组合每笔 R12/N3 > BASE；2) MDD R12/N3 严格优于 BASE；
3) PF R12/N3 ≥ BASE 且 ≥ 1.3；4) R12/N3 每笔 > 对照均值且 MDD 不差于对照均值；5) W0/W00 MDD 不比 BASE 差 > 3 点、每笔不低 0.3pp 以上；
6) n R12 ≥ 30、N3 ≥ 60，总 > 0。
"""
from __future__ import annotations
import argparse, bisect, gc, hashlib, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.aggregator_pure import _industry_group
from app.engine.production_policy import (SelectionPolicyContext, evaluate_selection_policy,
                                          load_production_selection_policy)
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD
from scripts.research_cooldown_filter import blocked_days, _m, _mean

SIDS = ("chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader")
VARIANTS = ("BASE", "V1", "V2", "V3")
PRIMARY = ("V1", "V2", "V3")
CHAIN = {"12m": "EXT", "W00": "W0"}
ORDER = ("EXT", "12m", "W0", "W00")
CD_SID = "sector-relative-strength-leader"
NSEED = 20


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def tie_key(draw: str, dt: str, code: str):
    return code if draw == "base" else _h(f"{draw[3:]}|{dt}|{code}")


def apply_cap(picks, vn, draw):
    """picks: list of dict(sid, dt, code, score, ind). 返回保留的 picks（顺序无关）。"""
    if vn == "BASE":
        return list(picks)
    cap = {"V1": 1, "V2": 1, "V3": 2}[vn]
    groups = defaultdict(list)
    for p in picks:
        if not p["ind"]:
            groups[("_u", p["sid"], p["dt"], p["code"])].append(p)
            continue
        k = (p["dt"], p["ind"]) if vn in ("V1", "V3") else (p["dt"], p["sid"], p["ind"])
        groups[k].append(p)
    keep = []
    for k, g in groups.items():
        if k[0] == "_u":
            keep.extend(g); continue
        g.sort(key=lambda p: (-p["score"], tie_key(draw, p["dt"], p["code"]), p["sid"]))
        keep.extend(g[:cap])
    return keep


def ctrl_drop(picks, kept, vn, seed):
    """同（策略, 日）删同样张数，随机删。"""
    by = defaultdict(list)
    for p in picks:
        by[(p["sid"], p["dt"])].append(p)
    nk = defaultdict(int)
    for p in kept:
        nk[(p["sid"], p["dt"])] += 1
    out = []
    for k, g in by.items():
        drop = len(g) - nk.get(k, 0)
        if drop <= 0:
            out.extend(g); continue
        g = sorted(g, key=lambda p: _h(f"{seed}|{vn}|{k[0]}|{k[1]}|{p['code']}"))
        out.extend(g[drop:])
    return out


def conc_stats(picks):
    byday = defaultdict(list)
    for p in picks:
        byday[p["dt"]].append(p)
    d_any = d_cross = d_within = d_dup = d_grp = 0
    for dt, g in byday.items():
        inds = defaultdict(list)
        grp = defaultdict(int)
        codes = defaultdict(int)
        for p in g:
            codes[p["code"]] += 1
            if p["ind"]:
                inds[p["ind"]].append(p["sid"])
                grp[_industry_group(p["ind"])] += 1
        multi = [s for s in inds.values() if len(s) >= 2]
        if multi:
            d_any += 1
        if any(len(set(s)) >= 2 for s in multi):
            d_cross += 1
        if any(len(s) > len(set(s)) for s in multi):
            d_within += 1
        if any(v >= 2 for v in codes.values()):
            d_dup += 1
        if any(v >= 2 for v in grp.values()):
            d_grp += 1
    n = len(byday)
    return {"pick_days": n, "picks": len(picks), "days_2plus_same_ind": d_any, "days_cross_strategy": d_cross,
            "days_within_strategy": d_within, "days_same_code_dup": d_dup, "days_2plus_same_group": d_grp,
            "share_2plus": round(d_any / n, 3) if n else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/sprint4/industry_cap.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ind_cache = json.loads((B / "data" / "industry_cache.json").read_text())
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED}}
    keep: dict = {}
    sector_shadow: dict = {}
    from sqlalchemy import text
    with SessionLocal() as db:
        cal = sorted({_d(r[0]) for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
        tdi = {d: i for i, d in enumerate(cal)}

        def T(dt):
            t = _d(dt)
            return tdi.get(t, bisect.bisect_right(cal, t) - 1)

        for w in a.windows.split(","):
            picks = {v: [] for v in names}   # draw -> list of pick dict (with sim)
            days_by_sid: dict = {}
            fill = {"card_ind": 0, "cache_ind": 0, "no_ind": 0}
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
                            ind = str(c.get("industry") or "").strip()
                            if ind and ind != "unknown":
                                fill["card_ind"] += 1
                            else:
                                ind = str(ind_cache.get(code) or "").strip()
                                if ind and ind != "unknown":
                                    fill["cache_ind"] += 1
                                else:
                                    ind = ""; fill["no_ind"] += 1
                            rows.append({"sid": sid, "dt": day["signal_date"], "code": code,
                                         "score": float(c.get("final_score") or 0), "ind": ind})
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
            all_days = sorted({d for ds in days_by_sid.values() for d in ds})

            def trades(ps):
                return [r for r in ps if r["net"] is not None]

            # sector C3 cooldown (reference)
            blk = {}
            sector_shadow[w] = {}
            for v in names:
                sh = sorted((r["ex"], r["en"], r["code"], r["net"]) for r in picks[v]
                            if r["sid"] == CD_SID and r["net"] is not None)
                sector_shadow[w][v] = sh
                hist = (sector_shadow.get(CHAIN[w], {}).get(v, []) if w in CHAIN else []) + sh
                hist.sort()
                blk[v] = blocked_days(hist, cal)["C3"]

            kw = {vn: {} for vn in VARIANTS}
            kcd = {vn: {} for vn in VARIANTS}
            ctrl = {vn: {} for vn in PRIMARY}
            removed = {vn: [] for vn in PRIMARY}
            per_sid = {vn: {sid: {} for sid in SIDS} for vn in VARIANTS}
            conc = [conc_stats(picks[v]) for v in names]
            for v in names:
                P = picks[v]
                Pcd = [r for r in P if not (r["sid"] == CD_SID and T(r["dt"]) in blk[v])]
                for vn in VARIANTS:
                    kept = apply_cap(P, vn, v)
                    kw[vn][v] = trades(kept)
                    kcd[vn][v] = trades(apply_cap(Pcd, vn, v))
                    for sid in SIDS:
                        per_sid[vn][sid][v] = [r for r in kw[vn][v] if r["sid"] == sid]
                    if vn != "BASE":
                        ks = {(r["sid"], r["dt"], r["code"]) for r in kept}
                        removed[vn].extend(r["net"] for r in P if r["net"] is not None
                                           and (r["sid"], r["dt"], r["code"]) not in ks)
                        ctrl[vn][v] = [trades(ctrl_drop(P, kept, vn, s)) for s in range(NSEED)]
            keep[w] = {"days": all_days, "days_by_sid": days_by_sid, "kw": kw, "kcd": kcd, "ctrl": ctrl,
                       "per_sid": per_sid}
            out = {"_days": len(all_days), "_ind_fill": fill,
                   "_conc": {k: round(statistics.mean(c[k] for c in conc), 2) for k in conc[0]}}
            out.update(summarize(keep[w], names, removed))
            res[w] = out
            print(w, json.dumps(out, ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            del picks
            gc.collect()

    if "12m" in keep and "EXT" in keep:
        a12, ae = keep["12m"], keep["EXT"]
        m = {"days": sorted(set(a12["days"]) | set(ae["days"])),
             "days_by_sid": {sid: sorted(set(a12["days_by_sid"].get(sid, [])) | set(ae["days_by_sid"].get(sid, [])))
                             for sid in SIDS},
             "kw": {vn: {v: a12["kw"][vn][v] + ae["kw"][vn][v] for v in names} for vn in VARIANTS},
             "kcd": {vn: {v: a12["kcd"][vn][v] + ae["kcd"][vn][v] for v in names} for vn in VARIANTS},
             "ctrl": {vn: {v: [a12["ctrl"][vn][v][s] + ae["ctrl"][vn][v][s] for s in range(NSEED)] for v in names}
                      for vn in PRIMARY},
             "per_sid": {vn: {sid: {v: a12["per_sid"][vn][sid][v] + ae["per_sid"][vn][sid][v] for v in names}
                              for sid in SIDS} for vn in VARIANTS}}
        rm = {vn: [] for vn in PRIMARY}
        for vn in PRIMARY:
            for wk in ("12m", "EXT"):
                rm[vn].append(res[wk][vn]["rm"])
        out = summarize(m, names, None)
        for vn in PRIMARY:
            n12, nE = res["12m"][vn]["rm"], res["EXT"][vn]["rm"]
            tot = n12["n"] + nE["n"]
            out[vn]["rm"] = {"n": round(tot, 1), "avg": round((n12["n"] * (n12["avg"] or 0) + nE["n"] * (nE["avg"] or 0)) / tot, 3)
                             if tot else None}
        res["N3"] = out
        print("N3", json.dumps(out, ensure_ascii=False), flush=True)

    verdict = {}
    for vn in PRIMARY:
        r12, n3, b12, b3 = res["12m"][vn], res["N3"][vn], res["12m"]["BASE"], res["N3"]["BASE"]
        c12, c3 = res["12m"][vn]["ctrl"], res["N3"][vn]["ctrl"]
        c1 = r12["avg"] > b12["avg"] and n3["avg"] > b3["avg"]
        c2 = r12["mdd"] > b12["mdd"] and n3["mdd"] > b3["mdd"]
        c3_ = r12["pf"] >= max(1.3, b12["pf"]) and n3["pf"] >= max(1.3, b3["pf"])
        c4 = r12["avg"] > c12["avg"] and n3["avg"] > c3["avg"] and r12["mdd"] >= c12["mdd"] and n3["mdd"] >= c3["mdd"]
        c5 = all(res[w][vn]["mdd"] >= res[w]["BASE"]["mdd"] - 3.0 and res[w][vn]["avg"] >= res[w]["BASE"]["avg"] - 0.3
                 for w in ("W0", "W00") if w in res)
        c6 = r12["n"] >= 30 and n3["n"] >= 60 and r12["total"] > 0 and n3["total"] > 0
        verdict[vn] = {"c1_avg": c1, "c2_mdd": c2, "c3_pf": c3_, "c4_ctrl": c4, "c5_crash": c5, "c6_n_total": c6,
                       "pass": all((c1, c2, c3_, c4, c5, c6)), "n3_lift": round(n3["avg"] - b3["avg"], 3)}
    passed = [vn for vn in PRIMARY if verdict[vn]["pass"]]
    verdict["_adopt"] = max(passed, key=lambda vn: verdict[vn]["n3_lift"]) if passed else None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


def summarize(k, names, removed):
    days = k["days"]
    out = {}
    for vn in VARIANTS:
        o = _mean({v: _m(k["kw"][vn][v], days) for v in names}, names)
        o["cd"] = _mean({v: _m(k["kcd"][vn][v], days) for v in names}, names)
        o["per_sid"] = {sid: _mean({v: _m(k["per_sid"][vn][sid][v], k["days_by_sid"].get(sid, days)) for v in names}, names)
                        for sid in SIDS}
        if vn != "BASE":
            per_draw = {}
            avgs = []
            for v in names:
                ms = [_m(rows, days) for rows in k["ctrl"][vn][v]]
                avgs.extend(x["avg"] for x in ms)
                per_draw[v] = {key: statistics.mean(x[key] for x in ms) for key in ms[0]}
            o["ctrl"] = _mean(per_draw, names)
            qs = statistics.quantiles(avgs, n=10)
            o["ctrl"]["avg_p10"], o["ctrl"]["avg_p90"] = round(qs[0], 3), round(qs[-1], 3)
            o["ctrl"]["beat_share"] = round(statistics.mean(
                1.0 if o["avg"] > x else 0.0 for x in avgs), 3)
            if removed is not None:
                rr = removed[vn]
                o["rm"] = {"n": round(len(rr) / len(names), 1), "avg": round(statistics.mean(rr), 3) if rr else None}
        out[vn] = o
    return out


if __name__ == "__main__":
    main()

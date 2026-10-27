"""Sprint4 §159：短线 到期日收盘封涨停就续持一夜。21 次选股平均，口径同 §150（research_market_crash_exit.py）。

预注册写于 docs/strategy-research.md §159（2026-10-25 00:27 CST，结果出来前写定，无网格、不调参）。
只改 BASE 已成交、离场原因 horizon_close 的单；出场日 x：封涨停 = close >= 前收*(1+band)-0.005 且 close >= high-0.005。
  LU_O：封涨停 -> x 下一根开盘卖（不可卖顺延下一可卖日开盘）。
  LU_C：封涨停 -> x 下一根收盘卖（收盘封跌停 -> 顺延下一可卖日开盘）。
  SC_O：x 收盘/前收-1 >= 5% -> x 下一根开盘卖。
  新净 = (新原始价 * k / 入场成交价 - 1) * 100 - fee；k = 1 - exit_slippage_bps/1e4；fee = 原 gross - 原 net。
对照 placebo：全部 horizon_close 单（有合规改写价）中按 md5 随机取同样笔数、同一时点改写，20 种子平均。
采纳 8 条同 §150。只读本地 solo 面板 + MySQL 日K（SELECT），不调 promax / tushare / 网关，不写库。
"""
from __future__ import annotations
import argparse, gc, hashlib, json, os, statistics, sys
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
from scripts.research_time_stop import _unsellable

SIDS = ("chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader")
VARIANTS = ("BASE", "LU_O", "LU_C", "SC_O")
PRIMARY = ("LU_O", "LU_C", "SC_O")
SPEC = {"LU_O": ("lu", "O"), "LU_C": ("lu", "C"), "SC_O": ("sc5", "O")}
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20
STATS = {"k_mismatch": 0, "k_checked": 0, "no_en_bar": 0, "push_next_open": 0, "push_no_bar": 0, "push_after_ex": 0, "horizon_close": 0, "lu": 0, "sc5": 0}


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _band(code: str) -> float:
    return 0.198 if code.startswith(("300", "301", "688")) else 0.098


def _next_sellable(bars, j0):
    for j in range(j0, len(bars)):
        if bars[j].open > 0 and not _unsellable(bars[j], bars[j - 1] if j > 0 else None):
            return j
    return None


def sim_card(card, bars, dt, ep, lim, kx):
    b = tuple(bars[:lim])
    day = Day(); day.signal_date = dt; day.outcome_bars = {card["code"]: b}
    tr = (PE._simulate_card(day, card, ep) or {}).get("trade")
    if not (tr and tr.executed and tr.exit_date and tr.entry_date):
        return None
    why = str(tr.exit_reason or "").split(":")[0]
    out = {"net": float(tr.net_return_pct), "en": _d(tr.entry_date), "ex": _d(tr.exit_date), "why": why}
    E, X = float(tr.entry_price), float(tr.exit_price)
    fee = float(tr.gross_return_pct) - float(tr.net_return_pct)
    ds = [_d(x.trade_date) for x in bars]
    if out["en"] not in ds or out["ex"] not in ds:
        STATS["no_en_bar"] += 1
        return out
    je, jx = ds.index(out["en"]), ds.index(out["ex"])
    if why != "horizon_close" or jx < 1 or jx + 1 >= len(bars):
        return out
    STATS["horizon_close"] += 1
    newnet = lambda raw: round((raw * kx / E - 1) * 100 - fee, 10)
    bx, bp = bars[jx], bars[jx - 1]
    band = _band(card["code"])
    lu = bx.close >= bp.close * (1 + band) - 0.005 and bx.close >= bx.high - 0.005
    sc5 = bx.close / bp.close - 1 >= 0.05
    STATS["lu"] += int(lu); STATS["sc5"] += int(sc5)
    cand = {}
    js = _next_sellable(bars, jx + 1)
    if js is None:
        cand["O"] = None; STATS["push_no_bar"] += 1
    else:
        if js > jx + 1: STATS["push_next_open"] += 1
        cand["O"] = (newnet(bars[js].open), ds[js])
    by, bq = bars[jx + 1], bars[jx]
    locked = by.close <= bq.close * (1 - band) + 0.005 and by.close <= by.low + 0.005
    if by.volume and float(by.volume) > 0 and not locked:
        cand["C"] = (newnet(by.close), ds[jx + 1])
    else:
        js2 = _next_sellable(bars, jx + 2)
        cand["C"] = None if js2 is None else (newnet(bars[js2].open), ds[js2])
    out["cand"] = cand; out["lu"] = lu; out["sc5"] = sc5
    return out


def variant_change(vn, s):
    """返回 (new_net, new_ex) 或 None（不改）。"""
    if s is None or "cand" not in s:
        return None
    flag, tm = SPEC[vn]
    return s["cand"][tm] if s[flag] else None


def _h(*a) -> int:
    return int(hashlib.md5("|".join(map(str, a)).encode()).hexdigest()[:12], 16)


def placebo_rows(P, S, tm, n_target, seed):
    elig = [i for i, r in enumerate(P) if S(r) is not None and S(r).get("cand", {}).get(tm) is not None]
    elig.sort(key=lambda i: _h(seed, P[i]["sid"], P[i]["dt"], P[i]["code"]))
    pick = set(elig[:n_target])
    rows = []
    for i, r in enumerate(P):
        if i in pick:
            nn, nx = S(r)["cand"][tm]
            rows.append(dict(r, net=nn, ex=nx))
        else:
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/s159/limitup_hold.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    kx = 1 - float(ep.exit_slippage_bps) / 1e4
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "kx": kx}}
    keep: dict = {}
    with SessionLocal() as db:
        for w in a.windows.split(","):
            picks = {v: [] for v in names}
            days_by_sid: dict = {}
            SIM: dict = {}
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
                for v in names:
                    for r in rows_by[v]:
                        k = (sid, r["dt"], r["code"])
                        if k not in SIM:
                            SIM[k] = sim_card(cards[(r["dt"], r["code"])], bars[r["dt"]].get(r["code"], ()), r["dt"], ep, lim, kx)
                        s = SIM[k]
                        if s is None:
                            continue
                        picks[v].append(dict(r, net=s["net"], en=s["en"], ex=s["ex"]))
                del cards, bars, rows_by
                gc.collect()
            all_days = sorted({d for ds in days_by_sid.values() for d in ds})
            S = lambda r: SIM[(r["sid"], r["dt"], r["code"])]
            kw = {vn: {} for vn in VARIANTS}
            plc = {vn: {sd: {} for sd in range(NSEED)} for vn in PRIMARY}
            aff = {vn: {"old": [], "new": [], "why": defaultdict(list), "sid": defaultdict(list)} for vn in PRIMARY}
            per_sid = {vn: {sid: {} for sid in SIDS} for vn in VARIANTS}
            for v in names:
                P = picks[v]
                kw["BASE"][v] = P
                for vn in PRIMARY:
                    rows = []; nchg = 0
                    for r in P:
                        ch = variant_change(vn, S(r))
                        if ch is not None:
                            nr = dict(r, net=ch[0], ex=ch[1]); rows.append(nr); nchg += 1
                            aff[vn]["old"].append(r["net"]); aff[vn]["new"].append(nr["net"])
                            aff[vn]["why"][S(r)["why"]].append((r["net"], nr["net"]))
                            aff[vn]["sid"][r["sid"]].append((r["net"], nr["net"]))
                        else:
                            rows.append(r)
                    kw[vn][v] = rows
                    for sd in range(NSEED):
                        plc[vn][sd][v] = placebo_rows(P, S, SPEC[vn][1], nchg, sd)
                for vn in VARIANTS:
                    for sid in SIDS:
                        per_sid[vn][sid][v] = [r for r in kw[vn][v] if r["sid"] == sid]
            nd = len(names)
            keep[w] = {"days": all_days, "days_by_sid": days_by_sid, "kw": kw, "per_sid": per_sid, "aff": aff, "plc": plc}
            out = {"_days": len(all_days), "_stats_cum": dict(STATS)}
            out.update(summarize(keep[w], names))
            for vn in PRIMARY:
                out[vn]["aff"] = affstat(aff[vn], nd)
                out[vn]["placebo"] = plc_stat(plc[vn], all_days, names)
            res[w] = out
            print(w, json.dumps(out, ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            del picks, SIM
            gc.collect()

    if "12m" in keep and "EXT" in keep:
        a12, ae = keep["12m"], keep["EXT"]
        days = sorted(set(a12["days"]) | set(ae["days"]))
        m = {"days": days,
             "days_by_sid": {sid: sorted(set(a12["days_by_sid"].get(sid, [])) | set(ae["days_by_sid"].get(sid, [])))
                             for sid in SIDS},
             "kw": {vn: {v: a12["kw"][vn][v] + ae["kw"][vn][v] for v in names} for vn in VARIANTS},
             "per_sid": {vn: {sid: {v: a12["per_sid"][vn][sid][v] + ae["per_sid"][vn][sid][v] for v in names}
                              for sid in SIDS} for vn in VARIANTS}}
        out = summarize(m, names)
        for vn in PRIMARY:
            A = {"old": a12["aff"][vn]["old"] + ae["aff"][vn]["old"], "new": a12["aff"][vn]["new"] + ae["aff"][vn]["new"],
                 "why": defaultdict(list), "sid": defaultdict(list)}
            for src in (a12, ae):
                for kk, xs in src["aff"][vn]["why"].items(): A["why"][kk] += xs
                for kk, xs in src["aff"][vn]["sid"].items(): A["sid"][kk] += xs
            out[vn]["aff"] = affstat(A, len(names))
            pl_ = {sd: {v: a12["plc"][vn][sd][v] + ae["plc"][vn][sd][v] for v in names} for sd in range(NSEED)}
            out[vn]["placebo"] = plc_stat(pl_, days, names)
        res["N3"] = out
        print("N3", json.dumps(out, ensure_ascii=False), flush=True)

    verdict = {}
    for vn in PRIMARY:
        r12, n3, b12, b3 = res["12m"][vn], res["N3"][vn], res["12m"]["BASE"], res["N3"]["BASE"]
        f12, f3 = r12["aff"], n3["aff"]
        c1 = r12["avg"] > b12["avg"] and n3["avg"] > b3["avg"]
        c2 = r12["pf"] >= max(1.3, b12["pf"]) and n3["pf"] >= max(1.3, b3["pf"])
        c3_ = r12["mdd"] >= b12["mdd"] and n3["mdd"] >= b3["mdd"]
        c4 = (r12["avg"] > r12["placebo"]["avg"] and n3["avg"] > n3["placebo"]["avg"]
              and f12.get("d_mean", 0) > 0 and f3.get("d_mean", 0) > 0
              and f12.get("d_mean_trim10", 0) > 0 and f3.get("d_mean_trim10", 0) > 0)
        c5 = all(res[w][vn]["mdd"] >= res[w]["BASE"]["mdd"] - 3.0 and res[w][vn]["avg"] >= res[w]["BASE"]["avg"] - 0.3
                 for w in ("W0", "W00") if w in res)
        testable = f12["n"] >= 10
        c6 = testable and f3["n"] >= 20
        c7 = r12["streak"] <= b12["streak"] + 1 and n3["streak"] <= b3["streak"] + 1
        c8 = r12["n"] >= 30 and n3["n"] >= 60 and r12["total"] > 0 and n3["total"] > 0
        verdict[vn] = {"c1_avg": c1, "c2_pf": c2, "c3_mdd": c3_, "c4_robust": c4, "c5_crash": c5, "c6_testable": c6,
                       "testable_r12_ge10": testable, "c7_streak": c7, "c8_n_total": c8,
                       "pass": all((c1, c2, c3_, c4, c5, c6, c7, c8)), "n3_lift": round(n3["avg"] - b3["avg"], 3)}
    passed = [vn for vn in PRIMARY if verdict[vn]["pass"]]
    verdict["_adopt"] = max(passed, key=lambda vn: verdict[vn]["n3_lift"]) if passed else None
    res["_verdict"] = verdict
    res["_stats"] = dict(STATS)
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


def plc_stat(plc, days, names):
    per_seed = [_mean({v: _m(plc[sd][v], days) for v in names}, names) for sd in sorted(plc)]
    return {k: round(statistics.fmean(x[k] for x in per_seed), 3) for k in ("avg", "pf", "mdd", "streak", "total")}


def affstat(A, nd):
    old, new = A["old"], A["new"]
    pair = lambda xs: {"n": round(len(xs) / nd, 1), "old": round(statistics.fmean(x for x, _ in xs), 3),
                       "new": round(statistics.fmean(y for _, y in xs), 3)} if xs else {"n": 0}
    o = {"n": round(len(old) / nd, 1), "old": round(statistics.fmean(old), 3) if old else 0.0,
         "new": round(statistics.fmean(new), 3) if new else 0.0,
         "by_why": {k: pair(v) for k, v in sorted(A["why"].items())},
         "by_sid": {k: pair(v) for k, v in sorted(A["sid"].items())}}
    if old:
        ds = sorted(y - x for x, y in zip(old, new))
        cut = ds[: len(ds) - max(1, len(ds) // 10)] if len(ds) >= 10 else ds[:-1]
        o.update(d_mean=round(statistics.fmean(ds), 3), d_median=round(statistics.median(ds), 3),
                 d_mean_trim10=round(statistics.fmean(cut), 3) if cut else 0.0,
                 d_pos_share=round(sum(1 for x in ds if x > 0) / len(ds), 3),
                 new_win=round(100 * sum(1 for x in new if x > 0) / len(new), 1))
    return o


def summarize(k, names):
    days = k["days"]
    out = {}
    for vn in VARIANTS:
        o = _mean({v: _m(k["kw"][vn][v], days) for v in names}, names)
        o["per_sid"] = {sid: _mean({v: _m(k["per_sid"][vn][sid][v], k["days_by_sid"].get(sid, days)) for v in names}, names)
                        for sid in SIDS}
        out[vn] = o
    return out


if __name__ == "__main__":
    main()

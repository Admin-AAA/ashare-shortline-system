"""Sprint4 §132：短线 滞涨时间止损（买入后第 2 / 3 天收盘仍未盈利即离场）。21 次选股平均，口径同 §127 / §129 / §130。

预注册写于 /workspace/strategy-research.md §132（2026-10-19 13:26 CST，结果出来前写定，无网格、不调参）。
第 k 天 = 以实际入场日为第 1 天的第 k 根 K 线；BASE 成交原离场日晚于第 k 天、第 k 天可卖（非一字跌停、有量）时检查：
  TS2  close_2 <= 入场价 → 第 2 天收盘离场
  TS3  close_3 <= 入场价 → 第 3 天收盘离场
  TS2L close_2 <  入场价 × 0.97 → 第 2 天收盘离场
离场 = 持仓 K 线截到第 k 天、同一执行核（含 exit_overrides / ma_break）重模拟；离场日 ≠ 第 k 天则保留原结果。
CTRL 每个变体一套：同一抽样、同一策略，从"第 k 天收盘仍持仓且可卖"的成交里按 md5 随机选同样笔数无条件第 k 天离场（20 种子）。
采纳（8 条全部满足，最多 1 个，多个取 N3 每笔提升最大）：1) 每笔 R12/N3 > BASE；2) PF R12/N3 ≥ BASE 且 ≥ 1.3；
3) MDD R12/N3 ≥ BASE；4) R12/N3 每笔 > 对照均值且 MDD 不差于对照均值；5) W0/W00 MDD 不比 BASE 差 > 3 点、每笔不低 0.3pp 以上；
6) 被改单 R12 ≥ 10（否则不可检验 → 否决）、N3 ≥ 20，原净均值 ≤ 0 且 新净均值 > 原净均值；7) 最长连亏 R12/N3 ≤ BASE + 1；
8) 总 R12/N3 > 0、n R12 ≥ 30 / N3 ≥ 60。
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

SIDS = ("chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader")
VARIANTS = ("BASE", "TS2", "TS3", "TS2L")
PRIMARY = ("TS2", "TS3", "TS2L")
KDAY = {"TS2": 2, "TS3": 3, "TS2L": 2}
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _unsellable(bar, prev) -> bool:
    if not bar.volume or float(bar.volume) <= 0:
        return True
    one_word = abs(bar.high - bar.low) < 1e-9
    return bool(one_word and prev is not None and bar.close < prev.close * 0.95)


def sim_card(card, bars, dt, ep, lim):
    """BASE 成交 + 第 2 / 3 天收盘信息与截断重模拟结果。"""
    b = tuple(bars[:lim])
    day = Day(); day.signal_date = dt; day.outcome_bars = {card["code"]: b}
    tr = (PE._simulate_card(day, card, ep) or {}).get("trade")
    if not (tr and tr.executed and tr.exit_date and tr.entry_date):
        return None
    out = {"net": float(tr.net_return_pct), "en": _d(tr.entry_date), "ex": _d(tr.exit_date),
           "ep": float(tr.entry_price or 0), "why": str(tr.exit_reason or "")}
    ie = next((i for i, x in enumerate(b) if _d(x.trade_date) == out["en"]), None)
    if ie is None or out["ep"] <= 0:
        return out
    for k in (2, 3):
        j = ie + k - 1
        if j >= len(b):
            continue
        bk = b[j]
        if _d(bk.trade_date) >= out["ex"] or _unsellable(bk, b[j - 1] if j > 0 else None):
            continue
        d2 = Day(); d2.signal_date = dt; d2.outcome_bars = {card["code"]: b[:j + 1]}
        nt = (PE._simulate_card(d2, card, ep) or {}).get("trade")
        if not (nt and nt.executed and nt.exit_date and _d(nt.exit_date) == _d(bk.trade_date)
                and _d(nt.entry_date) == out["en"]):
            continue
        out[f"c{k}"] = float(bk.close)
        out[f"cut{k}"] = (float(nt.net_return_pct), _d(nt.exit_date))
    return out


def trig(vn, s):
    k = KDAY[vn]
    if s is None or f"cut{k}" not in s:
        return False
    ck, ep_ = s[f"c{k}"], s["ep"]
    return ck < ep_ * 0.97 if vn == "TS2L" else ck <= ep_


def apply(r, s, k):
    net, ex = s[f"cut{k}"]
    return dict(r, net=net, ex=ex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/sprint4/time_stop.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED, "kday": KDAY}}
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
                            SIM[k] = sim_card(cards[(r["dt"], r["code"])], bars[r["dt"]].get(r["code"], ()), r["dt"], ep, lim)
                        s = SIM[k]
                        if s is None:
                            continue
                        picks[v].append(dict(r, net=s["net"], en=s["en"], ex=s["ex"]))
                del cards, bars, rows_by
                gc.collect()
            all_days = sorted({d for ds in days_by_sid.values() for d in ds})
            S = lambda r: SIM[(r["sid"], r["dt"], r["code"])]
            kw = {vn: {} for vn in VARIANTS}
            ctrl = {vn: {} for vn in PRIMARY}
            aff = {vn: {"old": [], "new": [], "why": defaultdict(list), "sid": defaultdict(list)} for vn in PRIMARY}
            per_sid = {vn: {sid: {} for sid in SIDS} for vn in VARIANTS}
            info = {"held_k": {2: 0, 3: 0}, "held_k_le_entry": {2: 0, 3: 0}}
            for v in names:
                P = picks[v]
                kw["BASE"][v] = P
                for r in P:
                    s = S(r)
                    for kk in (2, 3):
                        if f"cut{kk}" in s:
                            info["held_k"][kk] += 1
                            info["held_k_le_entry"][kk] += int(s[f"c{kk}"] <= s["ep"])
                for vn in PRIMARY:
                    k = KDAY[vn]
                    rows, nby = [], defaultdict(int)
                    for r in P:
                        s = S(r)
                        if trig(vn, s):
                            nr = apply(r, s, k); rows.append(nr); nby[r["sid"]] += 1
                            aff[vn]["old"].append(r["net"]); aff[vn]["new"].append(nr["net"])
                            aff[vn]["why"][s["why"].split(":")[0]].append((r["net"], nr["net"]))
                            aff[vn]["sid"][r["sid"]].append((r["net"], nr["net"]))
                        else:
                            rows.append(r)
                    kw[vn][v] = rows
                    cl = []
                    for seed in range(NSEED):
                        chosen = set()
                        for sid in SIDS:
                            el = [r for r in P if r["sid"] == sid and f"cut{k}" in S(r)]
                            el.sort(key=lambda r: _h(f"{seed}|{vn}|{sid}|{r['dt']}|{r['code']}"))
                            chosen |= {(r["sid"], r["dt"], r["code"]) for r in el[:nby[sid]]}
                        cl.append([apply(r, S(r), k) if (r["sid"], r["dt"], r["code"]) in chosen else r for r in P])
                    ctrl[vn][v] = cl
                for vn in VARIANTS:
                    for sid in SIDS:
                        per_sid[vn][sid][v] = [r for r in kw[vn][v] if r["sid"] == sid]
            nd = len(names)
            keep[w] = {"days": all_days, "days_by_sid": days_by_sid, "kw": kw, "ctrl": ctrl, "per_sid": per_sid, "aff": aff}
            out = {"_days": len(all_days),
                   "_info": {"held_k": {k: round(x / nd, 1) for k, x in info["held_k"].items()},
                             "held_k_le_entry_share": {k: round(info["held_k_le_entry"][k] / max(1, info["held_k"][k]), 3) for k in (2, 3)}}}
            out.update(summarize(keep[w], names))
            for vn in PRIMARY:
                out[vn]["aff"] = affstat(aff[vn], nd)
            res[w] = out
            print(w, json.dumps(out, ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            del picks, SIM
            gc.collect()

    if "12m" in keep and "EXT" in keep:
        a12, ae = keep["12m"], keep["EXT"]
        m = {"days": sorted(set(a12["days"]) | set(ae["days"])),
             "days_by_sid": {sid: sorted(set(a12["days_by_sid"].get(sid, [])) | set(ae["days_by_sid"].get(sid, [])))
                             for sid in SIDS},
             "kw": {vn: {v: a12["kw"][vn][v] + ae["kw"][vn][v] for v in names} for vn in VARIANTS},
             "ctrl": {vn: {v: [a12["ctrl"][vn][v][s] + ae["ctrl"][vn][v][s] for s in range(NSEED)] for v in names}
                      for vn in PRIMARY},
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
        res["N3"] = out
        print("N3", json.dumps(out, ensure_ascii=False), flush=True)

    verdict = {}
    for vn in PRIMARY:
        r12, n3, b12, b3 = res["12m"][vn], res["N3"][vn], res["12m"]["BASE"], res["N3"]["BASE"]
        c12, c3 = r12["ctrl"], n3["ctrl"]
        c1 = r12["avg"] > b12["avg"] and n3["avg"] > b3["avg"]
        c2 = r12["pf"] >= max(1.3, b12["pf"]) and n3["pf"] >= max(1.3, b3["pf"])
        c3_ = r12["mdd"] >= b12["mdd"] and n3["mdd"] >= b3["mdd"]
        c4 = r12["avg"] > c12["avg"] and n3["avg"] > c3["avg"] and r12["mdd"] >= c12["mdd"] and n3["mdd"] >= c3["mdd"]
        c5 = all(res[w][vn]["mdd"] >= res[w]["BASE"]["mdd"] - 3.0 and res[w][vn]["avg"] >= res[w]["BASE"]["avg"] - 0.3
                 for w in ("W0", "W00") if w in res)
        f12, f3 = r12["aff"], n3["aff"]
        testable = f12["n"] >= 10
        c6 = (testable and f3["n"] >= 20 and f12["old"] <= 0 and f3["old"] <= 0
              and f12["new"] > f12["old"] and f3["new"] > f3["old"])
        c7 = r12["streak"] <= b12["streak"] + 1 and n3["streak"] <= b3["streak"] + 1
        c8 = r12["n"] >= 30 and n3["n"] >= 60 and r12["total"] > 0 and n3["total"] > 0
        verdict[vn] = {"c1_avg": c1, "c2_pf": c2, "c3_mdd": c3_, "c4_ctrl": c4, "c5_crash": c5, "c6_affected": c6,
                       "testable_r12_ge10": testable, "c7_streak": c7, "c8_n_total": c8,
                       "pass": all((c1, c2, c3_, c4, c5, c6, c7, c8)), "n3_lift": round(n3["avg"] - b3["avg"], 3)}
    passed = [vn for vn in PRIMARY if verdict[vn]["pass"]]
    verdict["_adopt"] = max(passed, key=lambda vn: verdict[vn]["n3_lift"]) if passed else None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


def affstat(A, nd):
    old, new = A["old"], A["new"]
    pair = lambda xs: {"n": round(len(xs) / nd, 1), "old": round(statistics.fmean(x for x, _ in xs), 3),
                       "new": round(statistics.fmean(y for _, y in xs), 3)} if xs else {"n": 0}
    return {"n": round(len(old) / nd, 1), "old": round(statistics.fmean(old), 3) if old else 0.0,
            "new": round(statistics.fmean(new), 3) if new else 0.0,
            "old_win": round(100 * sum(1 for x in old if x > 0) / len(old), 1) if old else None,
            "by_why": {k: pair(v) for k, v in sorted(A["why"].items())},
            "by_sid": {k: pair(v) for k, v in sorted(A["sid"].items())}}


def summarize(k, names):
    days = k["days"]
    out = {}
    for vn in VARIANTS:
        o = _mean({v: _m(k["kw"][vn][v], days) for v in names}, names)
        o["per_sid"] = {sid: _mean({v: _m(k["per_sid"][vn][sid][v], k["days_by_sid"].get(sid, days)) for v in names}, names)
                        for sid in SIDS}
        if vn != "BASE":
            per_draw, avgs = {}, []
            for v in names:
                ms = [_m(rows, days) for rows in k["ctrl"][vn][v]]
                avgs.extend(x["avg"] for x in ms)
                per_draw[v] = {key: statistics.mean(x[key] for x in ms) for key in ms[0]}
            o["ctrl"] = _mean(per_draw, names)
            qs = statistics.quantiles(avgs, n=10)
            o["ctrl"]["avg_p10"], o["ctrl"]["avg_p90"] = round(qs[0], 3), round(qs[-1], 3)
            o["ctrl"]["beat_share"] = round(statistics.mean(1.0 if o["avg"] > x else 0.0 for x in avgs), 3)
        out[vn] = o
    return out


if __name__ == "__main__":
    main()

"""Sprint4 §151：短线跨策略资金分配（策略层权重）vs 现行逐票等权。21 次选股平均，口径同 §127–§150。

预注册写于 /workspace/strategy-research.md §151（2026-10-19 18:53 CST，结果出来前写定，无网格、不调参）。
  BASE    现行逐票等权（每个信号日 4 策略合并组合，每只 1/N）
  SA_EQS  raw = 1/n_k（每个出票策略等资金，再在自己的票间平分）
  SA_EXP  raw = max(m_k, 0.25)，m_k = 该策略出场日 <= T 的最近 30 笔净收益均值（<10 笔 = 预热）
  SA_IV   raw = 1/max(sd_k, 1.0)，sd_k = 同上 30 笔样本标准差
  三版：当日归一，夹 [0.5/N, 2/N]，反复夹-归一至收敛（同 §129 clip_norm）；预热策略 raw = 当天非预热策略 raw 中位数，全预热 -> 等权。
  CTRL 每版一套：同日把该版权重在当天票之间随机打乱（20 种子）。
  历史衔接：12m 接 EXT、W00 接 W0（同一抽样名）。
采纳（全部满足，最多 1 个，多个取 N3 每笔提升最大）：同 §129 的 7 条 + 8) R12/N3 多策略同日出票天数各 >= 15。
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
from app.engine.production_policy import (SelectionPolicyContext, evaluate_selection_policy,
                                          load_production_selection_policy)
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

SIDS = ("chao-gu-yang-jia-skill", "chen-xiaoqun-skill", "oversold-capital-reversal", "sector-relative-strength-leader")
VARIANTS = ("BASE", "SA_EQS", "SA_EXP", "SA_IV")
PRIMARY = ("SA_EQS", "SA_EXP", "SA_IV")
ORDER = ("EXT", "12m", "W0", "W00")
PRIOR = {"12m": "EXT", "W00": "W0"}
NSEED = 20
LOOK = 30
MINN = 10
EXP_FLOOR = 0.25
SD_FLOOR = 1.0
LO, HI = 0.5, 2.0


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def clip_norm(raw: list[float]) -> list[float]:
    n = len(raw)
    s = sum(raw)
    w = [x / s for x in raw]
    lo, hi = LO / n, HI / n
    for _ in range(20):
        c = [min(max(x, lo), hi) for x in w]
        s = sum(c)
        c = [x / s for x in c]
        if max(abs(a - b) for a, b in zip(c, w)) < 1e-12:
            w = c
            break
        w = c
    return w


def wmetrics(rows: list[dict], days: list[str], wkey: str) -> dict:
    """rows: trades with net, dt, ex, code, and r[wkey] = normalized day weight。"""
    if not rows:
        return {"n": 0, "avg": 0, "pf": 0, "mdd": 0, "wr": 0, "wwr": 0, "streak": 0, "dstreak": 0, "aw": 0, "al": 0, "total": 0}
    nday = defaultdict(int)
    for r in rows:
        nday[r["dt"]] += 1
    n = len(rows)
    gp = gl = tot = wwin = sw_w = sw_l = 0.0
    daily = defaultdict(float)
    for r in rows:
        w = r[wkey]
        wt = w * nday[r["dt"]]
        x = r["net"]
        tot += wt * x
        daily[r["dt"]] += w * x
        if x > 0:
            gp += wt * x; wwin += wt; sw_w += wt
        elif x < 0:
            gl += wt * x; sw_l += wt
    eq = peak = mdd = 0.0
    for d in days:
        eq += daily.get(d, 0.0)
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    s = ds = 0
    best = dbest = 0
    for d in sorted(daily):
        ds = ds + 1 if daily[d] < 0 else 0
        dbest = max(dbest, ds)
    for r in sorted(rows, key=lambda r: (r["ex"], r["dt"], r["code"])):
        s = s + 1 if r["net"] < 0 else 0
        best = max(best, s)
    return {"n": n, "avg": tot / n, "pf": gp / max(abs(gl), 1e-9), "mdd": mdd,
            "wr": 100.0 * sum(1 for r in rows if r["net"] > 0) / n, "wwr": 100.0 * wwin / n,
            "streak": best, "dstreak": dbest, "aw": gp / sw_w if sw_w else 0.0, "al": gl / sw_l if sw_l else 0.0,
            "total": tot}


def mean_m(ms: list[dict]) -> dict:
    keys = ms[0].keys()
    rnd = {"n": 1, "avg": 3, "pf": 2, "mdd": 1, "wr": 1, "wwr": 1, "streak": 1, "dstreak": 1, "aw": 2, "al": 2, "total": 1}
    return {k: round(statistics.mean(m[k] for m in ms), rnd.get(k, 3)) for k in keys}




class Hist:
    """每个抽样、每个策略：按出场日排序的 (ex, net)。stat(sid, T) 取 ex <= T 的最近 LOOK 笔。"""

    def __init__(self, rows):
        self.by = defaultdict(list)
        for r in rows:
            self.by[r["sid"]].append((r["ex"], r["net"]))
        for k in self.by:
            self.by[k].sort()
        self.keys = {k: [x[0] for x in v] for k, v in self.by.items()}

    def stat(self, sid, t):
        ks = self.keys.get(sid)
        if not ks:
            return None
        j = bisect.bisect_right(ks, t)
        win = [x[1] for x in self.by[sid][max(0, j - LOOK):j]]
        if len(win) < MINN:
            return None
        return statistics.fmean(win), statistics.stdev(win)


def day_weights(rows: list[dict], vn: str, hist: Hist | None, warm: dict) -> list[float]:
    n = len(rows)
    if vn == "BASE" or n == 1:
        return [1.0 / n] * n
    if vn == "SA_EQS":
        cnt = defaultdict(int)
        for r in rows:
            cnt[r["sid"]] += 1
        return clip_norm([1.0 / cnt[r["sid"]] for r in rows])
    t = _d(rows[0]["dt"])
    sraw = {}
    for sid in {r["sid"] for r in rows}:
        st = hist.stat(sid, t) if hist else None
        warm["tot"] += 1
        if st is None:
            sraw[sid] = None
            warm["warm"] += 1
            continue
        m, sd = st
        sraw[sid] = max(m, EXP_FLOOR) if vn == "SA_EXP" else 1.0 / max(sd, SD_FLOOR)
    have = [v for v in sraw.values() if v is not None]
    if not have:
        return [1.0 / n] * n
    med = statistics.median(have)
    raw = [sraw[r["sid"]] if sraw[r["sid"]] is not None else med for r in rows]
    return clip_norm(raw)


def assign(trades_by_draw: dict, names: list[str], prior: dict | None) -> dict:
    warm = {vn: {"tot": 0, "warm": 0} for vn in PRIMARY}
    for v in names:
        hrows = list(trades_by_draw[v]) + (list(prior[v]) if prior else [])
        hist = Hist(hrows)
        byday = defaultdict(list)
        for r in trades_by_draw[v]:
            byday[r["dt"]].append(r)
        for dt, g in byday.items():
            g.sort(key=lambda r: (r["sid"], r["code"]))
            for vn in VARIANTS:
                ws = day_weights(g, vn, hist, warm.get(vn, {"tot": 0, "warm": 0}))
                for r, w in zip(g, ws):
                    r["w_" + vn] = w
                if vn == "BASE":
                    continue
                for s in range(NSEED):
                    order = sorted(range(len(g)), key=lambda i: _h(f"{s}|{vn}|{dt}|{g[i]['sid']}|{g[i]['code']}"))
                    for pos, i in enumerate(order):
                        g[i][f"c{s}_{vn}"] = ws[pos]
    return {vn: round(x["warm"] / x["tot"], 3) if x["tot"] else None for vn, x in warm.items()}


def summarize(trades_by_draw: dict, days: list[str], names: list[str]) -> dict:
    out = {}
    for vn in VARIANTS:
        o = mean_m([wmetrics(trades_by_draw[v], days, "w_" + vn) for v in names])
        if vn != "BASE":
            per = []
            avgs = []
            for v in names:
                ms = [wmetrics(trades_by_draw[v], days, f"c{s}_{vn}") for s in range(NSEED)]
                avgs.extend(m["avg"] for m in ms)
                per.append({k: statistics.mean(m[k] for m in ms) for k in ms[0]})
            c = mean_m(per)
            qs = statistics.quantiles(avgs, n=10)
            c["avg_p10"], c["avg_p90"] = round(qs[0], 3), round(qs[-1], 3)
            c["beat_share"] = round(statistics.mean(1.0 if o["avg"] > x else 0.0 for x in avgs), 3)
            o["ctrl"] = c
        # 各策略资金占比（全窗口 Σw 占比，21 次平均）
        shares = []
        for v in names:
            tot = defaultdict(float)
            for r in trades_by_draw[v]:
                tot[r["sid"]] += r["w_" + vn]
            s = sum(tot.values()) or 1.0
            shares.append({k: tot.get(k, 0.0) / s for k in SIDS})
        o["_share"] = {k.split("-")[0]: round(statistics.mean(x[k] for x in shares), 3) for k in SIDS}
        out[vn] = o
    # 信息项
    per_sid = {}
    for sid in SIDS:
        ms = []
        for v in names:
            xs = [r["net"] for r in trades_by_draw[v] if r["sid"] == sid]
            if not xs:
                continue
            gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
            ms.append({"n": len(xs), "wr": 100.0 * sum(1 for x in xs if x > 0) / len(xs),
                       "avg": statistics.fmean(xs), "pf": gp / max(gl, 1e-9)})
        per_sid[sid.split("-")[0]] = mean_m(ms) if ms else None
    out["_per_sid_base"] = per_sid
    # 信息项：多策略同日出票的日子，按"该策略当天成交笔数 n_k"分桶的每笔（21 次合并）
    bk = defaultdict(list)
    for v in names:
        g = defaultdict(list)
        for r in trades_by_draw[v]:
            g[r["dt"]].append(r)
        for dt, rs in g.items():
            sids = defaultdict(int)
            for r in rs:
                sids[r["sid"]] += 1
            if len(sids) < 2:
                continue
            for r in rs:
                bk[sids[r["sid"]]].append(r["net"])
    out["_multi_by_nk"] = {str(k): {"n": round(len(v) / len(names), 1), "avg": round(statistics.fmean(v), 3)}
                           for k, v in sorted(bk.items())}
    nday = defaultdict(int)
    nsid = defaultdict(set)
    for r in trades_by_draw["base"]:
        nday[r["dt"]] += 1
        nsid[r["dt"]].add(r["sid"])
    multi = [sum(1 for d in {r["dt"] for r in trades_by_draw[v]}
                 if len({r["sid"] for r in trades_by_draw[v] if r["dt"] == d}) >= 2) for v in names]
    out["_ndays"] = {"trade_days": len(nday), "single_trade_days": sum(1 for x in nday.values() if x == 1),
                     "multi_sid_days_base": sum(1 for s in nsid.values() if len(s) >= 2),
                     "multi_sid_days_avg": round(statistics.fmean(multi), 1),
                     "avg_n": round(statistics.fmean(nday.values()), 2) if nday else 0}
    for vn in PRIMARY:
        wt = [r["w_" + vn] / r["w_BASE"] for r in trades_by_draw["base"]]
        out[vn]["_wratio"] = {"min": round(min(wt), 2), "max": round(max(wt), 2),
                              "sd": round(statistics.pstdev(wt), 3)} if wt else None
    return out


def collect(db, w, names, base_pol, ep, lim):
    picks = {v: [] for v in names}
    days_all: set = set()
    for sid in SIDS:
        pw = panels(sid)
        if w not in pw:
            print("MISSING", sid, w, flush=True); continue
        pol = standalone_selection_policy(base_pol, sid, 3)
        p = json.loads(pw[w].read_text())
        _register_only(p)
        days_all.update(d["signal_date"] for d in p["days"])
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
                if s is None:
                    continue
                r["net"], r["en"], r["ex"] = s
                picks[v].append(r)
        del cards, bars, sim, rows_by
        gc.collect()
    return picks, sorted(days_all)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/strategy_alloc.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED, "look": LOOK, "minn": MINN}}
    keep: dict = {}
    with SessionLocal() as db:
        for w in ORDER:
            picks, days = collect(db, w, names, base_pol, ep, lim)
            pr = keep[PRIOR[w]]["picks"] if w in PRIOR and PRIOR[w] in keep else None
            warm = assign(picks, names, pr)
            keep[w] = {"days": days, "picks": picks}
            out = {"_days": len(days), "_warm_share": warm}
            out.update(summarize(picks, days, names))
            res[w] = out
            print(w, json.dumps(out, ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            gc.collect()

    days = sorted(set(keep["12m"]["days"]) | set(keep["EXT"]["days"]))
    merged = {v: keep["EXT"]["picks"][v] + keep["12m"]["picks"][v] for v in names}
    out = summarize(merged, days, names)
    res["N3"] = out
    print("N3", json.dumps(out, ensure_ascii=False), flush=True)

    verdict = {}
    for vn in PRIMARY:
        r12, n3, b12, b3 = res["12m"][vn], res["N3"][vn], res["12m"]["BASE"], res["N3"]["BASE"]
        c12, c3 = r12["ctrl"], n3["ctrl"]
        c1 = r12["avg"] > b12["avg"] and n3["avg"] > b3["avg"]
        c2 = r12["pf"] > b12["pf"] and n3["pf"] > b3["pf"] and r12["pf"] >= 1.3 and n3["pf"] >= 1.3
        c3_ = r12["mdd"] >= b12["mdd"] and n3["mdd"] >= b3["mdd"]
        c4 = r12["avg"] > c12["avg"] and n3["avg"] > c3["avg"] and r12["mdd"] >= c12["mdd"] and n3["mdd"] >= c3["mdd"]
        c5 = r12["dstreak"] <= b12["dstreak"] + 0.5 and n3["dstreak"] <= b3["dstreak"] + 0.5
        c6 = all(res[w][vn]["mdd"] >= res[w]["BASE"]["mdd"] - 3.0 and res[w][vn]["avg"] >= res[w]["BASE"]["avg"] - 0.3
                 for w in ("W0", "W00") if w in res)
        c7 = r12["avg"] > 0 and n3["avg"] > 0 and r12["n"] >= 30 and n3["n"] >= 60
        c8 = res["12m"]["_ndays"]["multi_sid_days_avg"] >= 15 and res["N3"]["_ndays"]["multi_sid_days_avg"] >= 15
        verdict[vn] = {"c1_avg": c1, "c2_pf": c2, "c3_mdd": c3_, "c4_ctrl": c4, "c5_streak": c5, "c6_crash": c6,
                       "c7_exp_n": c7, "c8_testable": c8, "pass": all((c1, c2, c3_, c4, c5, c6, c7, c8)),
                       "n3_lift": round(n3["avg"] - b3["avg"], 3), "r12_lift": round(r12["avg"] - b12["avg"], 3)}
    passed = [vn for vn in PRIMARY if verdict[vn]["pass"]]
    verdict["_adopt"] = max(passed, key=lambda vn: verdict[vn]["n3_lift"]) if passed else None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

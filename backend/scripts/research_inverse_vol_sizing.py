"""Sprint4 §129：短线同日入选票按波动率倒数分配仓位 vs 现行等权。21 次选股平均，口径同 §104 / §121 / §127。

预注册写于 /workspace/strategy-research.md §129（2026-10-19 12:23 CST，结果出来前写定，无网格、不调参）。
  BASE 现行等权（每个信号日 4 策略合并组合，每只 1/N）
  IV1  原始权重 ∝ 1/vol20（20 日 pct_chg 标准差）
  IV2  原始权重 ∝ 1/vol20²
  IV3  原始权重 ∝ 1/rng20（20 日 (high-low)/pre_close 均值）
  三版：当日归一，夹 [0.5/N, 2/N]，反复夹-归一至收敛。
  CTRL 每版一套：同日把该版权重在当天票之间随机打乱（20 种子）。
采纳（全部满足，最多 1 个，多个取 N3 每笔提升最大）：1) R12/N3 每笔 > BASE；2) R12/N3 PF > BASE 且 ≥ 1.3；
3) R12/N3 MDD ≥ BASE；4) R12/N3 每笔 > CTRL 且 MDD ≥ CTRL；5) R12/N3 组合日最长连亏 ≤ BASE+0.5；
6) W0/W00 MDD 不比 BASE 差 > 3 点、每笔不低 0.3pp 以上；7) 每笔 > 0、n R12 ≥ 30 / N3 ≥ 60。
"""
from __future__ import annotations
import argparse, gc, hashlib, json, os, statistics, sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.market import DailyKbar
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
VARIANTS = ("BASE", "IV1", "IV2", "IV3")
PRIMARY = ("IV1", "IV2", "IV3")
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20
LOOKBACK = 20
MIN_BARS = 15
FLOOR = 0.5
LO, HI = 0.5, 2.0


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _iso(x) -> str:
    s = _d(x)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def fetch_vol(db, need: dict) -> dict:
    """need: dt -> set(code)。返回 (dt, code) -> (vol20, rng20) 或 None。T 日（含）往前 20 根。"""
    codes = sorted({c for cs in need.values() for c in cs})
    dts = sorted(_iso(d) for d in need)
    if not codes:
        return {}
    start = (date.fromisoformat(dts[0]) - timedelta(days=70)).isoformat()
    end = dts[-1]
    series = defaultdict(list)  # code -> [(td, pct, rng)]
    for i in range(0, len(codes), 400):
        chunk = codes[i:i + 400]
        rows = db.execute(select(DailyKbar.code, DailyKbar.trade_date, DailyKbar.high, DailyKbar.low,
                                 DailyKbar.close, DailyKbar.pct_chg)
                          .where(DailyKbar.code.in_(chunk), DailyKbar.trade_date >= start, DailyKbar.trade_date <= end)
                          .order_by(DailyKbar.code, DailyKbar.trade_date)).all()
        for r in rows:
            try:
                hi, lo, cl, pc = float(r.high or 0), float(r.low or 0), float(r.close or 0), float(r.pct_chg)
            except (TypeError, ValueError):
                continue
            if cl <= 0 or hi <= 0 or lo <= 0 or pc <= -100:
                continue
            pre = cl / (1 + pc / 100.0)
            series[str(r.code)].append((str(r.trade_date)[:10], pc, (hi - lo) / pre * 100.0 if pre > 0 else None))
    import bisect
    out = {}
    for dt, cs in need.items():
        t = _iso(dt)
        for c in cs:
            s = series.get(c, [])
            j = bisect.bisect_right([x[0] for x in s], t) if s else 0
            win = s[max(0, j - LOOKBACK):j]
            if len(win) < MIN_BARS:
                out[(dt, c)] = None
                continue
            vol = statistics.stdev([x[1] for x in win])
            rg = [x[2] for x in win if x[2] is not None]
            out[(dt, c)] = (max(vol, FLOOR), max(statistics.fmean(rg), FLOOR) if rg else None)
    return out


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


def day_weights(rows: list[dict], vn: str) -> list[float]:
    n = len(rows)
    if vn == "BASE" or n == 1:
        return [1.0 / n] * n
    idx = 0 if vn in ("IV1", "IV2") else 1
    vals = [(r["vr"][idx] if r["vr"] and r["vr"][idx] else None) for r in rows]
    have = [v for v in vals if v]
    if not have:
        return [1.0 / n] * n
    med = statistics.median(have)
    vals = [v if v else med for v in vals]
    raw = [1.0 / (v * v) if vn == "IV2" else 1.0 / v for v in vals]
    return clip_norm(raw)


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


def assign(trades_by_draw: dict, names: list[str]):
    """给每个抽样的成交票写入各变体权重与 CTRL 打乱权重。"""
    for v in names:
        byday = defaultdict(list)
        for r in trades_by_draw[v]:
            byday[r["dt"]].append(r)
        for dt, g in byday.items():
            g.sort(key=lambda r: (r["sid"], r["code"]))
            for vn in VARIANTS:
                ws = day_weights(g, vn)
                for r, w in zip(g, ws):
                    r["w_" + vn] = w
                if vn == "BASE":
                    continue
                for s in range(NSEED):
                    order = sorted(range(len(g)), key=lambda i: _h(f"{s}|{vn}|{dt}|{g[i]['sid']}|{g[i]['code']}"))
                    for pos, i in enumerate(order):
                        g[i][f"c{s}_{vn}"] = ws[pos]


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
        out[vn] = o
    # 信息项：入选票 vol20 分布、权重离散度、低/高波动半区每笔
    vols = [r["vr"][0] for r in trades_by_draw["base"] if r["vr"]]
    if vols:
        qs = statistics.quantiles(vols, n=4)
        out["_vol20"] = {"n": len(vols), "missing": sum(1 for r in trades_by_draw["base"] if not r["vr"]),
                         "q25": round(qs[0], 2), "med": round(qs[1], 2), "q75": round(qs[2], 2)}
        med = qs[1]
        lo = [r["net"] for r in trades_by_draw["base"] if r["vr"] and r["vr"][0] <= med]
        hi = [r["net"] for r in trades_by_draw["base"] if r["vr"] and r["vr"][0] > med]
        out["_vol20"]["lowhalf_avg"] = round(statistics.fmean(lo), 3) if lo else None
        out["_vol20"]["highhalf_avg"] = round(statistics.fmean(hi), 3) if hi else None
    for vn in PRIMARY:
        wt = []
        for r in trades_by_draw["base"]:
            wt.append(r["w_" + vn] / r["w_BASE"])
        out[vn]["_wratio"] = {"min": round(min(wt), 2), "max": round(max(wt), 2),
                              "sd": round(statistics.pstdev(wt), 3)} if wt else None
    nday = defaultdict(int)
    for r in trades_by_draw["base"]:
        nday[r["dt"]] += 1
    out["_ndays"] = {"trade_days": len(nday), "single_trade_days": sum(1 for x in nday.values() if x == 1),
                     "avg_n": round(statistics.fmean(nday.values()), 2) if nday else 0}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/sprint4/inverse_vol_sizing.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED}}
    keep: dict = {}
    with SessionLocal() as db:
        for w in a.windows.split(","):
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
                vols = fetch_vol(db, need)
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
                        r["vr"] = vols.get(k)
                        picks[v].append(r)
                del cards, bars, sim, rows_by, vols
                gc.collect()
            days = sorted(days_all)
            assign(picks, names)
            keep[w] = {"days": days, "picks": picks}
            out = {"_days": len(days)}
            out.update(summarize(picks, days, names))
            res[w] = out
            print(w, json.dumps(out, ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            gc.collect()

    if "12m" in keep and "EXT" in keep:
        days = sorted(set(keep["12m"]["days"]) | set(keep["EXT"]["days"]))
        merged = {v: keep["12m"]["picks"][v] + keep["EXT"]["picks"][v] for v in names}
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
        verdict[vn] = {"c1_avg": c1, "c2_pf": c2, "c3_mdd": c3_, "c4_ctrl": c4, "c5_streak": c5, "c6_crash": c6,
                       "c7_exp_n": c7, "pass": all((c1, c2, c3_, c4, c5, c6, c7)),
                       "n3_lift": round(n3["avg"] - b3["avg"], 3)}
    passed = [vn for vn in PRIMARY if verdict[vn]["pass"]]
    verdict["_adopt"] = max(passed, key=lambda vn: verdict[vn]["n3_lift"]) if passed else None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

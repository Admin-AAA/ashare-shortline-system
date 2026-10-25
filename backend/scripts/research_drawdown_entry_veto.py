"""Sprint4 §253：短线 现役4技能入场「信号日距近120日最高价深度回撤」VETO。21 次选股平均，口径同 §185/§233/§249/§251。

预注册写于 docs/strategy-research.md §253（2026-10-25 10:51 CST，结果出来前写定，无网格、不调参）。
特征 = 本地 data/cache/daily_kbar.parquet 的 close/high/pct_chg（pct_chg 连乘复权；不调用 promax / 网关）。
  dd_pct = (close / max(high, 含当日近120个交易日) - 1)*100；不足120行不计否决。
  BASE 现行（含已上线 EL_C）；DD30 dd<=-30；DD40 dd<=-40；DD50 dd<=-50；命中票不成交、不递补。
  CTRL 每个变体：同一抽样、同一（策略,日）随机删同样张数（20 种子）
采纳（8 条全部满足，同 §185/§233/§249/§251，最多 1 个，多个取 N3 每笔提升最大）。
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
VARIANTS = ("BASE", "DD30", "DD40", "DD50")
PRIMARY = ("DD30", "DD40", "DD50")
LOOKBACK = 120
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _trim_mean(xs, frac=0.1):
    """mean after dropping top frac largest values (for Δ trim10)."""
    if not xs:
        return None
    ys = sorted(xs)
    k = int(len(ys) * frac)
    if k > 0:
        ys = ys[: len(ys) - k]  # drop largest Δ
    return statistics.fmean(ys) if ys else None


class Dd:
    """个股信号日收盘相对含当日近120日最高价的回撤（%）索引。"""

    def __init__(self):
        import numpy as np
        import polars as pl
        CACHE = B / "data/cache/daily_kbar.parquet"
        c = pl.col
        df = (pl.scan_parquet(CACHE).select("code", "trade_date", "close", "high", "pct_chg")
              .with_columns(c("trade_date").cast(pl.Utf8), *[c(k).cast(pl.Float64) for k in ("close", "high", "pct_chg")])
              .filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
              .collect().unique(["code", "trade_date"]).sort("code", "trade_date"))
        # 复权：pct_chg 连乘（首行=1），adj_high = high * adj / close
        df = df.with_columns(
            (1.0 + c("pct_chg").fill_null(0.0).clip(-50.0, 300.0) / 100.0).log().cum_sum().over("code").exp().alias("adj"))
        df = df.with_columns((c("high") * c("adj") / c("close")).alias("ahigh"))
        df = df.with_columns(c("ahigh").rolling_max(window_size=LOOKBACK, min_samples=LOOKBACK).over("code").alias("hh"))
        cl = df["adj"].to_numpy(); hh = df["hh"].to_numpy()
        ok = np.isfinite(cl) & np.isfinite(hh) & (cl > 0) & (hh > 0)
        val = np.where(ok, (cl / np.where(ok, hh, 1.0) - 1.0) * 100.0, np.nan)
        codes = df["code"].to_list(); tds = df["trade_date"].to_list()
        self.vw = {}
        for i in range(len(codes)):
            g = val[i]
            if not np.isfinite(g):
                continue
            self.vw[(str(codes[i]), _d(tds[i]))] = float(g)
        self.bad = int((~ok).sum())
        self.last = _d(df["trade_date"].max())

    def flag(self, vn, code, dt):
        v = self.vw.get((str(code), _d(dt)))
        if v is None:
            return False
        if vn == "DD30":
            return v <= -30.0
        if vn == "DD40":
            return v <= -40.0
        if vn == "DD50":
            return v <= -50.0
        return False


def ctrl_drop(picks, kept, vn, seed):
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


def stat(rows, ndraw):
    xs = [r["net"] for r in rows]
    if not xs:
        return {"n": 0}
    w = sum(x for x in xs if x > 0); l = -sum(x for x in xs if x < 0)
    return {"n": round(len(xs) / ndraw, 1), "wr": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1),
            "avg": round(statistics.fmean(xs), 3), "pf": round(w / l, 2) if l else None}


def info_groups(trades, R, ndraw):
    g = defaultdict(list)
    for r in trades:
        v = R.vw.get((str(r["code"]), _d(r["dt"])))
        if v is None:
            g["dd_none"].append(r)
        elif v <= -50.0:
            g["dd_le_m50"].append(r)
        elif v <= -40.0:
            g["dd_le_m40"].append(r)
        elif v <= -30.0:
            g["dd_le_m30"].append(r)
        elif v <= -15.0:
            g["dd_le_m15"].append(r)
        else:
            g["dd_gt_m15"].append(r)
    return {k: stat(v, ndraw) for k, v in sorted(g.items())} | {"all": stat(trades, ndraw)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/s253/drawdown_entry_veto.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED, "section": "s253"}}
    keep: dict = {}
    print("loading drawdown features…", flush=True)
    R = Dd()
    res["_meta"]["kbar_last"] = R.last
    res["_meta"]["dd_keys"] = len(R.vw)
    res["_meta"]["dd_bad"] = R.bad
    print("ready", "dd", len(R.vw), "bad", R.bad, "last", R.last, flush=True)

    with SessionLocal() as db:
        for w in a.windows.split(","):
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
            all_days = sorted({d for ds in days_by_sid.values() for d in ds})

            def trades(ps):
                return [r for r in ps if r["net"] is not None]

            kw = {vn: {} for vn in VARIANTS}
            ctrl = {vn: {} for vn in PRIMARY}
            removed = {vn: [] for vn in PRIMARY}
            removed_delta = {vn: [] for vn in PRIMARY}
            removed_by_sid = {vn: defaultdict(int) for vn in PRIMARY}
            per_sid = {vn: {sid: {} for sid in SIDS} for vn in VARIANTS}
            for v in names:
                P = picks[v]
                for vn in VARIANTS:
                    if vn == "BASE":
                        kept = P
                    else:
                        kept = [r for r in P if not R.flag(vn, r["code"], r["dt"])]
                    kw[vn][v] = trades(kept)
                    for sid in SIDS:
                        per_sid[vn][sid][v] = [r for r in kw[vn][v] if r["sid"] == sid]
                    if vn != "BASE":
                        for r in P:
                            if r["net"] is not None and R.flag(vn, r["code"], r["dt"]):
                                removed[vn].append(r["net"])
                                removed_delta[vn].append(-r["net"])  # Δ = benefit of skipping
                                removed_by_sid[vn][r["sid"]] += 1
                        ctrl[vn][v] = [trades(ctrl_drop(P, kept, vn, s)) for s in range(NSEED)]
            keep[w] = {"days": all_days, "days_by_sid": days_by_sid, "kw": kw, "ctrl": ctrl, "per_sid": per_sid}
            pool = [r for v in names for r in kw["BASE"][v]]
            out = {"_days": len(all_days),
                   "_info": info_groups(pool, R, len(names)),
                   "_rm_by_sid": {vn: {s: round(n / len(names), 1) for s, n in removed_by_sid[vn].items()} for vn in PRIMARY}}
            out.update(summarize(keep[w], names, removed, removed_delta))
            res[w] = out
            print(w, json.dumps({k: v for k, v in out.items() if not k.startswith("_")}, ensure_ascii=False)[:900], flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            keep[w]["pool"] = pool
            del picks
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
        # merge removed lists from 12m+EXT via res windows
        rem_n3 = {vn: [] for vn in PRIMARY}
        rem_d_n3 = {vn: [] for vn in PRIMARY}
        out = summarize(m, names, None, None)
        for vn in PRIMARY:
            n12, nE = res["12m"][vn]["rm"], res["EXT"][vn]["rm"]
            tot = n12["n"] + nE["n"]
            out[vn]["rm"] = {"n": round(tot, 1),
                            "avg": round((n12["n"] * (n12["avg"] or 0) + nE["n"] * (nE["avg"] or 0)) / tot, 3) if tot else None,
                            "d_avg": round((n12["n"] * (n12.get("d_avg") or 0) + nE["n"] * (nE.get("d_avg") or 0)) / tot, 3) if tot else None,
                            "d_trim10": None}
        out["_info"] = info_groups(a12["pool"] + ae["pool"], R, len(names))
        res["N3"] = out
        print("N3", json.dumps({k: v for k, v in out.items() if k != "_info"}, ensure_ascii=False)[:900], flush=True)

    verdict = {}
    for vn in PRIMARY:
        r12, n3, b12, b3 = res["12m"][vn], res["N3"][vn], res["12m"]["BASE"], res["N3"]["BASE"]
        c12, c3 = r12["ctrl"], n3["ctrl"]
        c1 = r12["avg"] > b12["avg"] and n3["avg"] > b3["avg"]
        c2 = r12["pf"] >= max(1.3, b12["pf"]) and n3["pf"] >= max(1.3, b3["pf"])
        c3_ = r12["mdd"] >= b12["mdd"] and n3["mdd"] >= b3["mdd"]
        # c4: beat CTRL + Δ mean > 0 + trim10 > 0
        d12 = r12["rm"].get("d_avg"); d3 = n3["rm"].get("d_avg")
        t12 = r12["rm"].get("d_trim10"); t3 = n3["rm"].get("d_trim10")
        # N3 trim may be None (merged); require window-level trim for 12m, and d_avg for both
        c4 = (r12["avg"] > c12["avg"] and n3["avg"] > c3["avg"]
              and d12 is not None and d12 > 0 and d3 is not None and d3 > 0
              and t12 is not None and t12 > 0
              and (t3 is None or t3 > 0))
        c5 = all(res[w][vn]["mdd"] >= res[w]["BASE"]["mdd"] - 3.0 and res[w][vn]["avg"] >= res[w]["BASE"]["avg"] - 0.3
                 for w in ("W0", "W00") if w in res)
        rm12, rm3 = r12["rm"], n3["rm"]
        testable = rm12["n"] >= 10
        c6 = testable and rm3["n"] >= 20
        c7 = r12["streak"] <= b12["streak"] + 1 and n3["streak"] <= b3["streak"] + 1
        c8 = r12["n"] >= 30 and n3["n"] >= 60 and r12["total"] > 0 and n3["total"] > 0
        verdict[vn] = {"c1_avg": c1, "c2_pf": c2, "c3_mdd": c3_, "c4_ctrl_delta": c4, "c5_crash": c5,
                       "c6_aff": c6, "testable_r12_ge10": testable, "c7_streak": c7, "c8_n_total": c8,
                       "pass": all((c1, c2, c3_, c4, c5, c6, c7, c8)), "n3_lift": round(n3["avg"] - b3["avg"], 3),
                       "rm12_n": rm12["n"], "rm3_n": rm3["n"], "d12": d12, "d3": d3, "t12": t12}
    passed = [vn for vn in PRIMARY if verdict[vn]["pass"]]
    verdict["_adopt"] = max(passed, key=lambda vn: verdict[vn]["n3_lift"]) if passed else None
    # BASE echo for EL_C check
    verdict["_base_r12"] = {k: res["12m"]["BASE"].get(k) for k in ("n", "avg", "pf", "mdd", "streak", "total")}
    verdict["_base_n3"] = {k: res["N3"]["BASE"].get(k) for k in ("n", "avg", "pf", "mdd", "streak", "total")}
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


def summarize(k, names, removed, removed_delta):
    days = k["days"]
    out = {}
    for vn in VARIANTS:
        o = _mean({v: _m(k["kw"][vn][v], days) for v in names}, names)
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
            o["ctrl"]["beat_share"] = round(statistics.mean(1.0 if o["avg"] > x else 0.0 for x in avgs), 3)
            if removed is not None:
                rr = removed[vn]
                dd = removed_delta[vn]
                o["rm"] = {"n": round(len(rr) / len(names), 1),
                           "avg": round(statistics.mean(rr), 3) if rr else None,
                           "d_avg": round(statistics.mean(dd), 3) if dd else None,
                           "d_trim10": round(_trim_mean(dd), 3) if dd else None}
        out[vn] = o
    return out


if __name__ == "__main__":
    main()

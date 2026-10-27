"""Sprint4 §249：短线 现役4技能入场「开盘相对昨收涨幅过大」VETO。21 次选股平均，口径同 §136/§185/§233。

预注册写于 docs/strategy-research.md §249（2026-10-25 10:03 CST，结果出来前写定，无网格、不调参）。
特征 = 本地 data/cache/daily_kbar.parquet 的 open/close（昨收=同代码前一交易日 close；不调用 promax / 网关）。
  gap_pct = (open/prev_close - 1)*100 仅当 prev_close>0 且 open>0；否则不计否决。
  BASE 现行（含已上线 EL_C）；OG2 gap_pct>=2.0；OG3 >=3.0；OG5 >=5.0；命中票不成交、不递补。
  CTRL 每个变体：同一抽样、同一（策略,日）随机删同样张数（20 种子）
采纳（8 条全部满足，同 §185/§233，最多 1 个，多个取 N3 每笔提升最大）：
1) 每笔 R12/N3 > BASE；2) PF R12/N3 ≥ BASE 且 ≥ 1.3；3) MDD R12/N3 ≥ BASE；
4) R12/N3 每笔 > CTRL，且被否单 Δ 均值 R12/N3 > 0、trim10 后仍 > 0；
5) W0/W00 MDD 不比 BASE 差 > 3 点、每笔不低 0.3pp 以上；
6) 被否单 R12 ≥ 10（否则不可检验）、N3 ≥ 20；
7) 最长连亏 R12/N3 ≤ BASE + 1；8) 总 R12/N3 > 0、n R12 ≥ 30 / N3 ≥ 60。
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
VARIANTS = ("BASE", "OG2", "OG3", "OG5")
PRIMARY = ("OG2", "OG3", "OG5")
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


class OpenGap:
    """个股信号日开盘相对昨收涨幅（%）索引。"""
    TH = {"OG2": 2.0, "OG3": 3.0, "OG5": 5.0}

    def __init__(self):
        import numpy as np
        import polars as pl
        CACHE = B / "data/cache/daily_kbar.parquet"
        c = pl.col
        df = (pl.scan_parquet(CACHE).select("code", "trade_date", "open", "close")
              .with_columns(
                  c("trade_date").cast(pl.Utf8),
                  *[c(k).cast(pl.Float64) for k in ("open", "close")],
              )
              .filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & (c("trade_date") >= "2022-01-04"))
              .collect().unique(["code", "trade_date"]).sort("code", "trade_date"))
        df = df.with_columns(c("close").shift(1).over("code").alias("prev_close"))
        o = df["open"].to_numpy(); pc = df["prev_close"].to_numpy()
        ok = np.isfinite(o) & np.isfinite(pc) & (pc > 0) & (o > 0)
        gap = np.where(ok, (o / pc - 1.0) * 100.0, np.nan)
        codes = df["code"].to_list(); tds = df["trade_date"].to_list()
        self.gap = {}
        for i in range(len(codes)):
            g = gap[i]
            if g is None or (isinstance(g, float) and (np.isnan(g) or g != g)):
                continue
            self.gap[(str(codes[i]), _d(tds[i]))] = float(g)
        self.last = _d(df["trade_date"].max())
        self.ratio = self.gap  # alias so info_groups can still key if needed

    def flag(self, vn, code, dt):
        thr = self.TH.get(vn)
        if thr is None:
            return False
        v = self.gap.get((str(code), _d(dt)))
        return v is not None and v >= thr




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
        k = (str(r["code"]), _d(r["dt"]))
        v = R.gap.get(k)
        if v is None:
            g["gap_none"].append(r)
        elif v >= 5.0:
            g["gap_ge5"].append(r)
        elif v >= 3.0:
            g["gap_ge3"].append(r)
        elif v >= 2.0:
            g["gap_ge2"].append(r)
        else:
            g["gap_lt2"].append(r)
    return {k: stat(v, ndraw) for k, v in sorted(g.items())} | {"all": stat(trades, ndraw)}



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/s249/open_gap_entry_veto.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED, "section": "s249"}}
    keep: dict = {}
    print("loading open-gap features…", flush=True)
    R = OpenGap()
    res["_meta"]["kbar_last"] = R.last
    res["_meta"]["gap_keys"] = len(R.gap)
    print("ready", "gap", len(R.gap), "last", R.last, flush=True)

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

"""Sprint4 §134：短线 市场"赚钱效应"闸门（昨日涨停溢价为负 / 炸板率高 的信号日 4 策略不出票）。21 次选股平均，口径同 §127/§129/§130。

预注册写于 /workspace/strategy-research.md §134（2026-10-19 13:52 CST，结果出来前写定，无网格、不调参）。
情绪数据 = 本地 data/cache/daily_kbar.parquet（2022-01-04 起全市场；不调用 promax / 网关）。
  股票：00/30/60/68 开头（剔北交所），上一行须为市场上一交易日；lim = 20%（30x/688/689）否则 10%；
  涨停价 up = floor(prev_close×(1+lim)×100+0.5)/100；涨停收盘 close ≥ up−0.001 且 pct ≤ lim×100+0.5；炸板 high ≥ up−0.001 且 close < up−0.001。
  P(T)  = T−1 日涨停收盘、T 日有K 的股票 T 日收益 close_T/close_{T−1}−1 等权均值（无样本 → 不命中）
  ZB(T) = 炸板 / (炸板 + 涨停收盘)（分母 0 → 不命中）
  BASE 现行；G1 P(T)<0；G2 P(T)<0 且 P(T−1)<0；G3 ZB(T) ≥ 0.40；命中日 4 策略当天入选票全部不做。
  CTRL 每个变体一套：同一抽样、同一策略，从未命中的信号日里按 md5 整日随机删同样张数（20 种子）
采纳（7 条全部满足，最多 1 个，多个取 N3 每笔提升最大）：1) 每笔 R12/N3 > BASE；2) PF R12/N3 ≥ BASE 且 ≥ 1.3；
3) MDD R12/N3 ≥ BASE；4) R12/N3 每笔 > 对照均值且 MDD 不差于对照均值；5) W0/W00 MDD 不比 BASE 差 > 3 点、每笔不低 0.3pp 以上；
6) 被删成交 R12 ≥ 10（否则不可检验 → 否决）、N3 ≥ 20，被删单每笔 R12/N3 ≤ 0，且过滤后总收益 R12/N3 ≥ BASE 总收益；
7) n R12 ≥ 30、N3 ≥ 60，总 > 0。
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
VARIANTS = ("BASE", "G1", "G2", "G3")
PRIMARY = ("G1", "G2", "G3")
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


class Senti:
    """P(T) 昨日涨停溢价 / ZB(T) 炸板率，键 = YYYYMMDD。"""
    def __init__(self):
        import polars as pl
        CACHE = B / "data/cache/daily_kbar.parquet"
        c = pl.col
        df = (pl.scan_parquet(CACHE).select("code", "trade_date", "high", "close", "pct_chg")
              .with_columns(c("trade_date").cast(pl.Utf8))
              .filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & (c("trade_date") >= "2022-01-04"))
              .collect().unique(["code", "trade_date"]).sort("code", "trade_date"))
        td = df.select("trade_date").unique().sort("trade_date").with_columns(
            c("trade_date").shift(1).alias("mkt_prv"), c("trade_date").shift(-1).alias("mkt_nxt"))
        df = df.join(td, on="trade_date", how="left").sort("code", "trade_date")
        df = df.with_columns(c("close").shift(1).over("code").alias("pc"), c("trade_date").shift(1).over("code").alias("ptd"),
                             c("close").shift(-1).over("code").alias("nc"), c("trade_date").shift(-1).over("code").alias("ntd"))
        lim = pl.when(c("code").str.starts_with("30") | c("code").str.starts_with("688") | c("code").str.starts_with("689")).then(0.2).otherwise(0.1)
        up = ((c("pc") * (1 + lim) * 100 + 0.5).floor() / 100)
        ok = (c("ptd") == c("mkt_prv")) & c("pc").is_not_null() & (c("pct_chg") <= lim * 100 + 0.5)
        df = df.with_columns((ok & (c("close") >= up - 0.001)).fill_null(False).alias("lu"),
                             (ok & (c("high") >= up - 0.001) & (c("close") < up - 0.001)).fill_null(False).alias("zb"))
        zb = df.group_by("trade_date").agg(c("lu").sum().alias("nlu"), c("zb").sum().alias("nzb"))
        prem = (df.filter(c("lu") & (c("ntd") == c("mkt_nxt")) & c("nc").is_not_null())
                .group_by("mkt_nxt").agg((c("nc") / c("close") - 1).mean().alias("p"), pl.len().alias("np")))
        self.cal = td["trade_date"].to_list()
        self.prv = {_d(r[0]): _d(r[1]) for r in td.select("trade_date", "mkt_prv").iter_rows() if r[1]}
        self.P = {_d(d): float(p) for d, p, _ in prem.iter_rows()}
        self.NP = {_d(d): int(n) for d, _, n in prem.iter_rows()}
        self.ZB = {_d(d): (nz / (nz + nl)) for d, nl, nz in zb.iter_rows() if (nz + nl) > 0}
        self.NLU = {_d(d): int(nl) for d, nl, _ in zb.iter_rows()}
        self.last = _d(self.cal[-1])

    def flag(self, vn, dt):
        t = _d(dt)
        p = self.P.get(t)
        if vn == "G1":
            return p is not None and p < 0
        if vn == "G2":
            q = self.P.get(self.prv.get(t, ""))
            return p is not None and q is not None and p < 0 and q < 0
        if vn == "G3":
            z = self.ZB.get(t)
            return z is not None and z >= 0.40
        return False


def ctrl_drop(picks, flagged_days, kept, vn, seed):
    """同策略，从未命中的信号日里整日随机删同样张数（最后一组按 code hash 删差额）。同 §130。"""
    k_by = defaultdict(int)
    nk = defaultdict(int)
    for p in kept:
        nk[p["sid"]] += 1
    by_sid_day = defaultdict(lambda: defaultdict(list))
    for p in picks:
        by_sid_day[p["sid"]][p["dt"]].append(p)
    out = []
    for sid, days in by_sid_day.items():
        total = sum(len(g) for g in days.values())
        k = total - nk.get(sid, 0)
        k_by[sid] = k
        cand = sorted((d for d in days if d not in flagged_days), key=lambda d: _h(f"{seed}|{vn}|{sid}|{d}"))
        drop_days, partial, acc = set(), None, 0
        for d in cand:
            if acc >= k:
                break
            n = len(days[d])
            if acc + n <= k:
                drop_days.add(d); acc += n
            else:
                partial = (d, k - acc); acc = k
        for d, g in days.items():
            if d in drop_days:
                continue
            if partial and d == partial[0]:
                g = sorted(g, key=lambda p: _h(f"{seed}|{vn}|{sid}|{d}|{p['code']}"))
                out.extend(g[partial[1]:])
            else:
                out.extend(g)
    return out


def stat(rows, ndraw):
    xs = [r["net"] for r in rows]
    if not xs:
        return {"n": 0}
    w = sum(x for x in xs if x > 0); l = -sum(x for x in xs if x < 0)
    return {"n": round(len(xs) / ndraw, 1), "wr": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1),
            "avg": round(statistics.fmean(xs), 3), "pf": round(w / l, 2) if l else None}


def info_groups(trades, S: "Senti", ndraw):
    g = defaultdict(list)
    for r in trades:
        t = _d(r["dt"]); p = S.P.get(t); z = S.ZB.get(t)
        g["P_none" if p is None else ("P_neg" if p < 0 else "P_pos")].append(r)
        g["ZB_none" if z is None else ("ZB_ge40" if z >= 0.40 else "ZB_lt40")].append(r)
    return {k: stat(v, ndraw) for k, v in sorted(g.items())} | {"all": stat(trades, ndraw)}


def dist(S: "Senti", days):
    import statistics as st
    out = {}
    for nm, D in (("P", S.P), ("ZB", S.ZB)):
        xs = [D[_d(d)] for d in days if _d(d) in D]
        out[nm] = {"n_days": len(days), "n_val": len(xs)} | ({"q10_50_90": [round(q, 4) for q in (st.quantiles(xs, n=10)[0], st.median(xs), st.quantiles(xs, n=10)[-1])]} if len(xs) > 10 else {})
    miss = [d for d in days if _d(d) not in S.P]
    out["P_missing_days"] = miss[:10]
    out["np_med"] = st.median([S.NP[_d(d)] for d in days if _d(d) in S.NP]) if any(_d(d) in S.NP for d in days) else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/sprint4/sentiment_gate.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED}}
    keep: dict = {}
    from sqlalchemy import text
    with SessionLocal() as db:
        C = Senti()
        res["_meta"]["senti_last_day"] = C.last

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
            flagged = {vn: {d for d in all_days if C.flag(vn, d)} for vn in PRIMARY}

            def trades(ps):
                return [r for r in ps if r["net"] is not None]

            kw = {vn: {} for vn in VARIANTS}
            ctrl = {vn: {} for vn in PRIMARY}
            removed = {vn: [] for vn in PRIMARY}
            removed_by_sid = {vn: defaultdict(int) for vn in PRIMARY}
            per_sid = {vn: {sid: {} for sid in SIDS} for vn in VARIANTS}
            for v in names:
                P = picks[v]
                for vn in VARIANTS:
                    kept = P if vn == "BASE" else [r for r in P if r["dt"] not in flagged[vn]]
                    kw[vn][v] = trades(kept)
                    for sid in SIDS:
                        per_sid[vn][sid][v] = [r for r in kw[vn][v] if r["sid"] == sid]
                    if vn != "BASE":
                        for r in P:
                            if r["net"] is not None and r["dt"] in flagged[vn]:
                                removed[vn].append(r["net"]); removed_by_sid[vn][r["sid"]] += 1
                        ctrl[vn][v] = [trades(ctrl_drop(P, flagged[vn], kept, vn, s)) for s in range(NSEED)]
            keep[w] = {"days": all_days, "days_by_sid": days_by_sid, "kw": kw, "ctrl": ctrl, "per_sid": per_sid}
            pool = [r for v in names for r in kw["BASE"][v]]
            out = {"_days": len(all_days),
                   "_flag_days": {vn: sorted(flagged[vn]) for vn in PRIMARY},
                   "_info": info_groups(pool, C, len(names)), "_dist": dist(C, all_days),
                   "_rm_by_sid": {vn: {s: round(n / len(names), 1) for s, n in removed_by_sid[vn].items()} for vn in PRIMARY}}
            out.update(summarize(keep[w], names, removed))
            res[w] = out
            print(w, json.dumps({k: v for k, v in out.items() if k != "_flag_days"}, ensure_ascii=False), flush=True)
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
        out = summarize(m, names, None)
        for vn in PRIMARY:
            n12, nE = res["12m"][vn]["rm"], res["EXT"][vn]["rm"]
            tot = n12["n"] + nE["n"]
            out[vn]["rm"] = {"n": round(tot, 1), "avg": round((n12["n"] * (n12["avg"] or 0) + nE["n"] * (nE["avg"] or 0)) / tot, 3)
                             if tot else None}
        out["_info"] = info_groups(a12["pool"] + ae["pool"], C, len(names))
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
        rm12, rm3 = r12["rm"], n3["rm"]
        testable = rm12["n"] >= 10
        c6 = (testable and rm3["n"] >= 20 and rm12["avg"] is not None and rm3["avg"] is not None
              and rm12["avg"] <= 0 and rm3["avg"] <= 0 and r12["total"] >= b12["total"] and n3["total"] >= b3["total"])
        c7 = r12["n"] >= 30 and n3["n"] >= 60 and r12["total"] > 0 and n3["total"] > 0
        verdict[vn] = {"c1_avg": c1, "c2_pf": c2, "c3_mdd": c3_, "c4_ctrl": c4, "c5_crash": c5, "c6_removed": c6,
                       "testable_r12_ge10": testable, "c7_n_total": c7,
                       "pass": all((c1, c2, c3_, c4, c5, c6, c7)), "n3_lift": round(n3["avg"] - b3["avg"], 3)}
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
                o["rm"] = {"n": round(len(rr) / len(names), 1), "avg": round(statistics.mean(rr), 3) if rr else None}
        out[vn] = o
    return out


if __name__ == "__main__":
    main()

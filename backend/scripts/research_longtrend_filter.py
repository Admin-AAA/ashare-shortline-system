"""Sprint4 §146：短线 个股长期弱势（跌破年线 / 距 52 周高点回撤>=40% / 处于 52 周区间底部 20%）入选票回避过滤。
21 次选股平均，口径同 §127/§130/§134/§136/§138/§140/§142/§144。

预注册写于 /workspace/strategy-research.md §146（2026-10-19 16:54 CST，结果出来前写定，无网格、不调参）。
特征 = 本地 data/midterm/daily_hist_2020/2021 + data/cache/daily_kbar.parquet（拼接点同 lt_data.prices）的 pct_chg
（不调用 promax / 网关）：ac = 逐股 prod(1+clip(pct_chg,±35%)/100)；最近 250 行（含 T，不足 250 行 -> 空）：
  MA250 = mean(ac)，H250/L250 = max/min(ac)。
  BASE 现行；B250 ac<MA250；DD40 ac/H250-1<=-0.40；POS20 (ac-L250)/(H250-L250)<0.20；命中票不成交、不递补。
  CTRL 每个变体：同一抽样、同一（策略,日）随机删同样张数（20 种子）
采纳（7 条全部满足，最多 1 个，多个取 N3 每笔提升最大）：同 §136/§138/§140/§142/§144（c6 被删单 R12/N3 每笔 <= 0 且总收益不降）。
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
VARIANTS = ("BASE", "B250", "DD40", "POS20")
PRIMARY = ("B250", "DD40", "POS20")
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


RB = ("r_lt085", "r_085_1", "r_1_115", "r_ge115", "r_na")
DB = ("dd_gt-20", "dd_-20_-40", "dd_le-40", "dd_na")
PB = ("pos_lt02", "pos_02_05", "pos_05_08", "pos_ge08", "pos_na")


class Feat:
    """(code+YYYYMMDD) -> 编码 rb*20 + db*5 + pb（分档见 RB/DB/PB）。缺失 -> 全空 -> 不命中。"""
    def __init__(self):
        import polars as pl
        c = pl.col
        CACHE = B / "data/cache/daily_kbar.parquet"
        D = B / "data/midterm"
        ymd = lambda e: e.cast(pl.Utf8).str.replace_all("-", "").str.slice(0, 8)
        new = (pl.scan_parquet(CACHE).select(c("code").cast(pl.Utf8), ymd(c("trade_date")).alias("td"), c("pct_chg").cast(pl.Float64))
               .collect())
        cnt = new.group_by("td").len()
        cut = cnt.filter(c("len") >= 2000)["td"].min() or "20220104"
        new = new.filter(c("td") >= cut)
        h = pl.concat([pl.read_parquet(D / f"daily_hist_{y}.parquet", columns=["ts_code", "trade_date", "pct_chg"]) for y in (2020, 2021)],
                      how="diagonal_relaxed")
        h = h.select(c("ts_code").str.slice(0, 6).alias("code"), ymd(c("trade_date")).alias("td"), c("pct_chg").cast(pl.Float64)).filter(c("td") < cut)
        px = (pl.concat([h, new]).filter(~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
              .unique(["code", "td"]).sort("code", "td"))
        del h, new
        px = px.with_columns((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code").alias("ac"))
        W = 250
        px = px.with_columns(c("ac").rolling_mean(W, min_samples=W).over("code").alias("ma"),
                             c("ac").rolling_max(W, min_samples=W).over("code").alias("hi"),
                             c("ac").rolling_min(W, min_samples=W).over("code").alias("lo"))
        px = px.filter((c("td") >= "20210601") & c("ma").is_not_null())
        ratio = c("ac") / c("ma"); dd = c("ac") / c("hi") - 1
        pos = pl.when(c("hi") > c("lo")).then((c("ac") - c("lo")) / (c("hi") - c("lo")))
        rb = pl.when(ratio < 0.85).then(0).when(ratio < 1.0).then(1).when(ratio < 1.15).then(2).otherwise(3)
        db = pl.when(dd > -0.20).then(0).when(dd > -0.40).then(1).otherwise(2)
        p = pos
        pb = pl.when(p.is_null()).then(4).when(p < 0.2).then(0).when(p < 0.5).then(1).when(p < 0.8).then(2).otherwise(3)
        px = px.with_columns((rb * 20 + db * 5 + pb).cast(pl.Int16).alias("enc"), (c("code") + c("td")).alias("k"))
        self.cut = cut
        self.last = str(px["td"].max()); self.basic_last = "-"
        self.n = px.height
        self.enc = dict(zip(px["k"].to_list(), px["enc"].to_list()))
        del px

    def get(self, code, dt):
        e = self.enc.get(str(code) + _d(dt))
        if e is None:
            return 4, 3, 4
        return e // 20, (e % 20) // 5, e % 5

    def flag(self, vn, code, dt):
        rb, db, pb = self.get(code, dt)
        if vn == "B250":
            return rb in (0, 1)
        if vn == "DD40":
            return db == 2
        if vn == "POS20":
            return pb == 0
        return False


def ctrl_drop(picks, kept, vn, seed):
    """同（策略, 日）删同样张数，随机删。同 §127。"""
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


def info_groups(trades, R: "Feat", ndraw):
    g = defaultdict(list)
    for r in trades:
        if r.get("net") is None:
            continue
        rb, db, pb = R.get(r["code"], r["dt"])
        x = float(r["net"])
        g[RB[rb]].append(x); g[DB[db]].append(x); g[PB[pb]].append(x)
    out = {}
    for k, xs in g.items():
        if not xs:
            out[k] = {"n": 0}; continue
        w = sum(x for x in xs if x > 0); l = -sum(x for x in xs if x < 0)
        out[k] = {"n": round(len(xs) / ndraw, 1), "wr": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1),
                  "avg": round(statistics.fmean(xs), 3), "pf": round(w / l, 2) if l else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/sprint4/longtrend_filter.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED}}
    keep: dict = {}
    print("loading long-trend feats…", flush=True)
    R = Feat()
    res["_meta"]["kbar_last"] = R.last
    res["_meta"]["basic_last"] = R.basic_last
    print("feat ready", "keys", len(R.enc), "cut", R.cut, "last", R.last, flush=True)
    res["_meta"]["feat_keys"] = len(R.enc); res["_meta"]["cut"] = R.cut

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
                                removed[vn].append(r["net"]); removed_by_sid[vn][r["sid"]] += 1
                        ctrl[vn][v] = [trades(ctrl_drop(P, kept, vn, s)) for s in range(NSEED)]
            keep[w] = {"days": all_days, "days_by_sid": days_by_sid, "kw": kw, "ctrl": ctrl, "per_sid": per_sid}
            pool = [r for v in names for r in kw["BASE"][v]]
            out = {"_days": len(all_days),
                   "_info": info_groups(pool, R, len(names)),
                   "_rm_by_sid": {vn: {s: round(n / len(names), 1) for s, n in removed_by_sid[vn].items()} for vn in PRIMARY}}
            out.update(summarize(keep[w], names, removed))
            res[w] = out
            print(w, json.dumps({k: v for k, v in out.items() if not k.startswith("_")}, ensure_ascii=False)[:800], flush=True)
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
        out["_info"] = info_groups(a12["pool"] + ae["pool"], R, len(names))
        res["N3"] = out
        print("N3", json.dumps({k: v for k, v in out.items() if k != "_info"}, ensure_ascii=False)[:800], flush=True)

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

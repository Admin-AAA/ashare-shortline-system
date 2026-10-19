"""Sprint4 §130：短线 长假前入场 / 计划持有期跨长假 过滤。21 次选股平均，口径同 §127 / §129。

预注册写于 /workspace/strategy-research.md §130（2026-10-19 12:50 CST，结果出来前写定，无网格、不调参）。
日历 = daily_kbars 000001 交易日；gap(j) = cal[j]→cal[j+1] 之间的工作日数；≥3 长假，≥1 任意节假日。
信号日 T = cal[i]，计划窗口 j ∈ [i, i+4]（T+1 入场、持有 5 根 bar）。命中日 4 个策略当天入选票全部不做。
  BASE 现行
  H1   gap(i) ≥ 3 或 gap(i+1) ≥ 3（长假前最后 2 个交易日的信号）
  H2   任意 j ∈ [i, i+4] gap(j) ≥ 3（计划持有期跨长假）
  H3   任意 j ∈ [i, i+4] gap(j) ≥ 1（计划持有期跨任何节假日）
  CTRL 每个变体一套：同一抽样、同一策略，从未命中的信号日里按 md5 整日随机删同样张数（20 种子）
采纳（7 条全部满足，最多 1 个，多个取 N3 每笔提升最大）：1) 每笔 R12/N3 > BASE；2) PF R12/N3 ≥ BASE 且 ≥ 1.3；
3) MDD R12/N3 ≥ BASE；4) R12/N3 每笔 > 对照均值且 MDD 不差于对照均值；5) W0/W00 MDD 不比 BASE 差 > 3 点、每笔不低 0.3pp 以上；
6) 被删成交 R12 ≥ 10（否则不可检验 → 否决）、N3 ≥ 20，且被删单每笔 R12/N3 < BASE 每笔；7) n R12 ≥ 30、N3 ≥ 60，总 > 0。
"""
from __future__ import annotations
import argparse, bisect, datetime as _dt, gc, hashlib, json, os, statistics, sys
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
VARIANTS = ("BASE", "H1", "H2", "H3")
PRIMARY = ("H1", "H2", "H3")
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20
HOLD = 5  # 4 个策略 apply_exit_overrides 后 horizon_days 全部 = 5


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _h(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


class Cal:
    def __init__(self, dates):
        self.cal = sorted(dates)
        self.tdi = {d: i for i, d in enumerate(self.cal)}
        self.gap = []
        for a, b in zip(self.cal, self.cal[1:]):
            A = _dt.date(int(a[:4]), int(a[4:6]), int(a[6:]))
            Bd = _dt.date(int(b[:4]), int(b[4:6]), int(b[6:]))
            self.gap.append(sum(1 for k in range(1, (Bd - A).days) if (A + _dt.timedelta(k)).weekday() < 5))

    def idx(self, d):
        t = _d(d)
        return self.tdi.get(t, bisect.bisect_right(self.cal, t) - 1)

    def g(self, j):
        return self.gap[j] if 0 <= j < len(self.gap) else 0

    def flag(self, vn, dt):
        i = self.idx(dt)
        if vn == "H1":
            return self.g(i) >= 3 or self.g(i + 1) >= 3
        if vn == "H2":
            return any(self.g(j) >= 3 for j in range(i, i + HOLD))
        if vn == "H3":
            return any(self.g(j) >= 1 for j in range(i, i + HOLD))
        return False


def ctrl_drop(picks, flagged_days, kept, vn, seed):
    """同策略，从未命中的信号日里整日随机删同样张数（最后一组按 code hash 删差额）。"""
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


def info_groups(trades, C: Cal, ndraw):
    g = defaultdict(list)
    for r in trades:
        i, ie, ix = C.idx(r["dt"]), C.idx(r["en"]), C.idx(r["ex"])
        held = [C.g(j) for j in range(ie, ix)]
        if any(x >= 3 for x in held):
            g["held_cross_long"].append(r)
        elif any(x >= 1 for x in held):
            g["held_cross_short_only"].append(r)
        else:
            g["held_no_holiday"].append(r)
        ge = C.g(ie)
        if ge >= 3:
            g["entry_eve_long"].append(r)
        elif ge >= 1:
            g["entry_eve_short"].append(r)
        pre = [C.g(j) for j in range(i, ie)]
        if any(x >= 3 for x in pre):
            g["signal_entry_gap_long"].append(r)
        elif any(x >= 1 for x in pre):
            g["signal_entry_gap_short"].append(r)
    return {k: stat(v, ndraw) for k, v in sorted(g.items())} | {"all": stat(trades, ndraw)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/sprint4/holiday_filter.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "nseed": NSEED, "hold": HOLD}}
    keep: dict = {}
    from sqlalchemy import text
    with SessionLocal() as db:
        C = Cal({_d(r[0]) for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
        res["_meta"]["holidays"] = [(C.cal[j], C.cal[j + 1], x) for j, x in enumerate(C.gap) if x and C.cal[j] >= "20220101"]

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
            # 信息项（出结果后补的稳健性拆分，不进判定）：被删成交按"命中的第一个节假日"分组
            by_hol = {}
            for vn in PRIMARY:
                g = defaultdict(list)
                for r in pool:
                    if r["dt"] not in flagged[vn]:
                        continue
                    i = C.idx(r["dt"])
                    js = [j for j in range(i, i + HOLD) if C.g(j) >= (1 if vn == "H3" else 3)]
                    j = js[0]
                    g[f"{C.cal[j]}(gap{C.g(j)})"].append(r)
                by_hol[vn] = {k: stat(v, len(names)) for k, v in sorted(g.items())}
            out = {"_days": len(all_days),
                   "_flag_days": {vn: sorted(flagged[vn]) for vn in PRIMARY},
                   "_info": info_groups(pool, C, len(names)), "_rm_by_holiday": by_hol,
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
              and rm12["avg"] < b12["avg"] and rm3["avg"] < b3["avg"])
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

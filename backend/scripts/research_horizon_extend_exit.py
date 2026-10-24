"""Sprint4 §210：短线 浮盈后延期出场（horizon 到期且曾浮盈≥X%）。21 次选股平均，口径同 §185/§189。

预注册写于 docs/strategy-research.md §210（2026-10-25 06:05 CST，结果出来前写定，无网格、不调参）。
BASE = 当前含 EL_C 的真实执行核（_simulate_card 已读 strategy.yaml entry_low_exit）。
只改 exit_reason 前缀为 horizon_close 的已成交单：峰高=入场到原离场最高价；浮盈%=峰高/入场价−1；
若浮盈≥G%，把离场改到原离场日之后第 N 个可卖日收盘（封跌停顺延下一可卖开盘）。
  EXT1_F3：G=3%，N=1
  EXT1_F5：G=5%，N=1
  EXT2_F5：G=5%，N=2
新净 = (新原始价 * k / 入场成交价 - 1) * 100 - fee；fee = 原 gross - 原 net。
对照 placebo：同抽样从「可延期」成交中按 md5 随机取同样笔数，每单随机 1 个合规延期日，20 种子平均。
采纳 8 条同 §185。只读本地 solo 面板 + MySQL 日K（SELECT），不调 promax / tushare / 网关，不写库。
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
VARIANTS = ("BASE", "EXT1_F3", "EXT1_F5", "EXT2_F5")
PRIMARY = ("EXT1_F3", "EXT1_F5", "EXT2_F5")
# flag key in sim result, gain pct, extend N sellable days
SPEC = {"EXT1_F3": ("e1f3", 3.0, 1), "EXT1_F5": ("e1f5", 5.0, 1), "EXT2_F5": ("e2f5", 5.0, 2)}
ORDER = ("EXT", "12m", "W0", "W00")
NSEED = 20
STATS = {"k_mismatch": 0, "k_checked": 0, "no_en_bar": 0, "push_next_open": 0, "push_no_bar": 0,
         "no_ext_bars": 0, "e1f3_hits": 0, "e1f5_hits": 0, "e2f5_hits": 0, "skip_non_hz": 0}


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _band(code: str) -> float:
    return 0.198 if code.startswith(("300", "301", "688")) else 0.098


def _next_sellable(bars, j0):
    for j in range(j0, len(bars)):
        if bars[j].open > 0 and not _unsellable(bars[j], bars[j - 1] if j > 0 else None):
            return j
    return None


def _nth_sellable_after(bars, j_after, n):
    """第 n 个可卖日，下标 > j_after（不含 j_after 当日）。"""
    found = 0
    for j in range(j_after + 1, len(bars)):
        prev = bars[j - 1] if j > 0 else None
        if bars[j].open > 0 and not _unsellable(bars[j], prev):
            found += 1
            if found == n:
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
    if why in ("stop_loss_gap", "trailing_stop_gap") and jx > 0:
        raw_old = max(bars[jx].open, E * 0.85) if why == "stop_loss_gap" else bars[jx].open
        STATS["k_checked"] += 1
        if raw_old > 0 and abs(X / raw_old - kx) > 1e-6:
            STATS["k_mismatch"] += 1
    newnet = lambda raw: round((raw * kx / E - 1) * 100 - fee, 10)

    def close_exit_at(j):
        if j <= je or j >= len(bars):
            return None
        bx, bp = bars[j], bars[j - 1] if j > 0 else None
        locked = False
        if bp is not None:
            locked = bx.close <= bp.close * (1 - _band(card["code"])) + 0.005 and bx.close <= bx.low + 0.005
        if not locked:
            return (newnet(bx.close), ds[j])
        js = _next_sellable(bars, j + 1)
        if js is None:
            STATS["push_no_bar"] += 1
            return None
        STATS["push_next_open"] += 1
        return (newnet(bars[js].open), ds[js])

    # peak float over [je, jx]
    peak = 0.0
    for j in range(je, jx + 1):
        peak = max(peak, float(bars[j].high or 0))
    flt = (peak / E - 1.0) * 100.0 if E > 0 and peak > 0 else -999.0
    out["float"] = round(flt, 4)
    out["E"] = E

    is_hz = why.startswith("horizon_close")
    if not is_hz:
        STATS["skip_non_hz"] += 1
        out["e1f3"] = out["e1f5"] = out["e2f5"] = []
        return out

    def build(g, n, flag):
        days = []
        if flt < g:
            return days
        jn = _nth_sellable_after(bars, jx, n)
        if jn is None:
            STATS["no_ext_bars"] += 1
            return days
        res = close_exit_at(jn)
        if res is not None:
            STATS[f"{flag}_hits"] += 1
            days.append((ds[jn], res))
        return days

    out["e1f3"] = build(3.0, 1, "e1f3")
    out["e1f5"] = build(5.0, 1, "e1f5")
    out["e2f5"] = build(5.0, 2, "e2f5")
    return out


def variant_change(vn, s):
    """返回 (new_net, new_ex) 或 None（不改）。"""
    if s is None:
        return None
    flag, _g, _n = SPEC[vn]
    days = s.get(flag) or []
    for d, res in days:
        if res is not None:
            return res  # (net, ex)
    return None


def _h(*a) -> int:
    return int(hashlib.md5("|".join(map(str, a)).encode()).hexdigest()[:12], 16)


def placebo_rows(P, S, flag, n_target, seed):
    elig = []
    for i, r in enumerate(P):
        s = S(r)
        if s is None:
            continue
        days = s.get(flag) or []
        if any(res is not None for d, res in days):
            elig.append(i)
    elig.sort(key=lambda i: _h(seed, P[i]["sid"], P[i]["dt"], P[i]["code"]))
    pick = set(elig[:n_target])
    rows = []
    for i, r in enumerate(P):
        if i in pick:
            s = S(r)
            cs = [res for d, res in s[flag] if res is not None]
            if not cs:
                rows.append(r); continue
            # only one candidate typically; pick by hash if multiple
            cs.sort(key=lambda x: _h(seed, r["sid"], r["dt"], r["code"], x[1]))
            nr = dict(r, net=cs[0][0], ex=cs[0][1])
            rows.append(nr)
        else:
            rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/s210/horizon_extend_exit.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    kx = 1 - float(ep.exit_slippage_bps) / 1e4
    lim = ep.entry_lookahead_days + ep.horizon_days + 8
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names), "kx": kx, "section": 210, "base_includes_el_c": True}}
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
                    flag, _g, _n = SPEC[vn]
                    for sd in range(NSEED):
                        plc[vn][sd][v] = placebo_rows(P, S, flag, nchg, sd)
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
                 d_pos_share=round(sum(1 for d in ds if d > 0) / len(ds), 3),
                 new_win=round(100.0 * sum(1 for y in new if y > 0) / len(new), 1))
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

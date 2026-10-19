"""Sprint4 §148：短线 跳空出场改为"当日收盘再卖"。21 次选股平均，口径同 §127–§146。

预注册写于 /workspace/strategy-research.md §148（2026-10-19 17:21 CST，结果出来前写定，无网格、不调参）。
BASE 已成交、离场原因（冒号前）为目标原因的单，出场日 x 的日K b_x：
  b_x 收盘封跌停（close <= 前收*(1-band)+0.005 且 close <= low+0.005；band 0.098 / 创业板科创 0.198）
     → 下一个可卖日开盘卖（无可卖日 → 保留原结果并计数）；否则新原始出场价 = b_x 收盘。
  新净 = 新原始价 * (原出场成交价/原原始价) / 入场成交价 * 100 - 100 - fee（fee = 原 gross - 原 net，同执行核）。
  GC_SL  只改 stop_loss_gap；GC_TR 只改 trailing_stop_gap；GC_ALL 两类都改。
采纳（8 条全部满足，最多 1 个，多个取 N3 每笔提升最大）：1) 每笔 R12/N3 > BASE；2) PF R12/N3 ≥ BASE 且 ≥ 1.3；
3) MDD R12/N3 ≥ BASE；4) 被改单 Δ 中位数 R12/N3 > 0 且去掉 Δ 最大 10% 后 Δ 均值仍 > 0；
5) W0/W00 MDD 不比 BASE 差 > 3 点、每笔不低 0.3pp 以上；6) 被改单 R12 ≥ 10（否则不可检验 → 否决）、N3 ≥ 20；
7) 最长连亏 R12/N3 ≤ BASE + 1；8) 总 R12/N3 > 0、n R12 ≥ 30 / N3 ≥ 60。
只读本地 solo 面板 + MySQL 日K（SELECT）+ 本地 daily_kbar 缓存（信息项），不调 promax / tushare / 网关，不写库。
"""
from __future__ import annotations
import argparse, gc, json, os, statistics, sys
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
VARIANTS = ("BASE", "GC_SL", "GC_TR", "GC_ALL")
PRIMARY = ("GC_SL", "GC_TR", "GC_ALL")
TARGET = {"GC_SL": {"stop_loss_gap"}, "GC_TR": {"trailing_stop_gap"}, "GC_ALL": {"stop_loss_gap", "trailing_stop_gap"}}
ORDER = ("EXT", "12m", "W0", "W00")
STATS = {"mismatch": 0, "locked_next_open": 0, "locked_no_bar": 0, "gap_trades": 0}


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def _band(code: str) -> float:
    return 0.198 if code.startswith(("300", "301", "688")) else 0.098


def sim_card(card, bars, dt, ep, lim):
    b = tuple(bars[:lim])
    day = Day(); day.signal_date = dt; day.outcome_bars = {card["code"]: b}
    tr = (PE._simulate_card(day, card, ep) or {}).get("trade")
    if not (tr and tr.executed and tr.exit_date and tr.entry_date):
        return None
    why = str(tr.exit_reason or "").split(":")[0]
    out = {"net": float(tr.net_return_pct), "en": _d(tr.entry_date), "ex": _d(tr.exit_date), "why": why}
    if why not in ("stop_loss_gap", "trailing_stop_gap"):
        return out
    STATS["gap_trades"] += 1
    E, X = float(tr.entry_price), float(tr.exit_price)
    fee = float(tr.gross_return_pct) - float(tr.net_return_pct)
    jx = next((i for i, x in enumerate(b) if _d(x.trade_date) == out["ex"]), None)
    if jx is None or jx == 0:
        STATS["mismatch"] += 1
        return out
    bx, bp = b[jx], b[jx - 1]
    raw_old = max(bx.open, E * 0.85) if why == "stop_loss_gap" else bx.open
    if abs((X / E - 1) * 100 - fee - out["net"]) > 1e-6 or raw_old <= 0 or not (0.99 < X / raw_old <= 1.0):
        STATS["mismatch"] += 1
        return out
    k = X / raw_old  # 出场滑点系数（与执行核一致）
    locked = bx.close <= bp.close * (1 - _band(card["code"])) + 0.005 and bx.close <= bx.low + 0.005
    new_raw, new_ex = bx.close, out["ex"]
    if locked:
        nxt = None
        for j in range(jx + 1, len(bars)):
            if not _unsellable(bars[j], bars[j - 1]) and bars[j].open > 0:
                nxt = bars[j]; break
        if nxt is None:
            STATS["locked_no_bar"] += 1
            return out
        STATS["locked_next_open"] += 1
        new_raw, new_ex = nxt.open, _d(nxt.trade_date)
    out["gc"] = (round((new_raw * k / E - 1) * 100 - fee, 10), new_ex, bool(locked))
    out["gap_open"] = bx.open / bp.close - 1
    return out


def trig(vn, s):
    return s is not None and "gc" in s and s["why"] in TARGET[vn]


def apply(r, s):
    net, ex, _ = s["gc"]
    return dict(r, net=net, ex=ex)


def market_base(exit_dates):
    """信息项：同一批出场日，全市场非北交所 开盘 ≤ 前收×0.97 的股票 开盘→收盘 平均收益。"""
    try:
        import polars as pl
        sys.path.insert(0, str(B / "scripts" / "midterm"))
        from harness import CACHE
        ds = sorted(exit_dates)
        df = pl.scan_parquet(CACHE).select("code", "trade_date", "open", "close", "pct_chg").with_columns(
            pl.col("trade_date").cast(pl.Utf8).str.replace_all("-", "").alias("d")).filter(pl.col("d").is_in(ds)).collect()
        df = df.filter(~pl.col("code").str.slice(0, 1).is_in(["4", "8", "9"]) & (pl.col("open") > 0))
        df = df.with_columns((pl.col("close") / (1 + pl.col("pct_chg") / 100)).alias("pc"))
        g = df.filter(pl.col("open") <= pl.col("pc") * 0.97)
        oc = (g["close"] / g["open"] - 1) * 100
        return {"dates": len(ds), "dates_found": int(df["d"].n_unique()), "n_gapdown3": g.height,
                "open_to_close_mean": round(float(oc.mean()), 3) if g.height else None,
                "open_to_close_median": round(float(oc.median()), 3) if g.height else None,
                "share_close_gt_open": round(float((oc > 0).mean()), 3) if g.height else None}
    except Exception as e:  # 信息项失败不影响判定
        return {"error": repr(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--windows", default=",".join(ORDER))
    ap.add_argument("--out", default="output/research/sprint4/gap_exit_close.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    res: dict = {"_meta": {"names": len(names)}}
    keep: dict = {}
    exit_dates_all = {"12m": set(), "EXT": set()}
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
            aff = {vn: {"old": [], "new": [], "why": defaultdict(list), "sid": defaultdict(list), "locked": 0} for vn in PRIMARY}
            per_sid = {vn: {sid: {} for sid in SIDS} for vn in VARIANTS}
            for v in names:
                P = picks[v]
                kw["BASE"][v] = P
                for vn in PRIMARY:
                    rows = []
                    for r in P:
                        s = S(r)
                        if trig(vn, s):
                            nr = apply(r, s); rows.append(nr)
                            aff[vn]["old"].append(r["net"]); aff[vn]["new"].append(nr["net"])
                            aff[vn]["why"][s["why"]].append((r["net"], nr["net"]))
                            aff[vn]["sid"][r["sid"]].append((r["net"], nr["net"]))
                            aff[vn]["locked"] += int(s["gc"][2])
                            if w in exit_dates_all and vn == "GC_ALL":
                                exit_dates_all[w].add(s["ex"])
                        else:
                            rows.append(r)
                    kw[vn][v] = rows
                for vn in VARIANTS:
                    for sid in SIDS:
                        per_sid[vn][sid][v] = [r for r in kw[vn][v] if r["sid"] == sid]
            nd = len(names)
            keep[w] = {"days": all_days, "days_by_sid": days_by_sid, "kw": kw, "per_sid": per_sid, "aff": aff}
            out = {"_days": len(all_days), "_stats_cum": dict(STATS)}
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
             "per_sid": {vn: {sid: {v: a12["per_sid"][vn][sid][v] + ae["per_sid"][vn][sid][v] for v in names}
                              for sid in SIDS} for vn in VARIANTS}}
        out = summarize(m, names)
        for vn in PRIMARY:
            A = {"old": a12["aff"][vn]["old"] + ae["aff"][vn]["old"], "new": a12["aff"][vn]["new"] + ae["aff"][vn]["new"],
                 "why": defaultdict(list), "sid": defaultdict(list), "locked": a12["aff"][vn]["locked"] + ae["aff"][vn]["locked"]}
            for src in (a12, ae):
                for kk, xs in src["aff"][vn]["why"].items(): A["why"][kk] += xs
                for kk, xs in src["aff"][vn]["sid"].items(): A["sid"][kk] += xs
            out[vn]["aff"] = affstat(A, len(names))
        res["N3"] = out
        print("N3", json.dumps(out, ensure_ascii=False), flush=True)
        res["_market_base_R12"] = market_base(exit_dates_all["12m"])
        res["_market_base_N3"] = market_base(exit_dates_all["12m"] | exit_dates_all["EXT"])
        print("MARKET", json.dumps({k: res[k] for k in ("_market_base_R12", "_market_base_N3")}, ensure_ascii=False), flush=True)

    verdict = {}
    for vn in PRIMARY:
        r12, n3, b12, b3 = res["12m"][vn], res["N3"][vn], res["12m"]["BASE"], res["N3"]["BASE"]
        f12, f3 = r12["aff"], n3["aff"]
        c1 = r12["avg"] > b12["avg"] and n3["avg"] > b3["avg"]
        c2 = r12["pf"] >= max(1.3, b12["pf"]) and n3["pf"] >= max(1.3, b3["pf"])
        c3_ = r12["mdd"] >= b12["mdd"] and n3["mdd"] >= b3["mdd"]
        c4 = (f12.get("d_median", 0) > 0 and f3.get("d_median", 0) > 0
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


def affstat(A, nd):
    old, new = A["old"], A["new"]
    pair = lambda xs: {"n": round(len(xs) / nd, 1), "old": round(statistics.fmean(x for x, _ in xs), 3),
                       "new": round(statistics.fmean(y for _, y in xs), 3)} if xs else {"n": 0}
    o = {"n": round(len(old) / nd, 1), "old": round(statistics.fmean(old), 3) if old else 0.0,
         "new": round(statistics.fmean(new), 3) if new else 0.0, "locked": round(A["locked"] / nd, 1),
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

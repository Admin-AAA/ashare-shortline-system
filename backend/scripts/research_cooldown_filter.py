"""Sprint4 §104：短线策略级连亏 / 权益曲线冷却。21 次选股平均，口径与 §94/§97/§101 相同。

预注册写于 /workspace/strategy-research.md §104（2026-10-19 02:24 CST，结果出来前写定，无网格、不调参）。
状态只用同一策略、同一抽样的未过滤 cur 影子成交（executed，net_return_pct 已扣费）；信号日 T 只用 exit_date ≤ T 的影子单；
平仓顺序 (exit_date, entry_date, code)；历史衔接 EXT→12m、W0→W00（EXT/W0 冷启动）。触发 → 当日该策略全部选股不成交、不递补。
  C1 连亏3停5   : 日终最近 3 笔平仓全亏且当日有新亏损平仓 → 触发日 d；0 ≤ TD(T)−TD(d) ≤ 4 跳过
  C2 近10笔和负 : 截至 T 最近 10 笔平仓 net 和 < 0 跳过（不足 10 笔不跳过）
  C3 权益均线   : E(t)=截至 t 平仓 net 累加；MA20(E)(T) < MA60(E)(T) 跳过（E 不足 60 个交易日不跳过）
采纳（全部满足）：1) 12m 与 N3 每笔 > cur；2) 12m 与 N3 PF ≥ cur 且 ≥ 1.3；3) 12m 笔数 ≥ 30、N3 ≥ 60；
4) 12m 与 N3 总收益 > 0；5) W0 / W00 MDD 不比 cur 差超过 3 点；6) N3 被删且本会成交笔数（21 次平均）≥ 10。
"""
from __future__ import annotations
import argparse, bisect, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

VARIANTS = ("cur", "C1", "C2", "C3")
PRIMARY = ("C1", "C2", "C3")
CHAIN = {"12m": "EXT", "W00": "W0"}
ORDER = ("EXT", "12m", "W0", "W00")


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def blocked_days(shadow: list[tuple], cal: list[str]) -> dict[str, set[int]]:
    """shadow: [(exit, entry, code, net)] 已排序。返回每个变体被冷却的信号日 TD 序号集合。"""
    td = {d: i for i, d in enumerate(cal)}
    idx = lambda d: td.get(d, bisect.bisect_right(cal, d) - 1)
    by_day: dict[int, list[float]] = defaultdict(list)
    for ex, _en, _c, net in shadow:
        by_day[idx(ex)].append(net)
    out = {"C1": set(), "C2": set(), "C3": set()}
    if not by_day:
        return out
    first = min(by_day)
    closed: list[float] = []
    E, eq = [], 0.0
    for i in range(first, len(cal)):
        new = by_day.get(i, [])
        closed.extend(new)
        eq += sum(new)
        E.append(eq)
        # C1
        if any(x < 0 for x in new) and len(closed) >= 3 and all(x < 0 for x in closed[-3:]):
            out["C1"].update(range(i, i + 5))
        # C2
        if len(closed) >= 10 and sum(closed[-10:]) < 0:
            out["C2"].add(i)
        # C3
        if len(E) >= 60 and sum(E[-20:]) / 20 < sum(E[-60:]) / 60:
            out["C3"].add(i)
    return out


def _m(rows, days):
    mm = _metrics(rows, days)
    if not mm:
        return {"n": 0, "avg": 0, "pf": 0, "mdd": 0, "wr": 0, "streak": 0, "aw": 0, "al": 0}
    seq = [r["net"] for r in sorted(rows, key=lambda r: (r["ex"], r["dt"], r["code"]))]
    s = best = 0
    for x in seq:
        s = s + 1 if x < 0 else 0
        best = max(best, s)
    w = [x for x in seq if x > 0]; l = [x for x in seq if x < 0]
    return {"n": mm["n"], "avg": mm["avg"], "pf": mm["pf"], "mdd": mm["mdd"], "wr": mm["wr"] * 100, "streak": best,
            "aw": statistics.fmean(w) if w else 0.0, "al": statistics.fmean(l) if l else 0.0}


def _mean(per, names):
    mean = lambda k: statistics.mean(per[v][k] for v in names)
    return {"n": round(mean("n"), 1), "avg": round(mean("avg"), 3), "pf": round(mean("pf"), 2), "mdd": round(mean("mdd"), 1),
            "wr": round(mean("wr"), 1), "streak": round(mean("streak"), 1), "aw": round(mean("aw"), 2), "al": round(mean("al"), 2),
            "total": round(statistics.mean(per[v]["n"] * per[v]["avg"] for v in names), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chen-xiaoqun-skill,chao-gu-yang-jia-skill,sector-relative-strength-leader,"
                                      "oversold-capital-reversal,high-range-breakout-momentum")
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/cooldown_filter.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    res: dict = {}
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    from sqlalchemy import text
    with SessionLocal() as db:
        cal = sorted({_d(r[0]) for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
        tdi = {d: i for i, d in enumerate(cal)}
        for sid in a.sids.split(","):
            res[sid] = {}
            pol = standalone_selection_policy(base_pol, sid, 3)
            pw = panels(sid)
            keep: dict = {}
            shadow_of: dict = {}  # w -> draw -> [(exit, entry, code, net)]
            for w in ORDER:
                if w not in pw:
                    continue
                p = json.loads(pw[w].read_text())
                _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]
                allrows: dict = {}
                for v in names:
                    def rule(cs, d, _v=v):
                        if LIVE_DAY[sid](d["market_snapshot"]):
                            return []
                        cs = base(cs, d)
                        if sid in CARD:
                            cs = [c for c in cs if CARD[sid](c)]
                        return tiebreak(cs, _v, d["signal_date"])
                    _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                    allrows[v] = rows
                need: dict = defaultdict(set)
                for rows in allrows.values():
                    for r in rows:
                        need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need)
                sim: dict = {}

                def sim_of(dt, code):
                    k = (dt, code)
                    if k not in sim:
                        day = Day(); day.signal_date = dt
                        day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                        tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                        sim[k] = ((float(tr.net_return_pct), _d(tr.entry_date), _d(tr.exit_date))
                                  if tr and tr.executed and tr.exit_date else None)
                    return sim[k]

                shadow_of[w] = {}
                blk = {}
                for v in names:
                    sh = []
                    for r in allrows[v]:
                        s = sim_of(r["dt"], r["code"])
                        if s is not None:
                            sh.append((s[2], s[1], r["code"], s[0]))
                    shadow_of[w][v] = sh
                    hist = (shadow_of.get(CHAIN[w], {}).get(v, []) if w in CHAIN else []) + sh
                    hist.sort()
                    blk[v] = blocked_days(hist, cal)

                def T(dt):
                    t = _d(dt)
                    return tdi.get(t, bisect.bisect_right(cal, t) - 1)

                kw = {vn: {} for vn in VARIANTS}
                rm = {vn: {} for vn in PRIMARY}
                skip = {vn: [] for vn in PRIMARY}
                for vn in VARIANTS:
                    for v in names:
                        nrows, rrows, ns = [], [], 0
                        for r in allrows[v]:
                            s = sim_of(r["dt"], r["code"])
                            ok = vn == "cur" or T(r["dt"]) not in blk[v][vn]
                            if not ok:
                                ns += 1
                                if s is not None:
                                    rrows.append(s[0])
                                continue
                            if s is not None:
                                nrows.append({"dt": r["dt"], "code": r["code"], "net": s[0], "ex": s[2]})
                        kw[vn][v] = nrows
                        if vn != "cur":
                            rm[vn][v] = rrows
                            skip[vn].append(ns / len(allrows[v]) if allrows[v] else 0.0)
                keep[w] = {"days": days, "kw": kw, "rm": rm}
                out = {}
                for vn in VARIANTS:
                    out[vn] = _mean({v: _m(kw[vn][v], days) for v in names}, names)
                    if vn != "cur":
                        allr = [x for v in names for x in rm[vn][v]]
                        out[vn].update({"skip_rate": round(statistics.mean(skip[vn]), 3),
                                        "n_rm": round(len(allr) / len(names), 1),
                                        "avg_rm": round(statistics.mean(allr), 3) if allr else None})
                res[sid][w] = out
                print(sid, w, json.dumps(out, ensure_ascii=False), flush=True)
                outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            if "12m" in keep and "EXT" in keep:
                days = sorted(set(keep["12m"]["days"]) | set(keep["EXT"]["days"]))
                out = {}
                for vn in VARIANTS:
                    out[vn] = _mean({v: _m(keep["12m"]["kw"][vn][v] + keep["EXT"]["kw"][vn][v], days) for v in names}, names)
                    if vn != "cur":
                        allr = [x for w in ("12m", "EXT") for v in names for x in keep[w]["rm"][vn][v]]
                        out[vn].update({"n_rm": round(len(allr) / len(names), 1),
                                        "avg_rm": round(statistics.mean(allr), 3) if allr else None})
                res[sid]["N3"] = out
                print(sid, "N3", json.dumps(out, ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))

    verdict = {}
    for sid, ws in res.items():
        verdict[sid] = {}
        for vn in PRIMARY:
            r12, n3, cur12, cur3 = ws["12m"][vn], ws["N3"][vn], ws["12m"]["cur"], ws["N3"]["cur"]
            c1 = r12["avg"] > cur12["avg"] and n3["avg"] > cur3["avg"]
            c2 = r12["pf"] >= max(1.3, cur12["pf"]) and n3["pf"] >= max(1.3, cur3["pf"])
            c3 = r12["n"] >= 30 and n3["n"] >= 60
            c4 = r12["total"] > 0 and n3["total"] > 0
            c5 = all(ws[w][vn]["mdd"] >= ws[w]["cur"]["mdd"] - 3.0 for w in ("W0", "W00") if w in ws)
            c6 = (n3["n_rm"] or 0) >= 10
            verdict[sid][vn] = {"c1_avg": c1, "c2_pf": c2, "c3_n": c3, "c4_total": c4, "c5_old_mdd": c5, "c6_rm": c6,
                                "pass": all((c1, c2, c3, c4, c5, c6)), "n3_lift": round(n3["avg"] - cur3["avg"], 3)}
        passed = [vn for vn in PRIMARY if verdict[sid][vn]["pass"]]
        verdict[sid]["_adopt"] = max(passed, key=lambda vn: verdict[sid][vn]["n3_lift"]) if passed else None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

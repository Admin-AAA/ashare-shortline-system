"""Sprint4 §99（短线部分）：大额限售解禁回避入场过滤。21 次选股平均，口径与 §94/§97 相同。

预注册写于 /workspace/strategy-research.md §99（结果出来前写定，无网格、不调参）。
解禁口径 = app/services/unlock_filter.py：share_floats quantdata 行去重，float_date ∈ (T, T 后第 N 个交易日]
float_ratio（占总股本 %）求和；主口径 PIT（ann_date ≤ T）；*_all 为忽略 ann_date 的敏感性（仅参考，不参与采纳）。
  U1 : 20 个交易日内合计 ≥ 3% → 跳过
  U2 : 持有期 10 个交易日内合计 ≥ 1% → 跳过
过滤语义对齐 gap_up_blocked：被过滤的选股不成交、不递补。
窗口 12m（≈R12）/ EXT 2025-01~09 / W0 / W00；"N3" = 12m+EXT 合并（每次抽样合并后算指标，再 21 次平均）。
采纳（全部满足）：1) 12m 与 N3 每笔均值 > cur；2) 12m 与 N3 PF ≥ min(1.3, cur PF)；3) 12m 与 N3 笔数 ≥ 30（cur 12m <30 只看 N3）；
4) cur 选股在 N3 中被删且成交的笔数（21 次平均）≥ 10，且其每笔均值 < cur 每笔均值 − 1pp；5) W0 / W00 MDD 不比 cur 差超过 3 点。
"""
from __future__ import annotations
import argparse, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.production_policy import load_production_selection_policy
from app.services.standalone_strategies import standalone_selection_policy
from app.services.unlock_filter import load_events, ratio_between
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

CTX: dict = {"ev": {}, "cal": [], "u": {}}


def _end(dt: str, n: int) -> str:
    import bisect
    cal = CTX["cal"]
    k = bisect.bisect_right(cal, dt.replace("-", ""))
    return cal[min(k + n - 1, len(cal) - 1)]


def _u(dt, code, n, pit):
    k = (dt, code, n, pit)
    if k not in CTX["u"]:
        CTX["u"][k] = ratio_between(CTX["ev"], code, dt, _end(dt, n), pit)
    return CTX["u"][k]


VARIANTS = {
    "cur": lambda dt, code: True,
    "U1": lambda dt, code: _u(dt, code, 20, True) < 3,
    "U2": lambda dt, code: _u(dt, code, 10, True) < 1,
    "U1_all": lambda dt, code: _u(dt, code, 20, False) < 3,
    "U2_all": lambda dt, code: _u(dt, code, 10, False) < 1,
}
PRIMARY = ("U1", "U2")


def _m(rows, days):
    mm = _metrics(rows, days)
    return ({"n": mm["n"], "avg": mm["avg"], "pf": mm["pf"], "mdd": mm["mdd"], "wr": mm["wr"] * 100}
            if mm else {"n": 0, "avg": 0, "pf": 0, "mdd": 0, "wr": 0})


def _mean(per, names):
    mean = lambda k: statistics.mean(per[v][k] for v in names)
    return {"n": round(mean("n"), 1), "avg": round(mean("avg"), 3), "pf": round(mean("pf"), 2), "mdd": round(mean("mdd"), 1),
            "wr": round(mean("wr"), 1), "total": round(statistics.mean(per[v]["n"] * per[v]["avg"] for v in names), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chen-xiaoqun-skill,chao-gu-yang-jia-skill,sector-relative-strength-leader,"
                                      "oversold-capital-reversal,high-range-breakout-momentum")
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/unlock_entry_filter.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    res: dict = {}
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    from sqlalchemy import text
    with SessionLocal() as db:
        CTX["ev"] = load_events(db, "20150101", "20301231")
        past = {str(r[0]).replace("-", "") for r in db.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))}
        fut = {str(r[0]) for r in db.execute(text(
            "SELECT cal_date FROM trade_calendar WHERE exchange='SSE' AND is_open=1"))}
        CTX["cal"] = sorted(past | fut)  # trade_calendar 只从 2025-09 起，历史用 daily_kbars 交易日
        print("events codes", len(CTX["ev"]), "cal", CTX["cal"][0], CTX["cal"][-1], flush=True)
        for sid in a.sids.split(","):
            res[sid] = {}
            pol = standalone_selection_policy(base_pol, sid, 3)
            keep: dict = {}  # w -> vn -> draw -> rows ; w -> days
            for w, f in panels(sid).items():
                p = json.loads(f.read_text())
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
                net_cache: dict = {}

                def net_of(dt, code):
                    k = (dt, code)
                    if k not in net_cache:
                        day = Day(); day.signal_date = dt
                        day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                        tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                        net_cache[k] = float(tr.net_return_pct) if tr and tr.executed else None
                    return net_cache[k]

                kw = {vn: {} for vn in VARIANTS}
                rm = {vn: {} for vn in VARIANTS if vn != "cur"}
                skip = {vn: [] for vn in VARIANTS if vn != "cur"}
                for vn, pred in VARIANTS.items():
                    for v in names:
                        nrows, rrows, ns = [], [], 0
                        for r in allrows[v]:
                            x = net_of(r["dt"], r["code"])
                            ok = pred(r["dt"], r["code"])
                            if not ok:
                                ns += 1
                                if x is not None:
                                    rrows.append(x)
                                continue
                            if x is not None:
                                nrows.append({"dt": r["dt"], "code": r["code"], "net": x})
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
            # N3 = 12m + EXT pooled per draw
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
            c2 = r12["pf"] >= min(1.3, cur12["pf"]) and n3["pf"] >= min(1.3, cur3["pf"])
            c3 = n3["n"] >= 30 and (r12["n"] >= 30 or cur12["n"] < 30)
            c4 = (n3["n_rm"] or 0) >= 10 and n3["avg_rm"] is not None and n3["avg_rm"] < cur3["avg"] - 1.0
            c5 = all(ws[w][vn]["mdd"] >= ws[w]["cur"]["mdd"] - 3.0 for w in ("W0", "W00") if w in ws)
            verdict[sid][vn] = {"c1_avg": c1, "c2_pf": c2, "c3_n": c3, "c4_removed": c4, "c5_old_mdd": c5,
                                "pass": all((c1, c2, c3, c4, c5)), "n3_lift": round(n3["avg"] - cur3["avg"], 3)}
        passed = [vn for vn in PRIMARY if verdict[sid][vn]["pass"]]
        verdict[sid]["_adopt"] = max(passed, key=lambda vn: verdict[sid][vn]["n3_lift"]) if passed else None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

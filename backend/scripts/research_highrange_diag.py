"""Sprint4 §107 诊断（只读）：high-range-breakout-momentum 为何 EXT / W0 / W00 亏损。

口径同 §94/§104：base + 20 hash 平手 → 21 次选股，现行 exit_overrides/ma_break_exit 逐票 _simulate_card 重模拟。
输出逐笔明细（全部 21 次抽样，带 draw 标签）+ 分桶统计（每桶 n 为 21 次平均笔数，每笔均值/PF 为合并样本）。
桶：yaml 震荡日条件（卡片 up_count/market_sample_count ∈[0.35,0.5)）、卡片 market_regime、等权指数趋势（c<MA20、MA20<MA60）、
次日开盘缺口（open/recommend_price−1）、入场日序号、出场原因、setup、信号日涨幅、当日合格候选数。
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
from scripts.research_entry_rule_panel import RULES, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels, ur
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import tiebreak

SID = "high-range-breakout-momentum"
ORDER = ("EXT", "12m", "W0", "W00")
IDX = json.loads(Path("/tmp/eqw_idx.json").read_text())


def fv(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    if v is None:
        v = c.get(k)
    return v


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _d(x):
    return str(x)[:10]


def stat(nets, ndraw):
    if not nets:
        return {"n": 0}
    w = [x for x in nets if x > 0]; l = [x for x in nets if x < 0]
    return {"n": round(len(nets) / ndraw, 1), "wr": round(100 * len(w) / len(nets), 1), "avg": round(statistics.fmean(nets), 3),
            "pf": round(sum(w) / max(abs(sum(l)), 1e-9), 2), "aw": round(statistics.fmean(w), 2) if w else 0,
            "al": round(statistics.fmean(l), 2) if l else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/highrange_diag.json")
    a = ap.parse_args()
    pol = standalone_selection_policy(load_production_selection_policy(), SID, 3)
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    pw = panels(SID)
    res, trades = {}, []
    with SessionLocal() as db:
        for w in ORDER:
            p = json.loads(pw[w].read_text())
            _register_only(p)
            base = RULES[f"only:{SID}"]
            cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
            msnap = {d["signal_date"]: d["market_snapshot"] for d in p["days"]}
            elig = {}
            for d in p["days"]:
                cs = base([dict(c) for c in d["cards"]], d)
                elig[d["signal_date"]] = sum(1 for c in cs if float(c.get("final_score") or 0) >= 72.0)
            allrows = {}
            for v in names:
                def rule(cs, d, _v=v):
                    return tiebreak(base(cs, d), _v, d["signal_date"])
                _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                allrows[v] = rows
            need = defaultdict(set)
            for rows in allrows.values():
                for r in rows:
                    need[r["dt"]].add(r["code"])
            bars = fetch_bars(db, need)
            sim = {}
            for v in names:
                for r in allrows[v]:
                    k = (r["dt"], r["code"])
                    if k not in sim:
                        day = Day(); day.signal_date = r["dt"]
                        bs = tuple(bars[r["dt"]].get(r["code"], ())[:lim])
                        day.outcome_bars = {r["code"]: bs}
                        tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                        sim[k] = (tr, bs)
                    tr, bs = sim[k]
                    c = cards[k]; ms = msnap[r["dt"]]
                    rp = float(c.get("recommend_price") or 0)
                    o1 = bs[0].open if bs else None
                    uc, msc = num(fv(c, "up_count")), num(fv(c, "market_sample_count"))
                    x = IDX.get(_d(r["dt"])) or {}
                    ex = bool(tr and tr.executed)
                    ent_i = None
                    if ex:
                        ent_i = next((i for i, b in enumerate(bs) if b.trade_date[:10] == _d(tr.entry_date)), None)
                    trades.append({
                        "w": w, "draw": v, "dt": r["dt"], "code": r["code"], "executed": ex,
                        "net": float(tr.net_return_pct) if ex else None, "exit_reason": tr.exit_reason if tr else None,
                        "hold": tr.holding_days if ex else None, "entry_i": ent_i,
                        "entry_vs_rp": (tr.entry_price / rp - 1) * 100 if ex and rp else None,
                        "gap1": (o1 / rp - 1) * 100 if o1 and rp else None,
                        "ur_snap": ur(ms), "ur_card": (uc / msc) if uc is not None and msc else None,
                        "regime": c.get("market_regime"), "breadth_regime": (ms.get("breadth") or {}).get("regime"),
                        "idx_lt_ma20": (x["c"] < x["ma20"]) if x.get("ma20") else None,
                        "idx_ma20_lt_ma60": (x["ma20"] < x["ma60"]) if x.get("ma60") else None,
                        "setup": c.get("setup_type"), "pct": num(c.get("pct")), "chg20": num(fv(c, "chg20")),
                        "phase": next((s.get("phase") for s in c.get("strategy_signals") or [] if s.get("strategy_id") == SID), None),
                        "elig": elig.get(r["dt"]), "score": float(c.get("final_score") or 0),
                        "lu_cnt": ms.get("limit_up_count"), "ld_cnt": ms.get("limit_down_count"),
                    })
            print(w, "days", len(p["days"]), "rows/draw", statistics.mean(len(allrows[v]) for v in names), flush=True)

    nd = len(names)

    def bucket(rowsel, keyf):
        g = defaultdict(list)
        for t in rowsel:
            if t["executed"]:
                g[keyf(t)].append(t["net"])
        return {str(k): stat(v, nd) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}

    def gapb(t):
        g = t["gap1"]
        if g is None: return "na"
        return "<-2" if g < -2 else "-2..0" if g < 0 else "0..2" if g < 2 else "2..4" if g < 4 else ">=4"

    def pctb(t):
        g = t["pct"]
        if g is None: return "na"
        return "<1.5" if g < 1.5 else "1.5..5" if g <= 5 else "5..7" if g <= 7 else ">7"

    def urb(t):
        u = t["ur_card"] if t["ur_card"] is not None else t["ur_snap"]
        return "<0.35" if u < 0.35 else "0.35-0.5(choppy)" if u < 0.5 else "0.5-0.65" if u < 0.65 else ">=0.65"

    groups = {w: [t for t in trades if t["w"] == w] for w in ORDER}
    groups["N3"] = groups["12m"] + groups["EXT"]
    groups["OLD"] = groups["W0"] + groups["W00"]
    for w, ts in groups.items():
        ex = [t for t in ts if t["executed"]]
        res[w] = {
            "all": stat([t["net"] for t in ex], nd),
            "sel_per_draw": round(len(ts) / nd, 1), "not_exec_per_draw": round(sum(1 for t in ts if not t["executed"]) / nd, 1),
            "not_exec_reasons": dict(sorted(defaultdict(int, {}).items())),
            "by_ur": bucket(ts, urb),
            "by_regime": bucket(ts, lambda t: t["regime"]),
            "by_idx_lt_ma20": bucket(ts, lambda t: t["idx_lt_ma20"]),
            "by_idx_ma20_lt_ma60": bucket(ts, lambda t: t["idx_ma20_lt_ma60"]),
            "by_gap1": bucket(ts, gapb),
            "by_entry_i": bucket(ts, lambda t: t["entry_i"]),
            "by_exit": bucket(ts, lambda t: t["exit_reason"]),
            "by_setup": bucket(ts, lambda t: t["setup"]),
            "by_phase": bucket(ts, lambda t: t["phase"]),
            "by_pct": bucket(ts, pctb),
            "by_elig": bucket(ts, lambda t: "1-3" if (t["elig"] or 0) <= 3 else "4-6" if t["elig"] <= 6 else ">6"),
            "avg_hold": round(statistics.fmean([t["hold"] for t in ex]), 2) if ex else None,
            "avg_entry_vs_rp": round(statistics.fmean([t["entry_vs_rp"] for t in ex if t["entry_vs_rp"] is not None]), 2) if ex else None,
            "days_with_trade_per_draw": round(len({(t["draw"], t["dt"]) for t in ex}) / nd, 1),
        }
        ne = defaultdict(int)
        for t in ts:
            if not t["executed"]:
                ne[str(t["exit_reason"])] += 1
        res[w]["not_exec_reasons"] = {k: round(v / nd, 1) for k, v in ne.items()}
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    Path(str(outp).replace(".json", "_trades.json")).write_text(json.dumps(trades, ensure_ascii=False))
    for w in ("12m", "EXT", "N3", "W0", "W00"):
        print("==", w, json.dumps(res[w], ensure_ascii=False))


if __name__ == "__main__":
    main()

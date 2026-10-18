"""Sprint4 §96：信号日K线形态过滤（追高 / 收盘位置），目标提高每笔收益。21 次选股平均，口径与 §94 相同。

假设：信号日大涨（追高）或收在当日区间下部（冲高回落）的卡片次日期望更差。
预注册变体（写死，见结果前不改；仅用卡片信号日 open/high/low/close/pct，均为信号日收盘已知）：
  1. cur          — 基线
  2. skip_pct7    — 跳过信号日涨幅 pct > 7%
  3. skip_weak    — 跳过收盘位置 (close-low)/(high-low) < 0.3
  4. keep_strong  — 仅保留收盘位置 >= 0.7
  5. skip_pct7_weak — 同时应用 2 和 3
采纳标准与 §94 相同（≥3/4 窗每笔均值>cur、4 窗 lift≥+0.15pp、12m total≥85% cur、12m PF≥cur），不放宽。
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
from scripts.research_entry_rule_panel import RULES, _metrics, run_rule
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD

def _f(c, k):
    try:
        return float(c.get(k))
    except (TypeError, ValueError):
        return None


def _pos(c):
    h, l, cl = _f(c, "high"), _f(c, "low"), _f(c, "close")
    if None in (h, l, cl) or h <= l:
        return None
    return (cl - l) / (h - l)


def _pct(c):
    p = _f(c, "pct")
    return p


# 预注册变体：predicate(card) -> keep?
VARIANTS = {
    "cur": lambda c: True,
    "skip_pct7": lambda c: _pct(c) is None or _pct(c) <= 7.0,
    "skip_weak": lambda c: _pos(c) is None or _pos(c) >= 0.3,
    "keep_strong": lambda c: _pos(c) is not None and _pos(c) >= 0.7,
    "skip_pct7_weak": lambda c: (_pct(c) is None or _pct(c) <= 7.0) and (_pos(c) is None or _pos(c) >= 0.3),
}


def entry_gap(bars_for_day, code: str, prior_close: float | None) -> float | None:
    """next_open / prior_close - 1 from outcome bars (trade_date > signal)."""
    if prior_close is None or prior_close <= 0:
        return None
    seq = bars_for_day.get(code) or ()
    if not seq:
        return None
    nxt = float(seq[0].open or 0)
    if nxt <= 0:
        return None
    return nxt / prior_close - 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sids",
        default="chen-xiaoqun-skill,chao-gu-yang-jia-skill,sector-relative-strength-leader,oversold-capital-reversal,high-range-breakout-momentum",
    )
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/candle_entry_filter.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    res: dict = {}
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as db:
        for sid in a.sids.split(","):
            res[sid] = {}
            pol = standalone_selection_policy(base_pol, sid, 3)
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

                    _, rows = run_rule(
                        rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False
                    )
                    allrows[v] = rows

                need: dict = defaultdict(set)
                for rows in allrows.values():
                    for r in rows:
                        need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need)
                # gap + net cache shared across variants
                gap_cache: dict = {}
                net_cache: dict = {}

                def gap_of(dt, code):
                    k = (dt, code)
                    if k not in gap_cache:
                        card = cards.get(k) or {}
                        prior = card.get("recommend_price")
                        if prior is None:
                            fs = card.get("feature_snapshot") or {}
                            prior = fs.get("price")
                        try:
                            prior_f = float(prior) if prior is not None else None
                        except (TypeError, ValueError):
                            prior_f = None
                        gap_cache[k] = entry_gap(bars.get(dt) or {}, code, prior_f)
                    return gap_cache[k]

                def net_of(dt, code):
                    k = (dt, code)
                    if k not in net_cache:
                        day = Day()
                        day.signal_date = dt
                        day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                        tr = (PE._simulate_card(day, cards[k], ep) or {}).get("trade")
                        net_cache[k] = float(tr.net_return_pct) if tr and tr.executed else None
                    return net_cache[k]

                out = {}
                for vn, pred in VARIANTS.items():
                    per = {}
                    for v in names:
                        rows = allrows[v]
                        nrows = []
                        for r in rows:
                            if not pred(cards.get((r["dt"], r["code"])) or {}):
                                continue
                            x = net_of(r["dt"], r["code"])
                            if x is not None:
                                nrows.append({"dt": r["dt"], "code": r["code"], "net": x})
                        mm = _metrics(nrows, days)
                        per[v] = (
                            {"n": mm["n"], "avg": mm["avg"], "pf": mm["pf"], "mdd": mm["mdd"], "wr": mm["wr"] * 100}
                            if mm
                            else {"n": 0, "avg": 0, "pf": 0, "mdd": 0, "wr": 0}
                        )
                    mean = lambda k: statistics.mean(per[v][k] for v in names)
                    out[vn] = {
                        "n": round(mean("n"), 1),
                        "avg": round(mean("avg"), 3),
                        "pf": round(mean("pf"), 2),
                        "mdd": round(mean("mdd"), 1),
                        "wr": round(mean("wr"), 1),
                        "total": round(statistics.mean(per[v]["n"] * per[v]["avg"] for v in names), 1),
                        "paired_gt_cur": None,
                    }
                    out[vn]["_avgs"] = [round(per[v]["avg"], 3) for v in names]
                    # gap skip rate vs cur selection (mean over draws)
                    if vn != "cur":
                        skip_rates = []
                        for v in names:
                            rows = allrows[v]
                            if not rows:
                                skip_rates.append(0.0)
                                continue
                            skipped = sum(1 for r in rows if not pred(cards.get((r["dt"], r["code"])) or {}))
                            skip_rates.append(skipped / len(rows))
                        out[vn]["skip_rate"] = round(statistics.mean(skip_rates), 3)

                for vn in VARIANTS:
                    out[vn]["paired_gt_cur"] = round(
                        sum(x > y for x, y in zip(out[vn]["_avgs"], out["cur"]["_avgs"])) / len(names), 2
                    )
                # drop heavy _avgs from persisted summary but keep for paired
                slim = {
                    vn: {k: out[vn][k] for k in out[vn] if k != "_avgs"}
                    for vn in VARIANTS
                }
                res[sid][w] = slim
                print(sid, w, {vn: {k: slim[vn][k] for k in ("n", "avg", "pf", "mdd", "wr", "total", "skip_rate", "paired_gt_cur") if k in slim[vn]} for vn in VARIANTS}, flush=True)
                outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))

    verdict = {}
    for sid, ws in res.items():
        if sid.startswith("_"):
            continue
        verdict[sid] = {}
        for vn in VARIANTS:
            if vn == "cur":
                continue
            wins = sum(ws[w][vn]["avg"] > ws[w]["cur"]["avg"] for w in ws)
            lift = statistics.mean(ws[w][vn]["avg"] - ws[w]["cur"]["avg"] for w in ws)
            r12 = ws.get("12m") or next(iter(ws.values()))
            ok_total = r12[vn]["total"] >= 0.85 * r12["cur"]["total"]
            ok_pf = r12[vn]["pf"] >= r12["cur"]["pf"]
            verdict[sid][vn] = {
                "wins": f"{wins}/{len(ws)}",
                "lift": round(lift, 3),
                "total_ok": ok_total,
                "pf_ok": ok_pf,
                "pass": wins >= 3 and lift >= 0.15 and ok_total and ok_pf,
            }
        passed = [vn for vn, v in verdict[sid].items() if v["pass"]]
        if passed:
            best = max(passed, key=lambda vn: verdict[sid][vn]["lift"])
            verdict[sid]["_adopt"] = best
        else:
            verdict[sid]["_adopt"] = None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

"""Sprint4 §97：信号日主力资金流 / 融资余额作为入场过滤。21 次选股平均，口径与 §94/§96 相同。

假设：信号日主力净流出（尤其相对成交额大幅流出）或融资盘短期拥挤的卡片次日期望更差。
数据与时点（结果前写定）：
  - 主力净流入：MySQL capital_flow.main_net_inflow_yi（亿，Tushare moneyflow net_mf_amount），取信号日 T。
    时点：盘后流水线 cron 17:35 CST 内同步 capital_flow（created_at≈17:37–17:40 CST），实盘打分本身已读取
    T 日 main_net_inflow_yi（oversold 硬门槛 >0、chen 加分 >0.5、capital_resonance_score），T 收盘信号/T+1 开盘买入可用。
    缺失时回退卡片 feature_snapshot.main_net_inflow_yi；仍缺失 → 保留（不过滤）。
  - 成交额：卡片 amount_yi（信号日成交额，亿），缺失回退 feature_snapshot.amount_yi；缺失/<=0 → skip_mfneg_big 保留。
  - 融资余额：MySQL margin_detail.rzye，仅用 T−1（次日早上才发布）。T−1 = 交易日历中严格早于 T 的最近交易日，
    T−6 = 再往前 5 个交易日；增长 = rzye(T−1)/rzye(T−6) − 1。任一缺失（含非两融标的）→ 保留。
    margin_detail 自 2025-01 起 → 仅 12m / EXT 有覆盖；W0/W00 无覆盖（该变体在这两窗与 cur 完全相同）。
预注册变体（写死，见结果前不改，无网格、无事后调参）：
  1. cur             — 基线
  2. skip_mfneg      — 跳过信号日主力净流入 < 0
  3. skip_mfneg_big  — 跳过 主力净流入 / 信号日成交额 < −5%
  4. skip_rz_surge   — 跳过 rzye 在截至 T−1 的 5 个交易日内增长 ≥ +10%（融资拥挤）；不在 margin_detail 的保留
过滤语义对齐 gap_up_blocked：被过滤的选股不成交、不递补。
采纳标准与 §94 相同（≥3/4 窗每笔均值>cur、4 窗 lift≥+0.15pp、12m total≥85% cur、12m PF≥cur），不放宽。
skip_rz_surge 只在有覆盖的窗（12m、EXT）上判定：需 2/2 窗 >cur、覆盖窗平均 lift≥+0.15pp、12m total/PF 同上。
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

def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


CTX: dict = {"mf": {}, "rz": {}}  # per-window caches: (dt, code) -> value


def _mf(dt, code, c):
    v = CTX["mf"].get((dt, code))
    if v is None:
        v = _f((c.get("feature_snapshot") or {}).get("main_net_inflow_yi"))
    return v


def _amt(c):
    v = _f(c.get("amount_yi"))
    if v is None:
        v = _f((c.get("feature_snapshot") or {}).get("amount_yi"))
    return v if v and v > 0 else None


def _mf_ratio(dt, code, c):
    m, a = _mf(dt, code, c), _amt(c)
    return None if m is None or a is None else m / a


# 预注册变体：predicate(dt, code, card) -> keep?
VARIANTS = {
    "cur": lambda dt, code, c: True,
    "skip_mfneg": lambda dt, code, c: _mf(dt, code, c) is None or _mf(dt, code, c) >= 0,
    "skip_mfneg_big": lambda dt, code, c: _mf_ratio(dt, code, c) is None or _mf_ratio(dt, code, c) >= -0.05,
    "skip_rz_surge": lambda dt, code, c: CTX["rz"].get((dt, code)) is None or CTX["rz"][(dt, code)] < 0.10,
}
COVERED = {"skip_rz_surge": ("12m", "EXT")}


def load_ctx(db, need):
    """need: {dt: set(codes)} -> fill CTX mf / rz for exactly those (dt, code) pairs."""
    import bisect
    from datetime import date, timedelta
    from sqlalchemy import text
    CTX["mf"], CTX["rz"] = {}, {}
    codes = sorted({c for cs in need.values() for c in cs})
    dts = sorted(need)
    if not codes:
        return {}
    mf_rows = 0
    for i in range(0, len(dts), 40):
        chunk = dts[i:i + 40]
        q = text("SELECT code, trade_date, main_net_inflow_yi FROM capital_flow WHERE trade_date IN :d AND code IN :c").bindparams()
        from sqlalchemy import bindparam
        q = q.bindparams(bindparam("d", expanding=True), bindparam("c", expanding=True))
        for code, td, v in db.execute(q, {"d": chunk, "c": sorted({c for d in chunk for c in need[d]})}):
            if str(code) in need.get(str(td), ()):
                CTX["mf"][(str(td), str(code))] = _f(v)
                mf_rows += 1
    lo = (date.fromisoformat(dts[0]) - timedelta(days=25)).isoformat()
    hi = dts[-1]
    cal = [r[0] for r in db.execute(text(
        "SELECT DISTINCT trade_date FROM margin_detail WHERE trade_date >= :lo AND trade_date < :hi ORDER BY trade_date"),
        {"lo": lo, "hi": hi})]
    rz = defaultdict(dict)
    if cal:
        from sqlalchemy import bindparam
        q = text("SELECT code, trade_date, rzye FROM margin_detail WHERE trade_date >= :lo AND trade_date < :hi AND code IN :c").bindparams(bindparam("c", expanding=True))
        for code, td, v in db.execute(q, {"lo": lo, "hi": hi, "c": codes}):
            rz[str(code)][str(td)] = _f(v)
    rz_cov = 0
    for dt, cs in need.items():
        k = bisect.bisect_left(cal, dt) - 1  # T-1
        if k - 5 < 0:
            continue
        d1, d0 = cal[k], cal[k - 5]
        for code in cs:
            a1, a0 = rz.get(code, {}).get(d1), rz.get(code, {}).get(d0)
            if a1 is not None and a0 and a0 > 0:
                CTX["rz"][(dt, code)] = a1 / a0 - 1.0
                rz_cov += 1
    npair = sum(len(v) for v in need.values())
    return {"pairs": npair, "mf_db": mf_rows, "rz_cov": rz_cov}


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
    ap.add_argument("--out", default="output/research/sprint4/flow_entry_filter.json")
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
                cov = load_ctx(db, need)
                print(sid, w, 'coverage', cov, flush=True)
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
                            if not pred(r["dt"], r["code"], cards.get((r["dt"], r["code"])) or {}):
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
                            skipped = sum(1 for r in rows if not pred(r["dt"], r["code"], cards.get((r["dt"], r["code"])) or {}))
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
                slim["_coverage"] = cov
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
            wl = [w for w in ws if w in COVERED.get(vn, ws)]
            wins = sum(ws[w][vn]["avg"] > ws[w]["cur"]["avg"] for w in wl)
            lift = statistics.mean(ws[w][vn]["avg"] - ws[w]["cur"]["avg"] for w in wl)
            r12 = ws.get("12m") or next(iter(ws.values()))
            ok_total = r12[vn]["total"] >= 0.85 * r12["cur"]["total"]
            ok_pf = r12[vn]["pf"] >= r12["cur"]["pf"]
            verdict[sid][vn] = {
                "wins": f"{wins}/{len(wl)}",
                "windows": wl,
                "lift": round(lift, 3),
                "total_ok": ok_total,
                "pf_ok": ok_pf,
                "pass": (wins >= 3 if len(wl) >= 4 else wins == len(wl)) and lift >= 0.15 and ok_total and ok_pf,
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

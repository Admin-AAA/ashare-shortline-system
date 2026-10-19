"""Sprint4 §107：high-range-breakout-momentum 修复或退役。21 次选股平均，口径同 §94/§104（research_cooldown_filter.py）。

预注册写于 /workspace/strategy-research.md §107（2026-10-19 04:40 CST，跑任何变体之前写定，无网格、不调参）。
基线：
  cur_panel : 旧口径（与 §106b 逐位一致：EXT/W0 面板生成于 yaml「震荡日停出」之前，未补该否决）
  cur_live  : 现行规则口径 = cur_panel + 卡片 up_count ∈ [0.35, 0.5)×market_sample_count 当日不出票（yaml 原条件；12m/W00 面板本已含）
变体（全部在 cur_live 之上）：
  R1 skip_climax : 卡片 market_regime == '强牛·高潮' 不出票（yaml phase hard_veto）
  X1 ma10_break  : 持仓中收盘 < MA10 当日收盘离场（yaml ma_break_exit: {window: 10}，现成机制）
  RX             : R1 + X1
成本：引擎 net 已扣 ≈0.27% 往返；判定再扣 0.03pp（avg_c30）。
采纳（全部满足）：1) 12m 与 N3 每笔 > 0 且 avg_c30 > 0，N3 每笔 > cur_live N3；2) 12m 与 N3 PF ≥ 1.3；
3) 12m ≥ 30、N3 ≥ 60（1)2) 全过时放宽为 12m ≥ 15、N3 ≥ 30）；4) 12m 与 N3 总收益 > 0；
5) W0、W00 每笔 > −5% 且 MDD 不比 cur_live 差超过 3 点。
决策：恰好一个通过 → 采纳；多个 → N3 每笔最高；全不过 → 退役（RETIRED_STRATEGY_IDS）。
PF 口径同 §104/§106b：21 次各自 PF 的均值（另报合并样本 PF pf_pool 供参考）。
"""
from __future__ import annotations
import argparse, json, os, statistics, sys
from collections import Counter, defaultdict
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
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import tiebreak
from scripts.research_cooldown_filter import _m, _mean

SID = "high-range-breakout-momentum"
ORDER = ("EXT", "12m", "W0", "W00")
# 变体 -> (选股口径, 出场口径)
VARIANTS = {"cur_panel": ("panel", "base"), "cur_live": ("live", "base"), "R1": ("climax", "base"),
            "X1": ("live", "ma10"), "RX": ("climax", "ma10")}
PRIMARY = ("R1", "X1", "RX")
FILTER_REF = {"R1": "cur_live", "RX": "X1"}  # 被删笔数：相对同出场口径的 live 选股
COST_EXTRA = 0.03

_ORIG_MAW = PE.ma_break_window_for
EXIT_MODE = {"m": "base"}
PE.ma_break_window_for = lambda sid: (10 if (EXIT_MODE["m"] == "ma10" and sid == SID) else _ORIG_MAW(sid))


def fv(c, k):
    v = (c.get("feature_snapshot") or {}).get(k)
    if v is None:
        v = c.get(k)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def choppy(c) -> bool:  # yaml「震荡日停出」原条件
    uc, msc = fv(c, "up_count"), fv(c, "market_sample_count")
    return bool(msc and msc > 0 and uc is not None and msc * 0.35 <= uc < msc * 0.5)


def climax(c) -> bool:
    return c.get("market_regime") == "强牛·高潮"


SEL = {"panel": lambda c: True, "live": lambda c: not choppy(c), "climax": lambda c: not choppy(c) and not climax(c)}


def _d(x) -> str:
    return str(x)[:10]


def summarize(per_draw_rows, days, names, rm=None):
    out = _mean({v: _m(per_draw_rows[v], days) for v in names}, names)
    allr = [r for v in names for r in per_draw_rows[v]]
    nets = [r["net"] for r in allr]
    w = [x for x in nets if x > 0]; l = [x for x in nets if x < 0]
    out["avg_c30"] = round(out["avg"] - COST_EXTRA, 3)
    out["pf_pool"] = round(sum(w) / max(abs(sum(l)), 1e-9), 2) if nets else 0
    out["zero_loss_draws"] = sum(1 for v in names if per_draw_rows[v] and not any(r["net"] < 0 for r in per_draw_rows[v]))
    out["cost_gross_minus_net"] = round(statistics.fmean([r["gross"] - r["net"] for r in allr]), 3) if allr else None
    out["exit"] = {k: round(c / len(names), 1) for k, c in Counter(r["reason"] for r in allr).most_common()}
    if rm is not None:
        rr = [x for v in names for x in rm[v]]
        out["n_rm"] = round(len(rr) / len(names), 1)
        out["avg_rm"] = round(statistics.fmean(rr), 3) if rr else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/highrange_fix.json")
    a = ap.parse_args()
    pol = standalone_selection_policy(load_production_selection_policy(), SID, 3)
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    pw = panels(SID)
    res: dict = {"_meta": {"sid": SID, "draws": len(names), "panels": {w: str(f) for w, f in pw.items()}}}
    keep: dict = {}
    with SessionLocal() as db:
        for w in ORDER:
            p = json.loads(pw[w].read_text())
            _register_only(p)
            days = [d["signal_date"] for d in p["days"]]
            cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
            base = RULES[f"only:{SID}"]
            sel_rows: dict = {}
            vetoed = {"choppy_days": sorted({d["signal_date"] for d in p["days"] if any(choppy(c) for c in d["cards"])}),
                      "climax_days": sorted({d["signal_date"] for d in p["days"] if any(climax(c) for c in d["cards"])})}
            for sm, pred in SEL.items():
                sel_rows[sm] = {}
                for v in names:
                    def rule(cs, d, _v=v, _p=pred):
                        cs = [c for c in base(cs, d) if _p(c)]
                        return tiebreak(cs, _v, d["signal_date"])
                    _, rows = run_rule(rule, p, pol, {"output_count": 3, "score_line": 72.0}, apply_global_weights=False)
                    sel_rows[sm][v] = rows
            need: dict = defaultdict(set)
            for by in sel_rows.values():
                for rows in by.values():
                    for r in rows:
                        need[r["dt"]].add(r["code"])
            bars = fetch_bars(db, need)
            sim: dict = {}

            def sim_of(dt, code, em):
                k = (dt, code, em)
                if k not in sim:
                    EXIT_MODE["m"] = em
                    day = Day(); day.signal_date = dt
                    day.outcome_bars = {code: tuple(bars[dt].get(code, ())[:lim])}
                    tr = (PE._simulate_card(day, cards[(dt, code)], ep) or {}).get("trade")
                    EXIT_MODE["m"] = "base"
                    sim[k] = ({"net": float(tr.net_return_pct), "gross": float(tr.gross_return_pct),
                               "reason": str(tr.exit_reason), "ex": _d(tr.exit_date)}
                              if tr and tr.executed and tr.exit_date else None)
                return sim[k]

            kw: dict = {}
            for vn, (sm, em) in VARIANTS.items():
                kw[vn] = {}
                for v in names:
                    rows = []
                    for r in sel_rows[sm][v]:
                        s = sim_of(r["dt"], r["code"], em)
                        if s is not None:
                            rows.append({"dt": r["dt"], "code": r["code"], **s})
                    kw[vn][v] = rows
            rm: dict = {}
            for vn, ref in FILTER_REF.items():
                rm[vn] = {}
                for v in names:
                    mine = {(r["dt"], r["code"]) for r in kw[vn][v]}
                    rm[vn][v] = [r["net"] for r in kw[ref][v] if (r["dt"], r["code"]) not in mine]
            keep[w] = {"days": days, "kw": kw, "rm": rm}
            out = {vn: summarize(kw[vn], days, names, rm.get(vn)) for vn in VARIANTS}
            out["_veto_days"] = {k: len(v) for k, v in vetoed.items()}
            res[w] = out
            print(w, json.dumps({vn: {k: out[vn][k] for k in ("n", "avg", "pf", "mdd", "total")} for vn in VARIANTS},
                                ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        days = sorted(set(keep["12m"]["days"]) | set(keep["EXT"]["days"]))
        out = {}
        for vn in VARIANTS:
            comb = {v: keep["12m"]["kw"][vn][v] + keep["EXT"]["kw"][vn][v] for v in names}
            crm = ({v: keep["12m"]["rm"][vn][v] + keep["EXT"]["rm"][vn][v] for v in names} if vn in FILTER_REF else None)
            out[vn] = summarize(comb, days, names, crm)
        res["N3"] = out
        print("N3", json.dumps({vn: {k: out[vn][k] for k in ("n", "avg", "pf", "mdd", "total")} for vn in VARIANTS},
                               ensure_ascii=False), flush=True)

    verdict = {}
    cl = {w: res[w]["cur_live"] for w in ("12m", "N3", "W0", "W00")}
    for vn in PRIMARY:
        r12, n3 = res["12m"][vn], res["N3"][vn]
        c1 = (r12["avg"] > 0 and n3["avg"] > 0 and r12["avg_c30"] > 0 and n3["avg_c30"] > 0 and n3["avg"] > cl["N3"]["avg"])
        c2 = r12["pf"] >= 1.3 and n3["pf"] >= 1.3
        relaxed = c1 and c2
        c3 = (r12["n"] >= 15 and n3["n"] >= 30) if relaxed else (r12["n"] >= 30 and n3["n"] >= 60)
        c4 = r12["total"] > 0 and n3["total"] > 0
        c5 = all(res[w][vn]["avg"] > -5 and res[w][vn]["mdd"] >= cl[w]["mdd"] - 3.0 for w in ("W0", "W00"))
        verdict[vn] = {"c1_avg": c1, "c2_pf": c2, "c3_n": c3, "c3_relaxed": relaxed, "c4_total": c4, "c5_old": c5,
                       "pass": all((c1, c2, c3, c4, c5)), "n3_avg": n3["avg"]}
    passed = [vn for vn in PRIMARY if verdict[vn]["pass"]]
    verdict["_decision"] = ("adopt:" + max(passed, key=lambda vn: verdict[vn]["n3_avg"])) if passed else "retire"
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

"""Sprint4 §101：龙虎榜机构席位入场过滤。21 次选股平均，口径与 §94/§97/§99 相同。

预注册写于 /workspace/strategy-research.md §101（结果出来前写定，无网格、不调参）。
数据：Tushare top_inst（经统一数据网关拉取）按日缓存 output/research/sprint4/top_inst/YYYYMMDD.parquet
（scripts/fetch_top_inst_cache.py）。机构 = exalter == "机构专用"；同一 (code, 日) 按 (exalter, buy, sell) 去重后
对机构行 net_buy 求和。窗口 = T−2、T−1、T 三个交易日（T 日榜约 21:00 CST 入库，早于 T+1 开盘 → T+1 入场拦截）。
  L1 skip_inst_sell : 窗口内上榜 且 机构净买 < 0 → 跳过
  L2 need_inst_buy  : 窗口内上榜 且 机构净买 ≤ 0（含无机构席位）→ 跳过；未上榜保留
过滤语义对齐 gap_up_blocked：被过滤的选股不成交、不递补。
窗口 12m（≈R12）/ EXT 2025-01~09 / W0 / W00；"N3" = 12m+EXT 合并（每次抽样合并后算指标，再 21 次平均）。
采纳（全部满足）：1) 12m 与 N3 每笔 > cur；2) 12m 与 N3 PF ≥ 1.3；3) 12m 笔数 ≥ 30、N3 ≥ 60；4) 12m 与 N3 总收益 > 0；
5) W0 / W00 MDD 不比 cur 差超过 3 点；6) N3 被删且本会成交笔数（21 次平均）≥ 10。
"""
from __future__ import annotations
import argparse, bisect, json, os, statistics, sys
from collections import defaultdict
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import pandas as pd
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

CACHE = B / "output/research/sprint4/top_inst"
INST = "机构专用"
CTX: dict = {"lhb": {}, "cal": [], "w": {}}  # lhb: (yyyymmdd, code) -> inst_net_yi ; w cache


def load_lhb() -> dict:
    out: dict = {}
    for f in sorted(CACHE.glob("*.parquet")):
        df = pd.read_parquet(f)
        if df.empty:
            continue
        df["code"] = df["code"].astype(str).str.zfill(6)
        df = df.assign(_b=df["buy_amount_yi"].round(4), _s=df["sell_amount_yi"].round(4)) \
            .drop_duplicates(subset=["code", "institution", "_b", "_s"])
        d = f.stem
        inst = df[df["institution"] == INST].groupby("code")["net_buy_yi"].sum()
        for code in df["code"].unique():
            out[(d, code)] = float(inst.get(code, 0.0))
        out[(d, "__has__")] = 1.0
    return out


def window(dt: str, code: str):
    """返回 (on_list, inst_net) for T−2..T；缺榜日（缓存里没有该交易日）计数到 CTX['miss']。"""
    k = (dt, code)
    if k in CTX["w"]:
        return CTX["w"][k]
    cal = CTX["cal"]
    t = dt.replace("-", "")
    i = bisect.bisect_right(cal, t) - 1
    days = cal[max(0, i - 2): i + 1]
    on, net = False, 0.0
    for d in days:
        if (d, "__has__") not in CTX["lhb"]:
            CTX.setdefault("miss", set()).add(d)
        v = CTX["lhb"].get((d, code))
        if v is not None:
            on = True
            net += v
    CTX["w"][k] = (on, net)
    return on, net


VARIANTS = {
    "cur": lambda dt, code: True,
    "L1": lambda dt, code: not (window(dt, code)[0] and window(dt, code)[1] < 0),
    "L2": lambda dt, code: not (window(dt, code)[0] and window(dt, code)[1] <= 0),
}
PRIMARY = ("L1", "L2")


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
    ap.add_argument("--out", default="output/research/sprint4/lhb_entry_filter.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    res: dict = {}
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    from sqlalchemy import text
    with SessionLocal() as db:
        CTX["lhb"] = load_lhb()
        CTX["cal"] = sorted({str(r[0]).replace("-", "") for r in db.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
        print("lhb keys", len(CTX["lhb"]), "days", sum(1 for k in CTX["lhb"] if k[1] == "__has__"), flush=True)
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
            c2 = r12["pf"] >= 1.3 and n3["pf"] >= 1.3
            c3 = r12["n"] >= 30 and n3["n"] >= 60
            c4 = r12["total"] > 0 and n3["total"] > 0
            c5 = all(ws[w][vn]["mdd"] >= ws[w]["cur"]["mdd"] - 3.0 for w in ("W0", "W00") if w in ws)
            c6 = (n3["n_rm"] or 0) >= 10
            verdict[sid][vn] = {"c1_avg": c1, "c2_pf": c2, "c3_n": c3, "c4_total": c4, "c5_old_mdd": c5, "c6_rm": c6,
                                "pass": all((c1, c2, c3, c4, c5, c6)), "n3_lift": round(n3["avg"] - cur3["avg"], 3)}
        passed = [vn for vn in PRIMARY if verdict[sid][vn]["pass"]]
        verdict[sid]["_adopt"] = max(passed, key=lambda vn: verdict[sid][vn]["n3_lift"]) if passed else None
    res["_missing_lhb_days"] = sorted(CTX.get("miss", set()))
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

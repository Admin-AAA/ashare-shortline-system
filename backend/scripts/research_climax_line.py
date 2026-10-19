"""Sprint4 §121：L-070（非强牛·高潮日封线 120 → 强制空仓）按策略是否放开。21 次选股平均，口径同 §85–§88 / §94 / §104。

预注册写于 /workspace/strategy-research.md §121（2026-10-19 10:11 CST，结果出来前写定，无网格、不调参）。
  V0 现行 v27（高潮日线 120）
  V1 偏多·高潮→71.4 / 震荡·高潮→72.0 / 弱势·高潮→60.0（= 同 regime·活跃 的线），其余规则不变（高潮日最多 2 张）
  V2 同 V1 的线，高潮日每策略最多 1 张（通过线的最高分）
关键证据 = 高潮日新增成交单独统计（V0 当日 0 张）+ 策略整体变化。sector 另附 §104 C3 冷却后的整体（参考，不作门槛）。
采纳（全部满足）：1) 高潮日成交 R12/N3 每笔 > 0；2) PF ≥ 1.3（R12/N3）；3) 笔数 R12 ≥ 30、N3 ≥ 60；
4) 整体 MDD R12/N3 不差于 V0，W0/W00 不差超过 3 点；5) 整体总收益 R12/N3 > V0。都过取 N3 整体总收益增量大者。
"""
from __future__ import annotations
import argparse, bisect, json, os, statistics, sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

os.environ.pop("ASHARE_RESEARCH_EXTRA_SKILLS", None)
B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B))

import app.services.backtest.policy_experiment as PE
from app.db.session import SessionLocal
from app.engine.aggregator_pure import _market_base, _market_sentiment
from app.engine.production_policy import (SelectionPolicyContext, evaluate_selection_policy,
                                          load_production_selection_policy)
from app.services.standalone_strategies import standalone_selection_policy
from scripts.research_entry_rule_panel import RULES
from scripts.research_round2_single_skill_eval import _register_only
from scripts.research_stability_gates import panels
from scripts.research_exit_resim import fetch_bars, variant, Day
from scripts.research_tiebreak import LIVE_DAY, tiebreak
from scripts.research_trend_regime_gate import CARD
from scripts.research_cooldown_filter import blocked_days, _m, _mean

VARIANTS = ("V0", "V1", "V2")
PRIMARY = ("V1", "V2")
CLIMAX = ("偏多·高潮", "震荡·高潮", "弱势·高潮")
NORMAL_LINE = {"偏多·高潮": 71.4, "震荡·高潮": 72.0, "弱势·高潮": 60.0}
CHAIN = {"12m": "EXT", "W00": "W0"}
ORDER = ("EXT", "12m", "W0", "W00")
COOLDOWN_SIDS = {"sector-relative-strength-leader"}


def _d(x) -> str:
    return str(x)[:10].replace("-", "")


def combo(ms) -> str:
    return f"{_market_base(ms)}·{_market_sentiment(ms)}"


def v1_policy(pol):
    ov = dict(pol.regime_sentiment_line_overrides)
    for k in CLIMAX:
        assert ov.get(k) == 120.0, (k, ov.get(k))
        ov[k] = NORMAL_LINE[k]
    return replace(pol, regime_sentiment_line_overrides=tuple(ov.items()))


def select(rule, panel, pol, top1_climax: bool):
    rows = []
    for day in panel["days"]:
        cards = rule([dict(c) for c in day["cards"]], day)
        if not cards:
            continue
        res = evaluate_selection_policy(cards, SelectionPolicyContext(
            score_line=72.0, output_count=3, market_snapshot=dict(day["market_snapshot"]), risk_template="balanced",
            same_day_industry_counts=day["same_day_industry_counts"],
            same_day_recommendation_count=day["same_day_recommendation_count"]), pol)
        recs = sorted(res.recommendations, key=lambda c: -float(c.get("final_score") or 0))
        cl = combo(day["market_snapshot"]) in CLIMAX
        if cl and top1_climax:
            recs = recs[:1]
        for c in recs:
            code = str(c.get("code"))
            if day["net_by_code"].get(code) is None:
                continue
            rows.append({"dt": day["signal_date"], "code": code, "climax": cl})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sids", default="chao-gu-yang-jia-skill,chen-xiaoqun-skill,oversold-capital-reversal,"
                                      "sector-relative-strength-leader")
    ap.add_argument("--nrand", type=int, default=20)
    ap.add_argument("--out", default="output/research/sprint4/climax_line.json")
    a = ap.parse_args()
    base_pol = load_production_selection_policy()
    assert base_pol.version == 27, base_pol.version
    res: dict = {}
    ep = variant()
    lim = ep.entry_lookahead_days + ep.horizon_days + 2
    names = ["base"] + [f"rnd{i}" for i in range(a.nrand)]
    outp = Path(a.out); outp.parent.mkdir(parents=True, exist_ok=True)
    from sqlalchemy import text
    with SessionLocal() as db:
        cal = sorted({_d(r[0]) for r in db.execute(text("SELECT DISTINCT trade_date FROM daily_kbars WHERE code='000001'"))})
        tdi = {d: i for i, d in enumerate(cal)}

        def T(dt):
            t = _d(dt)
            return tdi.get(t, bisect.bisect_right(cal, t) - 1)

        for sid in a.sids.split(","):
            res[sid] = {}
            p0 = standalone_selection_policy(base_pol, sid, 3)
            pols = {"V0": p0, "V1": v1_policy(p0), "V2": v1_policy(p0)}
            pw = panels(sid)
            keep: dict = {}
            shadow_of: dict = {}
            for w in ORDER:
                if w not in pw:
                    continue
                p = json.loads(pw[w].read_text())
                _register_only(p)
                days = [d["signal_date"] for d in p["days"]]
                ncl = sum(1 for d in p["days"] if combo(d["market_snapshot"]) in CLIMAX)
                cards = {(d["signal_date"], c["code"]): c for d in p["days"] for c in d["cards"]}
                base = RULES[f"only:{sid}"]
                allrows = {vn: {} for vn in VARIANTS}
                for vn in VARIANTS:
                    for v in names:
                        def rule(cs, d, _v=v):
                            if LIVE_DAY[sid](d["market_snapshot"]):
                                return []
                            cs = base(cs, d)
                            if sid in CARD:
                                cs = [c for c in cs if CARD[sid](c)]
                            return tiebreak(cs, _v, d["signal_date"])
                        allrows[vn][v] = select(rule, p, pols[vn], vn == "V2")
                need: dict = defaultdict(set)
                for byv in allrows.values():
                    for rows in byv.values():
                        for r in rows:
                            need[r["dt"]].add(r["code"])
                bars = fetch_bars(db, need)
                del p
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

                kw = {vn: {} for vn in VARIANTS}; kc = {vn: {} for vn in VARIANTS}; kcd = {vn: {} for vn in VARIANTS}
                shadow_of[w] = {vn: {} for vn in VARIANTS}
                for vn in VARIANTS:
                    for v in names:
                        rows = []
                        for r in allrows[vn][v]:
                            s = sim_of(r["dt"], r["code"])
                            if s is not None:
                                rows.append({"dt": r["dt"], "code": r["code"], "net": s[0], "ex": s[2], "en": s[1],
                                             "climax": r["climax"]})
                        kw[vn][v] = rows
                        kc[vn][v] = [r for r in rows if r["climax"]]
                        if sid in COOLDOWN_SIDS:
                            sh = [(r["ex"], r["en"], r["code"], r["net"]) for r in rows]
                            shadow_of[w][vn][v] = sh
                            hist = (shadow_of.get(CHAIN[w], {}).get(vn, {}).get(v, []) if w in CHAIN else []) + sh
                            hist.sort()
                            blk = blocked_days(hist, cal)["C3"]
                            kcd[vn][v] = [r for r in rows if T(r["dt"]) not in blk]
                keep[w] = {"days": days, "kw": kw, "kc": kc, "kcd": kcd}
                out = {"_days": len(days), "_climax_days": ncl}
                for vn in VARIANTS:
                    out[vn] = _mean({v: _m(kw[vn][v], days) for v in names}, names)
                    if vn != "V0":
                        out[vn + "_climax"] = _mean({v: _m(kc[vn][v], days) for v in names}, names)
                    if sid in COOLDOWN_SIDS:
                        out[vn + "_cd"] = _mean({v: _m(kcd[vn][v], days) for v in names}, names)
                res[sid][w] = out
                print(sid, w, json.dumps(out, ensure_ascii=False), flush=True)
                outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
            if "12m" in keep and "EXT" in keep:
                days = sorted(set(keep["12m"]["days"]) | set(keep["EXT"]["days"]))
                out = {}
                for key, sfx in (("kw", ""), ("kc", "_climax"), ("kcd", "_cd")):
                    for vn in VARIANTS:
                        if key == "kc" and vn == "V0":
                            continue
                        if key == "kcd" and sid not in COOLDOWN_SIDS:
                            continue
                        out[vn + sfx] = _mean({v: _m(keep["12m"][key][vn][v] + keep["EXT"][key][vn][v], days)
                                               for v in names}, names)
                res[sid]["N3"] = out
                print(sid, "N3", json.dumps(out, ensure_ascii=False), flush=True)
            outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))

    verdict = {}
    for sid, ws in res.items():
        verdict[sid] = {}
        for vn in PRIMARY:
            c12, c3 = ws["12m"][vn + "_climax"], ws["N3"][vn + "_climax"]
            c1 = c12["avg"] > 0 and c3["avg"] > 0
            c2 = c12["pf"] >= 1.3 and c3["pf"] >= 1.3
            c3n = c12["n"] >= 30 and c3["n"] >= 60
            c4 = (ws["12m"][vn]["mdd"] >= ws["12m"]["V0"]["mdd"] and ws["N3"][vn]["mdd"] >= ws["N3"]["V0"]["mdd"]
                  and all(ws[w][vn]["mdd"] >= ws[w]["V0"]["mdd"] - 3.0 for w in ("W0", "W00") if w in ws))
            c5 = ws["12m"][vn]["total"] > ws["12m"]["V0"]["total"] and ws["N3"][vn]["total"] > ws["N3"]["V0"]["total"]
            verdict[sid][vn] = {"c1_avg": c1, "c2_pf": c2, "c3_n": c3n, "c4_mdd": c4, "c5_total": c5,
                                "pass": all((c1, c2, c3n, c4, c5)),
                                "n3_total_gain": round(ws["N3"][vn]["total"] - ws["N3"]["V0"]["total"], 1)}
        passed = [vn for vn in PRIMARY if verdict[sid][vn]["pass"]]
        verdict[sid]["_adopt"] = max(passed, key=lambda vn: verdict[sid][vn]["n3_total_gain"]) if passed else None
    res["_verdict"] = verdict
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("VERDICT", json.dumps(verdict, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

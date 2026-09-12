"""板块因子在【生产同构选中池】的三窗 canonical 终审（L-071，只读）。

两个假设（均为排序前硬否决、第 11 名递补，缺板块信息保守保留）：
 A 板块高潮拥挤：选中卡所属行业当日涨停家数处全行业截面 top tail（过热崩塌）。
 B 板块动量弱势：选中卡所属行业近5日动量处全行业截面 bottom tail（弱势行业不接力）。
三窗：冻结 _simulate_card 因果；前瞻 join btr_fa625；run3 join btr_2bec6fed。
度量逐笔等权 + 日 cohort（空仓记0）+ 配对日差 bootstrap CI + 被砍票机会成本。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json, _simulate_card
from scripts.research_canonical_two_window import (
    build_forward_canonical, build_market_snapshots, trade_metrics, daily_cohort,
    cohort_metrics, _bootstrap_ci)
from scripts.research_third_window import load_run3
OUT = B / "output" / "research"


def build_secmap():
    p = pd.read_pickle(OUT / "sector_factor_panel.pkl")
    # 行业级（每日每行业一行）
    sec = p[["trade_date", "industry", "sec_nlu", "sec_ret5"]].dropna(subset=["industry"]).drop_duplicates(
        ["trade_date", "industry"]).copy()
    sec["nlu_pct"] = sec.groupby("trade_date").sec_nlu.rank(pct=True)
    sec["m5_pct"] = sec.groupby("trade_date").sec_ret5.rank(pct=True)
    return {(r.trade_date, r.industry): (float(r.nlu_pct), float(r.m5_pct)) for r in sec.itertuples()}


def metrics(base, veto, cut, dates):
    base, veto, cut = pd.DataFrame(base), pd.DataFrame(veto), pd.DataFrame(cut)
    tb, tv = trade_metrics(base), trade_metrics(veto)
    cb, cv = daily_cohort(base, dates), daily_cohort(veto, dates)
    mb, mv = cohort_metrics(cb), cohort_metrics(cv)
    ch = (cv - cb); ch = ch[ch != 0]; lo, hi = _bootstrap_ci(ch.values)
    monthly = (cv - cb).groupby(lambda d: d[:7]).sum()
    cm = float(pd.DataFrame(cut).net.mean()) if len(cut) else float("nan")
    cpos = int((pd.DataFrame(cut).net > 0).sum()) if len(cut) else 0
    print(f"   逐笔 n{tb['n']}->{tv['n']} avg{tb['avg_net']:+.3f}->{tv['avg_net']:+.3f} "
          f"win{tb['win']:.3f}->{tv['win']:.3f} PF{tb['pf']}->{tv['pf']} | "
          f"cohort日均{mb['daily_avg']:+.4f}->{mv['daily_avg']:+.4f}")
    print(f"   配对Δ日{len(ch)} Δ均{ch.mean():+.4f} CI[{lo:+.4f},{hi:+.4f}] better-day{(ch>0).mean():.2f} "
          f"月正{(monthly>0).sum()}-{(monthly<0).sum()} | 被砍{len(cut)}均{cm:+.3f}/正期望{cpos}")


def frozen_arms(tox, pol, ep):
    snap = ScoredCandidateSnapshot.from_payload(json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    base, veto, cut = [], [], []
    for day in snap.days:
        cards = _thaw_json(day.cards); cm = {str(c.get("code")): c for c in cards}
        r0 = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations
        r1 = evaluate_selection_policy([c for c in cards if not tox(day.signal_date, c)],
                                       SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations
        s0, s1 = {str(c.get("code")) for c in r0}, {str(c.get("code")) for c in r1}
        def nt(code):
            tr = (_simulate_card(day, cm[code], ep) or {}).get("trade")
            return float(tr.net_return_pct) if (tr and tr.executed) else None
        for code in s0:
            v = nt(code);  base.append({"dt": day.signal_date, "net": v}) if v is not None else None
        for code in s1:
            v = nt(code);  veto.append({"dt": day.signal_date, "net": v}) if v is not None else None
        for code in s0 - s1:
            v = nt(code);  cut.append({"dt": day.signal_date, "net": v}) if v is not None else None
    dates = sorted({x["dt"] for x in base} | {x["dt"] for x in veto})
    return base, veto, cut, dates


def canon_arms(fw, tox, pol):
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]; fw = fw[fw.dt.isin(have)]
    base, veto, cut = [], [], []
    for d, gp in fw.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry),
                  "signal_price": 10.0} for r in gp.itertuples()]
        net = {str(r.code): float(r.net) for r in gp.itertuples()}
        r0 = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(snaps[d])), pol).recommendations
        r1 = evaluate_selection_policy([c for c in cards if not tox(d, c)],
                                       SelectionPolicyContext(72., 10, dict(snaps[d])), pol).recommendations
        s0, s1 = {str(c.get("code")) for c in r0}, {str(c.get("code")) for c in r1}
        for code in s0:
            if code in net: base.append({"dt": d, "net": net[code]})
        for code in s1:
            if code in net: veto.append({"dt": d, "net": net[code]})
        for code in s0 - s1:
            if code in net: cut.append({"dt": d, "net": net[code]})
    dates = sorted({x["dt"] for x in base} | {x["dt"] for x in veto})
    return base, veto, cut, dates


def main():
    seca = build_secmap(); pol = load_production_selection_policy(version=2); ep = ExecutionPolicy()
    fwd = build_forward_canonical(); run3 = load_run3()
    for kind, idx, thr, tag in [("A拥挤top", 0, 0.90, "nlu_pct>0.90"),
                                ("A拥挤top", 0, 0.95, "nlu_pct>0.95"),
                                ("B弱势bottom", 1, 0.10, "m5_pct<0.10"),
                                ("B弱势bottom", 1, 0.05, "m5_pct<0.05")]:
        print(f"\n########## 假设{kind} {tag} ##########")
        def tox(dt, c, idx=idx, thr=thr, kind=kind):
            ind = c.get("industry"); v = seca.get((dt, str(ind)))
            if v is None: return False
            x = v[idx]
            return x > thr if kind.startswith("A") else x < thr
        b, v, cu, dt = frozen_arms(tox, pol, ep); print(" [冻结]"); metrics(b, v, cu, dt)
        b, v, cu, dt = canon_arms(fwd.copy(), tox, pol); print(" [前瞻]"); metrics(b, v, cu, dt)
        b, v, cu, dt = canon_arms(run3.copy(), tox, pol); print(" [run3]"); metrics(b, v, cu, dt)


if __name__ == "__main__":
    main()

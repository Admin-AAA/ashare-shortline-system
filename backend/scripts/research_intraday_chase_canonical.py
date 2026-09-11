"""尾盘追高否决的【生产同构 canonical】双窗口终审（L-071，只读）。

对每个信号日跑两次真实 evaluate_selection_policy：
- baseline：全部候选卡（v2 政策）
- veto：剔除 (cell∈CELLS 且 pm_ret>T) 的卡后再选（生产等价于在排序前硬否决，第11名递补）
冻结：_simulate_card→simulation.simulate 真实因果出场；前瞻：join canonical run 净收益。
缺 pm_ret 的卡保守保留（两臂一致，不产生差异）。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import (
    ScoredCandidateSnapshot, _thaw_json, _simulate_card)
from scripts.research_canonical_two_window import (
    build_forward_canonical, build_market_snapshots, trade_metrics, daily_cohort,
    cohort_metrics, _bootstrap_ci, SLEEVE)
OUT = B / "output" / "research"
CELLS = {"弱势·活跃", "强牛·高潮"}


def frozen_arms(pm: dict, T: float):
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    ep = ExecutionPolicy(); pol = load_production_selection_policy(version=2)
    base_rows, veto_rows, cut_rows = [], [], []
    for day in snap.days:
        cards = _thaw_json(day.cards)
        b, se = _market_base(day.market_snapshot), _market_sentiment(day.market_snapshot)
        key = f"{b}·{se}"
        def is_toxic(c):
            v = pm.get((day.signal_date, str(c.get("code"))))
            return v is not None and key in CELLS and v > T
        r0 = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol)
        keep_cards = [c for c in cards if not is_toxic(c)]
        r1 = evaluate_selection_policy(keep_cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol)
        sel0 = {str(c.get("code")) for c in r0.recommendations}
        sel1 = {str(c.get("code")) for c in r1.recommendations}
        cardmap = {str(c.get("code")): c for c in cards}
        def emit(codes, rows):
            for code in codes:
                oc = _simulate_card(day, cardmap[code], ep)
                tr = oc.get("trade")
                if tr is None or not tr.executed: continue
                rows.append({"dt": day.signal_date, "code": code, "net": float(tr.net_return_pct), "key": key})
        emit(sel0, base_rows); emit(sel1, veto_rows)
        for code in sel0 - sel1:
            oc = _simulate_card(day, cardmap[code], ep); tr = oc.get("trade")
            if tr and tr.executed:
                cut_rows.append({"dt": day.signal_date, "code": code, "net": float(tr.net_return_pct), "key": key})
    return map(pd.DataFrame, (base_rows, veto_rows, cut_rows))


def forward_arms(pm: dict, T: float):
    fw = build_forward_canonical()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]; fw = fw[fw.dt.isin(have)]
    pol = load_production_selection_policy(version=2)
    base_rows, veto_rows, cut_rows = [], [], []
    for d, gp in fw.groupby("dt"):
        snap = snaps[d]; b, se = _market_base(snap), _market_sentiment(snap); key = f"{b}·{se}"
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry),
                  "signal_price": 10.0} for r in gp.itertuples()]
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        def toxic(c):
            v = pm.get((d, str(c.get("code"))))
            return v is not None and key in CELLS and v > T
        r0 = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, dict(snap)), pol)
        r1 = evaluate_selection_policy([c for c in cards if not toxic(c)],
                                       SelectionPolicyContext(72., 10, dict(snap)), pol)
        s0 = {str(c.get("code")) for c in r0.recommendations}
        s1 = {str(c.get("code")) for c in r1.recommendations}
        for codes, rows in ((s0, base_rows), (s1, veto_rows)):
            for code in codes:
                if code in netmap:
                    rows.append({"dt": d, "code": code, "net": netmap[code], "key": key})
        for code in s0 - s1:
            if code in netmap: cut_rows.append({"dt": d, "code": code, "net": netmap[code], "key": key})
    return map(pd.DataFrame, (base_rows, veto_rows, cut_rows))


def report(win, base, veto, cut, all_dates):
    tb, tv = trade_metrics(base), trade_metrics(veto)
    cb, cv = daily_cohort(base, all_dates), daily_cohort(veto, all_dates)
    mb, mv = cohort_metrics(cb), cohort_metrics(cv)
    chg = (cv - cb); ch = chg[chg != 0]; lo, hi = _bootstrap_ci(ch.values)
    monthly = chg.groupby(lambda d: d[:7]).sum()
    print(f" [{win}] 逐笔 n{tb['n']}->{tv['n']} avg{tb['avg_net']:+.4f}->{tv['avg_net']:+.4f} "
          f"win{tb['win']:.3f}->{tv['win']:.3f} PF{tb['pf']}->{tv['pf']}")
    print(f"        日cohort cum{mb['cum']:+.2f}->{mv['cum']:+.2f} MDD{mb['mdd']:+.2f}->{mv['mdd']:+.2f} "
          f"日均{mb['daily_avg']:+.4f}->{mv['daily_avg']:+.4f}")
    print(f"        配对变化日{len(ch)} Δ均{ch.mean():+.4f} CI[{lo:+.4f},{hi:+.4f}] "
          f"better-day{(ch>0).mean():.2f} 月正{(monthly>0).sum()}-{(monthly<0).sum()}")
    if len(cut):
        print(f"        被砍{len(cut)}笔 均{cut.net.mean():+.4f} win{(cut.net>0).mean():.3f} "
              f"分cell: " + " ".join(f"{k}{g.net.mean():+.2f}/n{len(g)}" for k, g in cut.groupby("key")))
    return dict(tb=tb, tv=tv, mb=mb, mv=mv, ci=(lo, hi), cut_n=len(cut), cut_mean=cut.net.mean() if len(cut) else None)


def main():
    pmf = pd.read_pickle(OUT / "pmret_lookup_frozen.pkl")
    pmw = pd.read_pickle(OUT / "pmret_lookup_forward.pkl")
    summary = {}
    for T in (1.25, 1.5, 1.75):
        print(f"\n================= pm_ret 阈值 T={T} =================")
        fb, fv, fc = frozen_arms(pmf, T)
        fd = sorted(set(fb.dt) | set(fv.dt))
        rz = report("FROZEN", fb, fv, fc, fd)
        wb, wv, wc = forward_arms(pmw, T)
        wd = sorted(set(wb.dt) | set(wv.dt))
        rw = report("FORWARD", wb, wv, wc, wd)
        summary[f"T{T}"] = {"frozen": {k: rz[k] for k in ("tb", "tv", "mb", "mv", "ci", "cut_n", "cut_mean")},
                            "forward": {k: rw[k] for k in ("tb", "tv", "mb", "mv", "ci", "cut_n", "cut_mean")}}
    (OUT / "intraday_chase_canonical.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print("\nsaved intraday_chase_canonical.json")


if __name__ == "__main__":
    main()

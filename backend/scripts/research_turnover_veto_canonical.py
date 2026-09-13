"""高换手（过热）否决的生产同构双窗口因果检验（只读）。

机制：全市场日截面 turnover_rate 分位 pct∈[0,1]；若候选卡 pct>thr 则在
evaluate_selection_policy 之前剔除，再按 final_score 自然递补（与生产选择同构）。
对照 baseline(v2) vs veto arm：逐笔等权/日cohort等权(空仓记0)/胜率/盈亏比/
配对日差 bootstrap95CI/逐月同号/被砍正期望票机会成本。

turnover 来自 daily_basic（T 收盘已知，无前视），分位用全市场日截面排名。
冻结=holdout_244d 快照 + _simulate_card 因果出场；前瞻=canonical run 净收益。
"""
from __future__ import annotations
import json, sys, pickle
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import (
    load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext)
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json, _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import (
    build_forward_canonical, build_market_snapshots, _bootstrap_ci)

OUT = B / "output" / "research"
THRS = [0.95, 0.93, 0.90, 0.88, 0.85]


def turnover_pct_lookup() -> dict:
    """(date,code)->当日全市场换手分位。"""
    panel = pickle.load(open(OUT / "newfactor_fullmarket.pkl", "rb"))
    x = panel[["trade_date", "code", "turnover_rate"]].dropna()
    x["pct"] = x.groupby("trade_date").turnover_rate.rank(pct=True)
    return {(r.trade_date, str(r.code)): float(r.pct) for r in x.itertuples()}


def _metrics(rows: list[dict], all_dates):
    if not rows:
        return {"n": 0, "avg": 0.0, "win": 0.0, "pf": float("nan"), "cohort": 0.0, "days": len(all_dates)}
    df = pd.DataFrame(rows)
    win = (df.net > 0).mean()
    gp, gl = df.net[df.net > 0].sum(), abs(df.net[df.net < 0].sum())
    pf = gp / gl if gl > 1e-9 else float("inf")
    g = df.groupby("dt").net.mean().reindex(sorted(all_dates)).fillna(0.0)
    return {"n": len(df), "avg": df.net.mean(), "win": win, "pf": pf,
            "cohort": g.mean(), "days": len(g), "g": g}


def frozen_arm(pctmap, thr, pol, ep):
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    all_dates = sorted(d.signal_date for d in snap.days)
    base, veto, removed, promoted = [], [], [], []
    for day in snap.days:
        dt = day.signal_date
        cards = _thaw_json(day.cards)
        tox = lambda c: (v := pctmap.get((dt, str(c.get("code"))))) is not None and v > thr
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(
            cards, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations}
        keep = [c for c in cards if not tox(c)]
        s1 = {str(c.get("code")) for c in evaluate_selection_policy(
            keep, SelectionPolicyContext(72., 10, day.market_snapshot), pol).recommendations}
        cm = {str(c.get("code")): c for c in cards}
        for code in s0:
            tr = (_simulate_card(day, cm[code], ep) or {}).get("trade")
            if tr and tr.executed:
                base.append({"dt": dt, "code": code, "net": float(tr.net_return_pct)})
        for code in s1:
            tr = (_simulate_card(day, cm[code], ep) or {}).get("trade")
            if tr and tr.executed:
                veto.append({"dt": dt, "code": code, "net": float(tr.net_return_pct)})
        for code in s0 - s1:
            tr = (_simulate_card(day, cm[code], ep) or {}).get("trade")
            if tr and tr.executed: removed.append(float(tr.net_return_pct))
        for code in s1 - s0:
            tr = (_simulate_card(day, cm[code], ep) or {}).get("trade")
            if tr and tr.executed: promoted.append(float(tr.net_return_pct))
    return all_dates, base, veto, np.array(removed), np.array(promoted)


def forward_arm(pctmap, thr, pol):
    fw = build_forward_canonical(); dates = sorted(fw.dt.unique())
    snaps = build_market_snapshots(dates); have = [d for d in dates if d in snaps]
    fw = fw[fw.dt.isin(have)]
    base, veto, removed, promoted = [], [], [], []
    for d, gp in fw.groupby("dt"):
        snap = snaps[d]
        cards = [{"code": str(r.code), "final_score": float(r.score),
                  "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        net = {str(r.code): float(r.net) for r in gp.itertuples()}
        tox = lambda c: (v := pctmap.get((d, str(c.get("code"))))) is not None and v > thr
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(
            cards, SelectionPolicyContext(72., 10, dict(snap)), pol).recommendations}
        s1 = {str(c.get("code")) for c in evaluate_selection_policy(
            [c for c in cards if not tox(c)], SelectionPolicyContext(72., 10, dict(snap)), pol).recommendations}
        for code in s0:
            if code in net: base.append({"dt": d, "code": code, "net": net[code]})
        for code in s1:
            if code in net: veto.append({"dt": d, "code": code, "net": net[code]})
        for code in s0 - s1:
            if code in net: removed.append(net[code])
        for code in s1 - s0:
            if code in net: promoted.append(net[code])
    return have, base, veto, np.array(removed), np.array(promoted)


def report(nm, all_dates, base, veto, rem, pro):
    mb, mv = _metrics(base, all_dates), _metrics(veto, all_dates)
    j = pd.concat([mb["g"].rename("b"), mv["g"].rename("v")], axis=1).fillna(0.0)
    dd = (j.v - j.b)
    (lo, hi) = _bootstrap_ci(dd.values, draws=5000)
    monthly = dd.groupby(dd.index.str[:7]).mean()
    samepos = (np.sign(monthly) > 0).mean()
    npos_removed = int((rem > 0).sum()) if len(rem) else 0
    print(f"\n[{nm}]  逐笔 base n{mb['n']} 均{mb['avg']:+.3f} 胜{mb['win']:.3f} PF{mb['pf']:.2f} | "
          f"veto n{mv['n']} 均{mv['avg']:+.3f} 胜{mv['win']:.3f} PF{mv['pf']:.2f}")
    print(f"     日cohort base {mb['cohort']:+.4f} -> veto {mv['cohort']:+.4f} (Δ{mv['cohort']-mb['cohort']:+.4f}); "
          f"配对日差 bootstrap95CI[{lo:+.4f},{hi:+.4f}] 逐月正占比{samepos:.2f}")
    if len(rem):
        print(f"     被砍 n{len(rem)} 均{rem.mean():+.3f} 胜{(rem>0).mean():.3f}(其中正期望{npos_removed}张,机会成本) | "
              f"递补 n{len(pro)} 均{pro.mean() if len(pro) else float('nan'):+.3f} 胜{(pro>0).mean() if len(pro) else float('nan'):.3f}")
    return {"mb": mb, "mv": mv, "lo": lo, "hi": hi, "samepos": samepos}


def main():
    pctmap = turnover_pct_lookup()
    print("turnover pct lookup:", len(pctmap))
    pol = load_production_selection_policy(version=2); ep = ExecutionPolicy()
    for thr in THRS:
        print(f"\n================= 高换手否决阈值 top{int(round((1-thr)*100))}% (pct>{thr}) =================")
        ad, b, v, r, p = frozen_arm(pctmap, thr, pol, ep)
        report("冻结", ad, b, v, r, p)
        ad, b, v, r, p = forward_arm(pctmap, thr, pol)
        report("前瞻", ad, b, v, r, p)


if __name__ == "__main__":
    main()

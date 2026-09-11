"""机制诊断：被砍票 vs 递补票 + 按日聚类逐笔自助（只读）。
否决的净效果 = 用"递补票"替换"被砍票"；仅当被砍显著差于递补才改善。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json, _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots, _bootstrap_ci
OUT = B / "output" / "research"
CELLS = {"弱势·活跃", "强牛·高潮"}
T = 1.25


def cluster_boot_day(df_base, df_veto, draws=5000, seed=11):
    """按日聚类：每天 veto 均 - base 均，对天自助（仅两臂出票不同的天）。"""
    a = df_base.groupby("dt").net.mean(); b = df_veto.groupby("dt").net.mean()
    j = pd.concat([a.rename("a"), b.rename("v")], axis=1).dropna()
    d = (j.v - j.a).values
    return _bootstrap_ci(d, draws=draws, seed=seed), d


def frozen():
    pm = pd.read_pickle(OUT / "pmret_lookup_frozen.pkl")
    snap = ScoredCandidateSnapshot.from_payload(json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    ep = ExecutionPolicy(); pol = load_production_selection_policy(version=2)
    removed, promoted = [], []
    for day in snap.days:
        cards = _thaw_json(day.cards)
        b, se = _market_base(day.market_snapshot), _market_sentiment(day.market_snapshot); key = f"{b}·{se}"
        tox = lambda c: (v := pm.get((day.signal_date, str(c.get("code"))))) is not None and key in CELLS and v > T
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, SelectionPolicyContext(72.,10,day.market_snapshot),pol).recommendations}
        keep = [c for c in cards if not tox(c)]
        s1 = {str(c.get("code")) for c in evaluate_selection_policy(keep, SelectionPolicyContext(72.,10,day.market_snapshot),pol).recommendations}
        cm = {str(c.get("code")): c for c in cards}
        for code in s0 - s1:
            oc = _simulate_card(day, cm[code], ep); tr = oc.get("trade")
            if tr and tr.executed: removed.append(float(tr.net_return_pct))
        for code in s1 - s0:
            oc = _simulate_card(day, cm[code], ep); tr = oc.get("trade")
            if tr and tr.executed: promoted.append(float(tr.net_return_pct))
    return np.array(removed), np.array(promoted)


def forward():
    pm = pd.read_pickle(OUT / "pmret_lookup_forward.pkl")
    fw = build_forward_canonical(); dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    have = [d for d in dates if d in snaps]; fw = fw[fw.dt.isin(have)]; pol = load_production_selection_policy(version=2)
    removed, promoted = [], []
    for d, gp in fw.groupby("dt"):
        snap = snaps[d]; b, se = _market_base(snap), _market_sentiment(snap); key = f"{b}·{se}"
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry), "signal_price": 10.0} for r in gp.itertuples()]
        net = {str(r.code): float(r.net) for r in gp.itertuples()}
        tox = lambda c: (v := pm.get((d, str(c.get("code"))))) is not None and key in CELLS and v > T
        s0 = {str(c.get("code")) for c in evaluate_selection_policy(cards, SelectionPolicyContext(72.,10,dict(snap)),pol).recommendations}
        s1 = {str(c.get("code")) for c in evaluate_selection_policy([c for c in cards if not tox(c)],SelectionPolicyContext(72.,10,dict(snap)),pol).recommendations}
        for code in s0 - s1:
            if code in net: removed.append(net[code])
        for code in s1 - s0:
            if code in net: promoted.append(net[code])
    return np.array(removed), np.array(promoted)


def show(name, rem, pro):
    print(f"\n[{name}] T={T}  被砍 n={len(rem)} 均{rem.mean():+.3f}/胜{(rem>0).mean():.3f}   递补 n={len(pro)} 均{pro.mean():+.3f}/胜{(pro>0).mean():.3f}")
    # 配对替换增益：被砍-递补（负=砍得对）。按可用长度随机配对的均值自助
    rng = np.random.default_rng(7)
    diffs = []
    n = min(len(rem), len(pro))
    for _ in range(5000):
        rr = rem[rng.integers(0, len(rem), n)]; pp = pro[rng.integers(0, len(pro), n)]
        diffs.append((pp - rr).mean())  # 递补-被砍，正=替换改善
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    print(f"   替换增益(递补-被砍)均{pro.mean()-rem.mean():+.3f} bootstrap95 CI[{lo:+.3f},{hi:+.3f}]")


def main():
    r, p = frozen(); show("FROZEN", r, p)
    r, p = forward(); show("FORWARD", r, p)


if __name__ == "__main__":
    main()

"""构建 v2 基线【选中交易】明细表（三窗口），附 final_score、regime、多个风险因子的当日全市场分位。
冻结窗因果 simulate 只跑一次并缓存；前瞻/三窗用 canonical 净收益。供离线冗余/聚集/稳健性分析。"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np, pandas as pd

B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.production_policy import load_production_selection_policy, evaluate_selection_policy, SelectionPolicyContext
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot, _thaw_json, _simulate_card
from app.engine.backtest import ExecutionPolicy
from scripts.research_canonical_two_window import build_forward_canonical, build_market_snapshots
from scripts.research_third_window import load_run3

OUT = B / "output" / "research"
FACTORS = ["ivol20", "vol20", "max1_20", "skew20", "ret60", "beta20", "amp20"]


def pct_maps():
    p = pd.read_pickle(OUT / "anomaly_panel.pkl")[["trade_date", "code"] + FACTORS]
    maps = {}
    for f in FACTORS:
        x = p[["trade_date", "code", f]].dropna()
        x["pct"] = x.groupby("trade_date")[f].rank(pct=True)
        maps[f] = {(r.trade_date, str(r.code)): float(r.pct) for r in x.itertuples()}
    return maps


class _Snap:
    def __init__(self, ms): self.market_snapshot = dict(ms)


def rows_for(groups, maps, ep, w):
    pol = load_production_selection_policy(version=2)
    out = []
    for dt, cards, netmap, dayobj in groups:
        ms = dayobj.market_snapshot
        sel = evaluate_selection_policy(cards, SelectionPolicyContext(72., 10, ms), pol).recommendations
        cm = {str(c.get("code")): c for c in cards}
        b = _market_base(ms); se = _market_sentiment(ms)
        for c in sel:
            code = str(c.get("code"))
            if netmap is not None:
                net = netmap.get(code)
            else:
                tr = (_simulate_card(dayobj, cm[code], ep) or {}).get("trade")
                net = float(tr.net_return_pct) if (tr is not None and tr.executed) else None
            if net is None: continue
            row = {"w": w, "dt": dt, "code": code, "net": net,
                   "score": float(c.get("final_score") or cm[code].get("final_score") or 0),
                   "base": b, "sent": se}
            for f in FACTORS:
                row[f + "_pct"] = maps[f].get((dt, code), np.nan)
            out.append(row)
    return out


def frozen_groups():
    import json
    snap = ScoredCandidateSnapshot.from_payload(json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    return [(d.signal_date, _thaw_json(d.cards), None, d) for d in snap.days]


def canon_groups(which):
    fw = build_forward_canonical() if which == "fwd" else load_run3()
    dates = sorted(fw.dt.unique()); snaps = build_market_snapshots(dates)
    fw = fw[fw.dt.isin([d for d in dates if d in snaps])]
    g = []
    for d, gp in fw.groupby("dt"):
        cards = [{"code": str(r.code), "final_score": float(r.score), "industry": str(r.industry), "signal_price": 10.0}
                 for r in gp.itertuples()]
        netmap = {str(r.code): float(r.net) for r in gp.itertuples()}
        g.append((d, cards, netmap, _Snap(snaps[d])))
    return g


def main():
    maps = pct_maps(); ep = ExecutionPolicy()
    r1 = rows_for(frozen_groups(), maps, ep, "W1")
    r2 = rows_for(canon_groups("fwd"), maps, None, "W2")
    r3 = rows_for(canon_groups("third"), maps, None, "W3")
    df = pd.DataFrame(r1 + r2 + r3)
    df.to_pickle(OUT / "selected_factor_table.pkl")
    print("saved selected_factor_table.pkl rows=", len(df))
    print(df.w.value_counts())


if __name__ == "__main__":
    main()

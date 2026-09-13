"""深挖 震荡·平淡（及对照格）在冻结窗的【全候选反事实】分布 + 前瞻选中明细（只读）。

选中层冻结窗 0 笔震荡·平淡，需看全候选在各分数档的因果净收益，判断该格是否两窗口皆毒。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import load_production_selection_policy
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import (
    ProductionPolicyBacktestRunner, ScoredCandidateSnapshot, _purged_split,
    CanonicalPolicyExperimentSpec, _thaw_json)

OUT = B / "output" / "research"


def frozen_all_candidates():
    v1 = load_production_selection_policy(version=1)
    snap = ScoredCandidateSnapshot.from_payload(
        json.loads((B / "output/frozen_snapshots/holdout_244d.json").read_text()))
    ep = ExecutionPolicy()
    split = _purged_split(tuple(d.signal_date for d in snap.days),
                          purge_days=ep.entry_lookahead_days + ep.horizon_days)
    date2split = {dt: w for w, ds in split.items() for dt in ds}
    runner = ProductionPolicyBacktestRunner(repository=None)
    spec = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
    for k, val in [("candidate_snapshot", snap), ("score_line", 72.),
                   ("output_count", 10), ("execution_policy", ep)]:
        object.__setattr__(spec, k, val)
    _, rec = runner._run_arm(spec, v1, split)
    rows = []
    for day in snap.days:
        mb = _market_base(day.market_snapshot); se = _market_sentiment(day.market_snapshot)
        for card in _thaw_json(day.cards):
            code = str(card.get("code") or "")
            key = f"{day.signal_date}:{code}"
            oc = rec["outcomes"].get(key) or {}
            tr = oc.get("trade")
            if tr is None or not tr.executed:
                continue
            rows.append({"dt": day.signal_date, "code": code,
                         "score": float(card.get("final_score") or 0),
                         "key": f"{mb}·{se}", "base": mb, "sent": se,
                         "net": float(tr.net_return_pct), "selected": key in rec["selected_keys"],
                         "split": date2split.get(day.signal_date, "?")})
    return pd.DataFrame(rows)


def main():
    df = frozen_all_candidates()
    df.to_pickle(OUT / "canon_frozen_all_candidates.pkl")
    print("frozen all-executed candidates:", len(df), "days", df.dt.nunique())
    buckets = [(0, 60), (60, 68), (68, 72), (72, 76), (76, 82), (82, 200)]
    for cell in ["震荡·平淡", "震荡·活跃", "震荡·高潮", "偏多·平淡", "弱势·平淡", "强牛·平淡"]:
        sub = df[df.key == cell]
        if not len(sub):
            print(f"\n## {cell}: 冻结全候选 0 笔")
            continue
        print(f"\n## {cell}: 冻结全候选 n={len(sub)} 均{sub.net.mean():+.3f} win{(sub.net>0).mean():.2f}")
        for lo, hi in buckets:
            z = sub[(sub.score >= lo) & (sub.score < hi)]
            if len(z):
                sel = z[z.selected]
                print(f"   [{lo:>3},{hi if hi<200 else '+'}) n{len(z):>4} 均{z.net.mean():+.3f} win{(z.net>0).mean():.2f} | 其中选中 n{len(sel)}"
                      + (f" 均{sel.net.mean():+.3f}" if len(sel) else ""))
    # 前瞻 震荡·平淡 选中明细
    fw = pd.read_pickle(OUT / "canon_forward_v1_selected.pkl")
    zd = fw[fw.key == "震荡·平淡"].sort_values("dt")
    print("\n===== 前瞻 震荡·平淡 选中明细（", len(zd), "笔 ）=====")
    print(zd.to_string(index=False))
    print("涉及交易日:", sorted(zd.dt.unique()), "天数", zd.dt.nunique())


if __name__ == "__main__":
    main()

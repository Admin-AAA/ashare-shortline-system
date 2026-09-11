"""终版 production_selection.v2 双窗口证据落盘（只读）。
冻结244d canonical(evaluate_selection_policy+simulate, 无bug) + 前瞻117d DB canonical run。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import pandas as pd
B = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(B))
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import load_production_selection_policy
from app.engine.aggregator import _market_base, _market_sentiment
from app.services.backtest.policy_experiment import (ProductionPolicyBacktestRunner,
    ScoredCandidateSnapshot, _purged_split, CanonicalPolicyExperimentSpec)
from scripts.research_forward_validation import build_forward, build_market_snapshots

OUT = B / "output" / "research"
ev = {"note": "v2=保留L-060活跃/强牛高潮放行, 硬否决偏多/震荡/弱势·高潮(120); v1不可变",
      "frozen_window": "2025-01-15~2026-01-16 (244d, production-rule-replay snapshot)",
      "forward_window": "2026-02-09~2026-08-05 (117d, canonical run btr_fa625, score>=70)"}

# ---- frozen canonical ----
v1 = load_production_selection_policy(version=1); v2 = load_production_selection_policy(version=2)
snap = ScoredCandidateSnapshot.from_payload(json.loads((B/"output/frozen_snapshots/holdout_244d.json").read_text()))
ep = ExecutionPolicy(); split = _purged_split(tuple(d.signal_date for d in snap.days), purge_days=ep.entry_lookahead_days+ep.horizon_days)
runner = ProductionPolicyBacktestRunner(repository=None); spec = CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
for k, val in [("candidate_snapshot",snap),("score_line",72.),("output_count",10),("execution_policy",ep)]: object.__setattr__(spec,k,val)
mbse = {d.signal_date:(_market_base(d.market_snapshot),_market_sentiment(d.market_snapshot)) for d in snap.days}
def frame(pol):
    _,rec = runner._run_arm(spec,pol,split); rows=[]
    for key in rec["selected_keys"]:
        dt,code=key.split(":",1); tr=(rec["outcomes"].get(key) or {}).get("trade")
        if tr is None or not tr.executed: continue
        mb,se=mbse[dt]; rows.append((dt,mb,se,float(tr.net_return_pct)))
    return pd.DataFrame(rows,columns=["date","mb","se","net"])
def block(f,w):
    ff=f[f.date.isin(set(split[w]))]; dc=ff.groupby("date").net.mean()
    wsum,lsum=ff.net[ff.net>0].sum(),abs(ff.net[ff.net<0].sum())
    return {"n":int(len(ff)),"avg_net_pct":round(ff.net.mean(),4),"daily_cohort_avg_pct":round(dc.mean(),4),
            "win_rate":round((ff.net>0).mean(),4),"profit_factor":round(wsum/lsum,3) if lsum else None}
fz={}
for name,pol in [("v1",v1),("v2",v2)]:
    f=frame(pol); fz[name]={w:block(f,w) for w in ("train","validation","out_of_sample")}
    fz[name]["ALL"]=block(f,"train")  # placeholder replaced below
    fz[name]["ALL"]={"n":int(len(f)),"avg_net_pct":round(f.net.mean(),4),
                     "daily_cohort_avg_pct":round(f.groupby('date').net.mean().mean(),4),
                     "win_rate":round((f.net>0).mean(),4)}
ev["frozen_canonical"]=fz

# ---- forward canonical cells ----
tr=build_forward(); dates=sorted(tr.signal_date.unique()); snaps=build_market_snapshots(dates)
tr=tr[tr.signal_date.isin(snaps)].copy()
tr["base"]=tr.signal_date.map(lambda d:_market_base(snaps.get(d)))
tr["sent"]=tr.signal_date.map(lambda d:_market_sentiment(snaps.get(d)))
tr["key"]=tr.base+"·"+tr.sent
cells={}
for k,c in tr.groupby("key"):
    cells[k]={"n":int(len(c)),"avg_net_pct":round(c.net.mean(),4),"win_rate":round((c.net>0).mean(),4)}
ev["forward_cells_score_ge70"]=cells
(OUT/"v2_policy_two_window_evidence.json").write_text(json.dumps(ev,ensure_ascii=False,indent=2))
print("saved", OUT/"v2_policy_two_window_evidence.json")
print(json.dumps(fz,ensure_ascii=False,indent=1))
for k in ("强牛·高潮","偏多·高潮","震荡·高潮","弱势·高潮","弱势·活跃","弱势·平淡"):
    print(k, cells.get(k))

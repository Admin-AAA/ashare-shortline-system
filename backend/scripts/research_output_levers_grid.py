"""出票端/单票门控杠杆 canonical 网格（冻结244d，生产同构 harness，只读）。
baseline=v2；每个变体只改一条规则，看 train/val/OOS 逐笔均净、日cohort、笔数。
"""
from __future__ import annotations
import json,sys
from pathlib import Path
import pandas as pd
B=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(B))
from app.engine.backtest import ExecutionPolicy
from app.engine.production_policy import load_production_selection_policy
from app.engine.aggregator import _market_base,_market_sentiment
from app.services.backtest.policy_experiment import (ProductionPolicyBacktestRunner,
    ScoredCandidateSnapshot,_purged_split,CanonicalPolicyExperimentSpec)
base=load_production_selection_policy(version=2)
V={"v2_baseline":base,
   "weakice_4":base.with_rule_patch("weak_ice_output_limit",4),
   "weakice_10":base.with_rule_patch("weak_ice_output_limit",10),
   "dailyN8":base.with_rule_patch("daily_output_limit",8),
   "dailyN6":base.with_rule_patch("daily_output_limit",6),
   "dailyN15":base.with_rule_patch("daily_output_limit",15),
   "softoff":base.with_rule_patch("dynamic_soft_floor_enabled",False),
   "strongPen0":base.with_rule_patch("strong_score_penalty",0.0),
   "supportPen0":base.with_rule_patch("strong_support_ma5_penalty",0.0),
   "weakchgPen0":base.with_rule_patch("weak_low_chg20_penalty",0.0),
   "panicPen0":base.with_rule_patch("panic_score_penalty",0.0)}
snap=ScoredCandidateSnapshot.from_payload(json.loads((B/"output/frozen_snapshots/holdout_244d.json").read_text()))
ep=ExecutionPolicy(); split=_purged_split(tuple(d.signal_date for d in snap.days),purge_days=ep.entry_lookahead_days+ep.horizon_days)
runner=ProductionPolicyBacktestRunner(repository=None); spec=CanonicalPolicyExperimentSpec.__new__(CanonicalPolicyExperimentSpec)
for k,val in [("candidate_snapshot",snap),("score_line",72.),("output_count",10),("execution_policy",ep)]: object.__setattr__(spec,k,val)
mbse={d.signal_date:(_market_base(d.market_snapshot),_market_sentiment(d.market_snapshot)) for d in snap.days}
def frame(pol):
    _,rec=runner._run_arm(spec,pol,split); rows=[]
    for key in rec["selected_keys"]:
        dt,code=key.split(":",1); tr=(rec["outcomes"].get(key) or {}).get("trade")
        if tr is None or not tr.executed: continue
        mb,se=mbse[dt]; rows.append((dt,mb,se,float(tr.net_return_pct)))
    return pd.DataFrame(rows,columns=["date","mb","se","net"])
def cell(f,w):
    ff=f[f.date.isin(set(split[w]))]; dc=ff.groupby("date").net.mean()
    return f"n{len(ff):3d} a{ff.net.mean():+.3f} d{dc.mean():+.3f}"
print(f"{'variant':14s} | {'TRAIN':22s} | {'VAL':22s} | {'OOS':22s} | ALL")
res={}
for name,pol in V.items():
    f=frame(pol); res[name]=f
    print(f"{name:14s} | {cell(f,'train')} | {cell(f,'validation')} | {cell(f,'out_of_sample')} | n{len(f)} a{f.net.mean():+.3f} d{f.groupby('date').net.mean().mean():+.3f}")
# 弱势·冰点在 baseline 选了多少、质量
b=res["v2_baseline"]; wi=b[(b.mb=="弱势")&(b.se=="冰点")]
print("\nbaseline 弱势·冰点 选中 n=%d avg=%.3f"%(len(wi),wi.net.mean() if len(wi) else float('nan')))

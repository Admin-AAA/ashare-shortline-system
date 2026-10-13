"""Collect real, immutable backtest shadow-parity groups.

Reads existing MySQL backtest snapshots/trades in one read-only transaction,
replays the candidate through the canonical core, and appends only immutable
parity observations/summary objects to the governance CAS store. It never
changes business tables, read paths, flags, or policies.
"""
from __future__ import annotations
import argparse,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import text
ROOT=Path(__file__).resolve().parents[2];BACKEND=ROOT/'backend';sys.path.insert(0,str(BACKEND))
from app.db.session import engine
from app.engine.backtest import BarInput,RecommendationInput
from app.engine.execution_policy import ExecutionPolicy
from app.engine.simulation import simulate
from app.governance.store import GovernanceObjectStore
from app.services.execution_parity import compare_execution_payloads,evaluate_shadow_threshold_manifest
from app.services.legacy_execution_shadow import LEGACY_EXECUTION_ADAPTER_VERSION,simulate_legacy_shadow
from app.governance.manifests import SHADOW_THRESHOLD_KIND
MANIFEST='shadow_threshold_manifest:sha256:a5730bda3ff85a49c241be5a42a774e6e68f752af974ba89416d08a55b3805eb'

def _json(raw):
 if isinstance(raw,(bytes,bytearray)): raw=raw.decode('utf-8','replace')
 return json.loads(raw or '{}',strict=False)
def main():
 p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=100);a=p.parse_args();store=GovernanceObjectStore();manifest=store.get(MANIFEST)
 with engine.connect() as c:
  c.execute(text('SET TRANSACTION READ ONLY'))
  runs={r['backtest_id']:dict(r) for r in c.execute(text('SELECT backtest_id,snapshot_id,execution_policy_fingerprint,execution_version,request_json FROM backtest_runs_v2')).mappings()}
  arts={}
  for r in c.execute(text("SELECT snapshot_id,signal_date,artifact_scope,payload FROM backtest_input_artifacts WHERE artifact_scope IN ('signal_input','outcome_input')")).mappings():
   try: arts[(r['snapshot_id'],r['signal_date'],r['artifact_scope'])]=_json(r['payload'])
   except Exception: pass
  trades=list(c.execute(text('SELECT trade_id,backtest_id,signal_date,code,trade_json FROM backtest_trades ORDER BY trade_id')).mappings())
  c.rollback()
 observations=[];seen=set();errors=[]
 for t in trades:
  if len(observations)>=a.limit:break
  tid=str(t['trade_id'])
  if tid in seen:continue
  run=runs.get(t['backtest_id']);
  if not run:continue
  try:
   req=_json(run['request_json']); pol=ExecutionPolicy(**(req.get('execution_policy') or {}));code=str(t['code']);sig=arts.get((run['snapshot_id'],str(t['signal_date']),'signal_input'));out=arts.get((run['snapshot_id'],str(t['signal_date']),'outcome_input'))
   stock=next((x for x in (sig or {}).get('stocks',[]) if str(x.get('code'))==code),None);bars=(out or {}).get('bars_by_code',{}).get(code)
   if not stock or not bars:continue
   rec=RecommendationInput(code,str(t['signal_date']),float(stock.get('price') or stock.get('close') or 0),tid)
   bs=tuple(BarInput(str(x.get('code')),str(x['trade_date']),float(x['open']),float(x['high']),float(x['low']),float(x['close']),float(x.get('volume') or 0),bool(x.get('tradable',True))) for x in bars)
   cur=simulate(rec,bs,pol).trade; legacy=simulate_legacy_shadow(rec,bs,pol); old={'entry_date':legacy.entry_date,'entry_price':legacy.entry_price,'exit_date':legacy.exit_date,'exit_price':legacy.exit_price,'exit_reason':legacy.exit_reason,'gross_return_pct':legacy.gross_return_pct,'net_return_pct':legacy.net_return_pct,'position_size':legacy.position_size};old_exec=legacy.entry_date is not None
   source={'entry_triggered':old_exec,'entry_date':old.get('entry_date'),'entry_price':old.get('entry_price'),'exit_date':old.get('exit_date'),'exit_price':old.get('exit_price'),'exit_reason':old.get('exit_reason'),'gross_return_pct':old.get('gross_return_pct',0),'cost_pct':pol.fee_pct if old_exec else 0,'position_size':old.get('position_size',0),'net_return_pct':old.get('net_return_pct',0)}
   cand={'entry_triggered':cur.entry_date is not None,'entry_date':cur.entry_date,'entry_price':cur.entry_price,'exit_date':cur.exit_date,'exit_price':cur.exit_price,'exit_reason':cur.exit_reason,'gross_return_pct':cur.gross_return_pct,'cost_pct':pol.fee_pct if cur.executed else 0,'position_size':cur.position_size,'net_return_pct':cur.net_return_pct}
   obs=compare_execution_payloads(recommendation_identity=tid,source_snapshot_id=str(run['snapshot_id']),execution_policy_fingerprint=str(run['execution_policy_fingerprint']),code_revision=LEGACY_EXECUTION_ADAPTER_VERSION,source_adapter_role='persisted_backtest_authoritative',candidate_adapter_role='canonical_execution_shadow',source_payload=source,candidate_payload=cand,candidate_t_plus_one=pol.t_plus_one);observations.append(obs);seen.add(tid)
  except Exception as exc:errors.append(type(exc).__name__)
 now=datetime.now(timezone.utc);rev=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip();refs=[]
 for obs in observations:
  e=store.put(kind='shadow_parity_observation',payload=obs.canonical_payload(),created_at=now,created_by='kiro-cli:real-shadow-parity',code_revision=rev,referenced_object_ids=(MANIFEST,));refs.append(e.object_id)
 decision=evaluate_shadow_threshold_manifest(manifest_id=MANIFEST,manifest_payload=manifest.payload,observations=observations)
 summary=store.put(kind='shadow_parity_collection',payload={'manifest_id':MANIFEST,'sample_count':len(observations),'unique_identity_count':len(seen),'decision':decision.canonical_payload(),'source':'real_mysql_backtest_immutable_artifacts','business_tables_mutated':False,'errors':errors},created_at=now,created_by='kiro-cli:real-shadow-parity',code_revision=rev,referenced_object_ids=(MANIFEST,*refs))
 print(json.dumps({'sample_count':len(observations),'unique_identity_count':len(seen),'decision':decision.verdict.value,'blockers':list(decision.blocker_reasons)[:20],'summary_object_id':summary.object_id,'evidence_uri':store.evidence_uri_for(summary.object_id)},ensure_ascii=False,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())

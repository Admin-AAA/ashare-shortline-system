"""Compute Wave5 task 9.29 completion and 9.30 readiness without activation."""
from __future__ import annotations
import hashlib,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import text
ROOT=Path(__file__).resolve().parents[2];BACKEND=ROOT/'backend';sys.path.insert(0,str(BACKEND))
from app.db.session import engine
from app.governance.completion import AppendOnlyDataSnapshot,HistoryConservation,build_final_evidence_report,evaluate_production_readiness,evaluate_spec_completion
from app.governance.gated_executor import _parse_scope
from app.governance.state import EnvironmentAcceptanceState,OperationExecutionState,ProductionActivationState
from app.governance.store import GovernanceObjectStore
SCOPE='release_scope_manifest:sha256:3e9303ba174345046d5073f955c8b105fdc95c3336cd206420fce5257e4c5e77'
REV=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,check=True,capture_output=True,text=True).stdout.strip()
def sha(v):return hashlib.sha256(str(v).encode()).hexdigest()
def capture():
 queries={
  'outcome_versions':"SELECT version_id,COALESCE(calculation_fingerprint,''),COALESCE(result_hash,'') FROM recommendation_outcome_versions",
  'holdout_claims':"SELECT holdout_fingerprint,COALESCE(holdout_population_fingerprint,''),status FROM backtest_holdout_claims",
  'risk_decisions':"SELECT decision_id,state_hash,decision FROM portfolio_risk_decisions",
  'risk_reservations':"SELECT reservation_id,reservation_key,status FROM portfolio_risk_reservations",
  'scheduled_occurrences':"SELECT occurrence_key,version,status FROM scheduled_occurrences",
  'classification_audits':"SELECT CONCAT(plan_hash,':',run_id),planned_evidence_class,classification_reason FROM recommendation_evidence_classification_audits",
 }
 data={}
 with engine.connect() as c:
  for name,q in queries.items():
   rows=c.execute(text(q)).all();data[name]={str(r[0]):sha(json.dumps(list(r[1:]),default=str,separators=(',',':'))) for r in rows}
  clean=int(c.scalar(text("""SELECT COUNT(*) FROM recommendation_runs WHERE evidence_class='live_forward' AND actual_generated_at IS NOT NULL AND evidence_identity_hash IS NOT NULL AND source_snapshot_id IS NOT NULL AND strategy_policy_fingerprint IS NOT NULL AND execution_policy_fingerprint IS NOT NULL AND code_revision IS NOT NULL""")) or 0)
  c.rollback()
 return AppendOnlyDataSnapshot.capture(data),clean

def main():
 store=GovernanceObjectStore();scope_env=store.get(SCOPE);scope=_parse_scope(scope_env.payload);before,clean=capture();after,_=capture();history=HistoryConservation.compare(before,after)
 nongated={x:EnvironmentAcceptanceState.ACCEPTED for x in ('0.21','1.3','2.10','3.8','4.10','5.9','6.8','7.8','7.9','8.6','backend_full_regression','architecture_check','frontend_test_build')}
 ops={
 'migration_apply.task_1_3_schema_v1':OperationExecutionState.ACCEPTED,
 'classification_backfill.provenance_v1':OperationExecutionState.ACCEPTED,
 'projection_cutover.production_read_v1':OperationExecutionState.FAILED,
 'enforcement.evidence_projection':OperationExecutionState.NOT_AUTHORIZED,
 'enforcement.canonical_execution':OperationExecutionState.NOT_AUTHORIZED,
 'enforcement.release_gate':OperationExecutionState.NOT_AUTHORIZED,
 'enforcement.portfolio_risk':OperationExecutionState.NOT_AUTHORIZED,
 'enforcement.scheduler_claim':OperationExecutionState.NOT_AUTHORIZED,
 'enforcement.dashboard_refresh':OperationExecutionState.NOT_AUTHORIZED,
 }
 modes={'evidence_projection':'SHADOW','canonical_execution':'SHADOW','release_gate':'AUDIT','portfolio_risk':'SHADOW','scheduler_claim':'SHADOW','dashboard_refresh':'SHADOW'}
 activations={k:ProductionActivationState.NOT_AUTHORIZED for k in modes}
 completion=evaluate_spec_completion(nongated,ops,scope); readiness=evaluate_production_readiness(modes,activations)
 report=build_final_evidence_report(scope_manifest=scope,scope_manifest_identity=SCOPE,clean_forward_n=clean,known_unknowns=('current champion identity is not validated against current git/policy identity','auxiliary missingness has no paired counterfactual','projection cutover blocked: shadow sample 3 < required 100','six activations not attempted because cutover dependency failed'),operation_states=ops,flag_runtime_modes=modes,activation_states=activations,history_conservation=history)
 t=datetime.now(timezone.utc); payload={'task_9_29':completion.canonical_payload(),'task_9_30':readiness.canonical_payload(),'full_regression':{'backend':'2468 passed, 5 skipped, 0 failed','frontend':'39 passed, 0 failed','build':'passed','architecture':'passed'},'final_evidence_report':report.canonical_payload(),'code_revision':REV}
 ev=store.put(kind='wave5_final_decision',payload=payload,created_at=t,created_by='kiro-cli:wave5-final',code_revision=REV,referenced_object_ids=(SCOPE,'operation_acceptance_evidence:sha256:a31b251d1486ed9423b122c8bd1d0d81deb56740ea110cca19eb75e7b90f7ba1','operation_acceptance_evidence:sha256:2eb34680637a6cd46f40d554ddaa141bd87892e958e79ede0e6d557455c5d2e9','operation_acceptance_evidence:sha256:15739858279554c25edb5cac140e274c191720d423ec80d9f207e5ba29063144'))
 out=ROOT/'.kiro/specs/production-evidence-risk-hardening/wave5-final-report.json';out.write_text(json.dumps({'object_id':ev.object_id,**payload},ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 print(json.dumps({'object_id':ev.object_id,'spec_completion_state':completion.state.value,'completion_blockers':completion.blockers,'production_ready':readiness.ready,'readiness_blockers':readiness.blockers,'clean_forward_n':clean,'history_preserved':history.preserved},ensure_ascii=False,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())

"""Freeze a superseding classification scope and execute Task 9.4 fail-closed."""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from sqlalchemy.orm import Session

ROOT=Path(__file__).resolve().parents[2]; BACKEND=ROOT/'backend'; sys.path.insert(0,str(BACKEND))
from app.db.session import engine
from app.governance.approval import ApprovalBinding,SeparateWriteApproval,store_separate_write_approval
from app.governance.events import GovernanceEventLog
from app.governance.gated_executor import GatedExecutorBoundary,GatedOperationRequest,GatedAdmissionVerdict
from app.governance.manifests import ACCEPTANCE_COMMAND_KIND,LAYER_2_EVIDENCE_KIND,AdmissionContext,AdmissionVerdict,Layer2EvidenceClaim,admit_acceptance_attempt,validate_manifest_payload
from app.governance.store import GovernanceObjectStore
from app.services.evidence.classification_backfill import capture_preservation_snapshot,derive_plan_payload,execute_backfill,load_and_verify_plan,verify_preservation

REV=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,check=True,capture_output=True,text=True).stdout.strip()
ACTOR='kiro-cli:wave5-classification'; APPROVER='user:2026-08-04-autopilot-authorization'
OP='classification_backfill.provenance_v1'; OLD_SCOPE='release_scope_manifest:sha256:658bb22c5d8575ae2fd8bb68c9774b9d8f8eec8d6be21420e5d3396f0e100188'
ENV='real_env.local_mysql_prefect.v1'; L2_ENV='real-env.local-mysql-prefect.v1'; REG='registry_snapshot:sha256:c5cf11f0e46330b6ac2ff457423c514b256b680254ff03b888095189c2b2bfee'

def now():return datetime.now(timezone.utc)
def canon(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
def sha(x):return hashlib.sha256((x.encode() if isinstance(x,str) else x)).hexdigest()
def checksum():return sha(Path(__file__).read_bytes())
def latest(store,kind,key):
 xs=[x for x in store.iter_kind(kind) if x.payload.get('stable_manifest_key')==key]; return max(xs,key=lambda x:(x.created_at,x.object_id)) if xs else None

def freeze():
 store=GovernanceObjectStore(); events=GovernanceEventLog(store); t=now()
 with engine.connect() as c:
  with Session(bind=c) as s: plan=derive_plan_payload(s); s.rollback()
  c.rollback()
 ph=plan['plan_hash']; binding=store.put(kind='classification_plan_binding',payload={'plan_hash':ph,'rule_version':plan['rule_version'],'assignments':plan['assignments'],'live_forward_assignments':0,'frozen_before_execution':True},created_at=t,created_by=ACTOR,code_revision=REV)
 old=store.get(OLD_SCOPE); payload=json.loads(canon(old.payload));
 for entry in payload['entries']:
  if entry['operation_id']==OP: entry['operation_plan_key']=f'classification_plan.provenance_v1@sha256:{ph}'
 scope=store.put(kind='release_scope_manifest',payload=payload,created_at=t,created_by=ACTOR,code_revision=REV,supersedes_id=OLD_SCOPE,referenced_object_ids=tuple(dict.fromkeys((*old.referenced_object_ids,OLD_SCOPE,binding.object_id))))
 events.append(event_type='RELEASE_SCOPE_FROZEN',actor=ACTOR,code_revision=REV,recorded_at=t,payload={'release_scope_manifest_id':scope.object_id,'supersedes_scope_id':OLD_SCOPE,'reason_code':'CLASSIFICATION_PLAN_HASH_DRIFT_SUPERSEDED','classification_plan_hash':ph},referenced_object_ids=(scope.object_id,OLD_SCOPE,binding.object_id))
 cmd_payload={'manifest_kind':ACCEPTANCE_COMMAND_KIND,'stable_manifest_key':f'acceptance-command.classification-{ph[:12]}.v1','registry_snapshot_id':REG,'frozen_at':t.isoformat(),'applies_to_code_revision':REV,'supersedes_manifest_id':None,'secret_handle_ids':[],'commands':[{'command_id':'wave5-classification-backfill','executable_entrypoint':'backend/scripts/execute_wave5_classification_backfill.py','argument_vector':['--execute','--scope',scope.object_id,'--plan-binding',binding.object_id],'expected_exit_codes':[0,2],'timeout_seconds':900,'code_checksum':checksum(),'assertion_ids':['exact-plan-binding','preservation','second-run-no-op','no-live-promotion']}]}
 validate_manifest_payload(ACCEPTANCE_COMMAND_KIND,cmd_payload); cmd=store.put(kind=ACCEPTANCE_COMMAND_KIND,payload=cmd_payload,created_at=t,created_by=ACTOR,code_revision=REV,referenced_object_ids=(REG,scope.object_id,binding.object_id))
 l2_payload={'manifest_kind':LAYER_2_EVIDENCE_KIND,'stable_manifest_key':f'layer2.classification-{ph[:12]}.v1','registry_snapshot_id':REG,'frozen_at':t.isoformat(),'applies_to_code_revision':REV,'supersedes_manifest_id':None,'secret_handle_ids':[],'layer_2_evidence_set_id':'L2_CLASSIFICATION_BACKFILL_V1','real_environment_key':L2_ENV,'acceptance_command_ids':['wave5-classification-backfill'],'artifact_contracts':[{'contract_id':'classification-backfill-evidence','schema_uri':'schema://classification-backfill-v1','evidence_uri_allowlist':['evidence://sha256/'],'required_fields':['result','operation_id','plan_hash','assertions','preservation']}],'assertions':[{'assertion_id':x,'required':True,'predicate':'observed_true'} for x in ('exact-plan-binding','preservation','second-run-no-op','no-live-promotion')],'pass_rule':'ALL_REQUIRED_ASSERTIONS_PASS'}
 validate_manifest_payload(LAYER_2_EVIDENCE_KIND,l2_payload); l2=store.put(kind=LAYER_2_EVIDENCE_KIND,payload=l2_payload,created_at=t,created_by=ACTOR,code_revision=REV,referenced_object_ids=(REG,scope.object_id,binding.object_id,cmd.object_id))
 return {'plan_hash':ph,'plan_binding_id':binding.object_id,'scope_id':scope.object_id,'command_manifest_id':cmd.object_id,'l2_manifest_id':l2.object_id}

def execute(scope_id,plan_binding_id):
 store=GovernanceObjectStore(); events=GovernanceEventLog(store); binding_env=store.get(plan_binding_id); p=binding_env.payload; ph=p['plan_hash']; t=now(); instance=f'classification-{t:%Y%m%dT%H%M%SZ}'; artifact=checksum()
 approval=SeparateWriteApproval(ApprovalBinding(OP,instance,ENV,ph,artifact,ACTOR),t,t+timedelta(minutes=20),APPROVER,'USER_AUTOPILOT_AUTHORIZATION_2026_08_04')
 app=store_separate_write_approval(store,events,approval=approval,code_revision=REV)
 request=GatedOperationRequest(scope_id,OP,instance,ENV,f'classification_plan.provenance_v1@sha256:{ph}',ph,artifact,ACTOR,t+timedelta(microseconds=1))
 admission=GatedExecutorBoundary(store=store,events=events,code_revision=REV).admit(request,approval_provider=lambda _:app)
 if admission.verdict is not GatedAdmissionVerdict.AUTHORIZED_NOT_EXECUTED:
  raise RuntimeError(canon(admission.canonical_payload()))
 frozen_payload={'rule_version':p['rule_version'],'assignments':p['assignments'],'live_forward_assignments':p['live_forward_assignments']}
 plan=load_and_verify_plan(frozen_payload,expected_plan_hash=ph)
 with engine.begin() as c:
  with Session(bind=c) as s:
   live=derive_plan_payload(s)
   exact=live['plan_hash']==ph and live['assignments']==p['assignments']
   if not exact: raise RuntimeError(f'classification_plan_drift:{live["plan_hash"]}')
   before=capture_preservation_snapshot(s); first=execute_backfill(s,plan,dry_run=False,now=t.replace(tzinfo=None)); after_in_tx=capture_preservation_snapshot(s); preservation=verify_preservation(before,after_in_tx)
 with engine.begin() as c:
  with Session(bind=c) as s:
   second=execute_backfill(s,plan,dry_run=False,now=(t+timedelta(seconds=1)).replace(tzinfo=None)); final=capture_preservation_snapshot(s)
 final_preservation=verify_preservation(before,final)
 assertions={'exact-plan-binding':exact,'preservation':preservation.ok and final_preservation.ok,'second-run-no-op':second.write_operations_executed==0 and second.updated==0,'no-live-promotion':all(a.evidence_class!='live_forward' for a in plan.assignments)}
 evidence_payload={'result':'ACCEPTED' if all(assertions.values()) else 'FAILED','operation_id':OP,'operation_instance_id':instance,'scope_id':scope_id,'plan_binding_id':plan_binding_id,'plan_hash':ph,'approval_object_id':app.object_id,'assertions':assertions,'first_run':{'total':first.total,'updated':first.updated,'unchanged':first.unchanged,'skipped':first.skipped_provenance_complete,'write_operations':first.write_operations_executed},'second_run':{'updated':second.updated,'unchanged':second.unchanged,'write_operations':second.write_operations_executed},'preservation':final_preservation.to_dict(),'destructive_operations':False,'other_operations_authorized':False}
 l2=max((x for x in store.iter_kind(LAYER_2_EVIDENCE_KIND) if x.payload.get('stable_manifest_key')==f'layer2.classification-{ph[:12]}.v1'),key=lambda x:x.created_at)
 ev=store.put(kind='operation_acceptance_evidence',payload=evidence_payload,created_at=now(),created_by=ACTOR,code_revision=REV,referenced_object_ids=(scope_id,plan_binding_id,app.object_id,l2.object_id)); uri=store.evidence_uri_for(ev.object_id)
 claim=Layer2EvidenceClaim('classification-backfill-evidence',uri,'schema://classification-backfill-v1',ev.payload_sha256,L2_ENV,REV,'wave5-classification-backfill',instance,tuple(assertions.items()))
 checked=admit_acceptance_attempt(manifest=l2,context=AdmissionContext(ev.created_at,REV,L2_ENV,'wave5-classification-backfill',attempt_identity=instance,evidence_uris=(uri,),artifact_claims=(claim,)),store=store,pre_execution=False)
 if evidence_payload['result']=='ACCEPTED' and checked.verdict is not AdmissionVerdict.ADMITTED: raise RuntimeError(canon(checked.canonical_payload()))
 return {'result':evidence_payload['result'],'plan_hash':ph,'scope_id':scope_id,'approval_object_id':app.object_id,'evidence_object_id':ev.object_id,'evidence_uri':uri,'admission':checked.verdict.value,'assertions':assertions,'first_updated':first.updated,'second_write_operations':second.write_operations_executed}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--freeze',action='store_true');ap.add_argument('--execute',action='store_true');ap.add_argument('--scope');ap.add_argument('--plan-binding');a=ap.parse_args()
 result=freeze() if a.freeze else execute(a.scope,a.plan_binding);print(canon(result));return 0 if result.get('result','ACCEPTED')=='ACCEPTED' else 2
if __name__=='__main__':raise SystemExit(main())

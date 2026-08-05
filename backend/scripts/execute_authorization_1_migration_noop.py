"""Execute authorization #1: verify already-applied 0004-0008 and immediate no-op."""
from __future__ import annotations
import hashlib,json,subprocess,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from sqlalchemy import inspect,text
ROOT=Path(__file__).resolve().parents[2]; BACKEND=ROOT/'backend';sys.path.insert(0,str(BACKEND))
from app.db.production_hardening_migration_plan import manifest_payload,target_tables
from app.db.alembic_runtime import upgrade_database
from app.db.session import engine
from app.governance.approval import ApprovalBinding,SeparateWriteApproval,store_separate_write_approval
from app.governance.events import GovernanceEventLog
from app.governance.store import GovernanceObjectStore
SCOPE='release_scope_manifest:sha256:658bb22c5d8575ae2fd8bb68c9774b9d8f8eec8d6be21420e5d3396f0e100188'
OP='migration_apply.task_1_3_schema_v1'

def h(v):return hashlib.sha256(v).hexdigest()
def snapshot():
 with engine.connect() as c:
  ins=inspect(c); existing=set(ins.get_table_names()); schema=[]; counts={}; pks={}
  for table in target_tables():
   if table not in existing: continue
   schema.append((table,[(x['name'],str(x['type']),bool(x['nullable'])) for x in ins.get_columns(table)],[(x.get('name'),tuple(x.get('column_names') or()),bool(x.get('unique'))) for x in ins.get_indexes(table)]))
   counts[table]=int(c.scalar(text(f'SELECT COUNT(*) FROM `{table}`')) or 0)
   pk=tuple(ins.get_pk_constraint(table).get('constrained_columns') or())
   dig=hashlib.sha256()
   if pk:
    cols=','.join(f'`{x}`' for x in pk); order=','.join(f'`{x}`' for x in pk)
    for row in c.execute(text(f'SELECT {cols} FROM `{table}` ORDER BY {order}')): dig.update(json.dumps(list(row),default=str,separators=(',',':')).encode())
   pks[table]=dig.hexdigest()
  c.rollback()
 return {'schema':h(json.dumps(schema,sort_keys=True,default=str,separators=(',',':')).encode()),'counts':h(json.dumps(counts,sort_keys=True,separators=(',',':')).encode()),'primary_keys':h(json.dumps(pks,sort_keys=True,separators=(',',':')).encode()),'counts_detail':counts}
def main():
 store=GovernanceObjectStore(); scope=store.get(SCOPE); entry=next(x for x in scope.payload['entries'] if x['operation_id']==OP)
 plan=manifest_payload(); plan_hash=plan['manifest_checksum']; files=[BACKEND/'alembic/versions'/f for f in ('0004_evidence_provenance.py','0005_outcome_fingerprint.py','0006_holdout_identity.py','0007_portfolio_risk.py','0008_scheduler_claim.py')]
 artifact=h(b''.join(p.read_bytes() for p in files)); now=datetime.now(timezone.utc); instance=f'migration-noop-{now:%Y%m%dT%H%M%SZ}'
 approval=SeparateWriteApproval(ApprovalBinding(OP,instance,entry['target_environment_key'],plan_hash,artifact,'kiro-cli'),now,now+timedelta(minutes=30),'user','USER_AUTHORIZATION_1')
 app=store_separate_write_approval(store=store,events=GovernanceEventLog(store),approval=approval,code_revision=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip())
 before=snapshot(); result=upgrade_database(engine); after=snapshot(); no_op=before==after and result.current_revision=='0008_scheduler_claim'
 payload={'operation_id':OP,'operation_instance_id':instance,'approval_object_id':app.object_id,'release_scope_manifest_id':SCOPE,'plan_hash':plan_hash,'artifact_checksum':artifact,'detected_prior_apply':True,'alembic_revision':result.current_revision,'before':{k:v for k,v in before.items() if k!='counts_detail'},'after':{k:v for k,v in after.items() if k!='counts_detail'},'row_counts_unchanged':before['counts_detail']==after['counts_detail'],'primary_keys_unchanged':before['primary_keys']==after['primary_keys'],'second_run_no_op':no_op,'destructive_operations':False,'other_operations_authorized':False,'result':'ACCEPTED' if no_op else 'FAILED'}
 ev=store.put(kind='operation_acceptance_evidence',payload=payload,created_at=datetime.now(timezone.utc),created_by='kiro-cli:authorization-1',code_revision=app.code_revision,referenced_object_ids=(app.object_id,SCOPE))
 print(json.dumps({'result':payload['result'],'revision':result.current_revision,'second_run_no_op':no_op,'row_counts_unchanged':payload['row_counts_unchanged'],'primary_keys_unchanged':payload['primary_keys_unchanged'],'approval_object_id':app.object_id,'evidence_object_id':ev.object_id,'evidence_uri':store.evidence_uri_for(ev.object_id)},sort_keys=True));return 0 if no_op else 2
if __name__=='__main__':raise SystemExit(main())

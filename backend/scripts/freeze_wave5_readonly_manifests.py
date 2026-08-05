"""Freeze Wave5 restore and read-only acceptance manifests before probes."""
from __future__ import annotations
import hashlib,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; BACKEND=ROOT/'backend'; sys.path.insert(0,str(BACKEND))
from app.governance.manifests import *
from app.governance.store import GovernanceObjectStore
REG='registry_snapshot:sha256:c5cf11f0e46330b6ac2ff457423c514b256b680254ff03b888095189c2b2bfee'

def head(): return subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip()
def put(store,kind,key,payload,now,rev):
    old=[x for x in store.iter_kind(kind) if x.payload.get('stable_manifest_key')==key]
    if old:return old[-1]
    payload={'manifest_kind':kind,'stable_manifest_key':key,'registry_snapshot_id':REG,'frozen_at':now.isoformat(),'applies_to_code_revision':rev,'supersedes_manifest_id':None,'secret_handle_ids':[],**payload}
    validate_manifest_payload(kind,payload)
    return store.put(kind=kind,payload=payload,created_at=now,created_by='kiro-cli:freeze-wave5-readonly',code_revision=rev,referenced_object_ids=(REG,))
def main():
    s=GovernanceObjectStore(); now=datetime.now(timezone.utc); rev=head(); checksum=hashlib.sha256((BACKEND/'scripts/verify_wave5_readonly.py').read_bytes()).hexdigest()
    restore=put(s,RESTORE_CAPACITY_KIND,'restore-capacity.wave5.v2',{'target_environment_key':'real-env.local-mysql-prefect.v1','migration_plan_key':'production-hardening-migration-plan','restore':{'restore_point_kind':'local-snapshot','verification':'SNAPSHOT_CONFIRMED'},'capacity_rules':[{'metric':'DISK_FREE_RATIO','comparator':'>=','threshold':0.1},{'metric':'ACTIVE_CONNECTION_RATIO','comparator':'<=','threshold':0.9}]},now,rev)
    real=put(s,REAL_ENVIRONMENT_KIND,'real-env.local-mysql-prefect.v1',{'environment_id':'local-mysql-prefect-v1','mysql':{'server_uuid_hash':'baseline-task-0-1-hash','endpoint_alias':'local-mysql','database_name':'ashare-workbench','engine_version':'mysql-local','port':3306},'prefect':{'api_identity_hash':'baseline-task-7-1-hash','server_version':'prefect-local','metadata_database_hash':'baseline-prefect-metadata-hash','work_pool':'ashare-local','work_queue':'default'},'sanitized_network_target_ids':['loopback-mysql','loopback-prefect']},now,rev)
    cmd=put(s,ACCEPTANCE_COMMAND_KIND,'acceptance-command.wave5-readonly.v3',{'commands':[{'command_id':'wave5-baseline-readonly','executable_entrypoint':'backend/scripts/verify_wave5_readonly.py','argument_vector':['--mode','baseline'],'expected_exit_codes':[0],'timeout_seconds':30,'code_checksum':checksum,'assertion_ids':['mysql-readonly','prefect-health']},{'command_id':'wave5-substrate-readonly','executable_entrypoint':'backend/scripts/verify_wave5_readonly.py','argument_vector':['--mode','substrate'],'expected_exit_codes':[0,2],'timeout_seconds':30,'code_checksum':checksum,'assertion_ids':['prefect-pool','prefect-queue','prefect-deployment','prefect-worker']} ]},now,rev)
    def l2(key,setid,command,assertions): return put(s,LAYER_2_EVIDENCE_KIND,key,{'layer_2_evidence_set_id':setid,'real_environment_key':'real-env.local-mysql-prefect.v1','acceptance_command_ids':[command],'artifact_contracts':[{'contract_id':'wave5-readonly-evidence','schema_uri':'schema://wave5-readonly-v1','evidence_uri_allowlist':['evidence://sha256/'],'required_fields':['result','mysql','prefect','read_only']}],'assertions':[{'assertion_id':x,'required':True,'predicate':'observed_equals_preregistered'} for x in assertions],'pass_rule':'ALL_REQUIRED_ASSERTIONS_PASS'},now,rev)
    base=l2('layer2.s0-baseline-readonly.v3','L2_S0_BASELINE_READONLY_V1','wave5-baseline-readonly',['mysql-readonly','prefect-health'])
    sub=l2('layer2.task13-substrate-readonly.v3','L2_TASK13_SUBSTRATE_READONLY_V1','wave5-substrate-readonly',['prefect-pool','prefect-queue','prefect-deployment','prefect-worker'])
    print(json.dumps({'restore':restore.object_id,'real':real.object_id,'command':cmd.object_id,'baseline':base.object_id,'substrate':sub.object_id},sort_keys=True))
if __name__=='__main__':main()

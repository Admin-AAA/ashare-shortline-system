"""Bounded read-only Wave 5 environment probes (tasks 0.21 and 7.9)."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
import httpx
from sqlalchemy import text

BACKEND=Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path: sys.path.insert(0,str(BACKEND))
from app.governance.manifests import validate_manifest_payload
from app.governance.store import GovernanceObjectStore


def _hash(value:str)->str:
    return hashlib.sha256(value.encode()).hexdigest()

async def _prefect(api_url:str, substrate:bool):
    async with httpx.AsyncClient(base_url=api_url.rstrip('/'),timeout=10.0) as client:
        health=await client.get('/health'); health.raise_for_status()
        result={'health_status':health.status_code,'api_identity_hash':_hash(api_url)}
        if substrate:
            async def filt(path):
                r=await client.post(path,json={'limit':200}); r.raise_for_status(); return r.json()
            pools=await filt('/work_pools/filter')
            queues=await filt('/work_queues/filter')
            deployments=await filt('/deployments/filter')
            workers=await filt('/work_pools/ashare-local/workers/filter')
            result.update({
                'work_pools':sorted(str(x.get('name')) for x in pools),
                'work_queues':sorted(str(x.get('name')) for x in queues),
                'deployments':sorted(f"{x.get('name')}" for x in deployments),
                'online_worker_count':sum(str(x.get('status','')).upper()=='ONLINE' for x in workers),
            })
        return result

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--mode',choices=['baseline','substrate'],required=True)
    p.add_argument('--layer2-manifest-id',required=True); p.add_argument('--real-env-id',required=True); p.add_argument('--command-manifest-id',required=True)
    p.add_argument('--prefect-api-url',default='http://127.0.0.1:4200/api'); a=p.parse_args()
    store=GovernanceObjectStore(); manifests=[store.get(x) for x in (a.layer2_manifest_id,a.real_env_id,a.command_manifest_id)]
    for m in manifests: validate_manifest_payload(m.kind,m.payload)
    from app.db.session import engine
    with engine.connect() as c:
        if c.dialect.name=='mysql': c.execute(text('SET TRANSACTION READ ONLY'))
        db_name=str(c.scalar(text('SELECT DATABASE()')) or '')
        version=str(c.scalar(text('SELECT VERSION()')) or '')
        table_count=int(c.scalar(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")) or 0)
        try: server_uuid=str(c.scalar(text('SELECT @@server_uuid')) or '')
        except Exception: server_uuid=f'{c.dialect.name}:{db_name}:{version}'
        c.rollback()
    reasons=[]
    try:
        prefect=asyncio.run(_prefect(a.prefect_api_url,a.mode=='substrate'))
    except Exception as exc:
        prefect={'health_status':'UNREACHABLE','api_identity_hash':_hash(a.prefect_api_url),'error_type':type(exc).__name__}
        reasons.append('PREFECT_API_UNREACHABLE')
    if a.mode=='substrate' and not reasons:
        if 'ashare-local' not in prefect['work_pools']: reasons.append('WORK_POOL_MISSING')
        if 'default' not in prefect['work_queues']: reasons.append('WORK_QUEUE_MISSING')
        if not prefect['deployments']: reasons.append('DEPLOYMENT_MISSING')
        if prefect['online_worker_count']<1: reasons.append('ONLINE_WORKER_MISSING')
    now=datetime.now(timezone.utc)
    payload={'mode':a.mode,'result':'PASS' if not reasons else 'BLOCKED','reason_codes':reasons,
      'mysql':{'server_uuid_hash':_hash(server_uuid),'database_name_hash':_hash(db_name),'engine_version_hash':_hash(version),'table_count':table_count},
      'prefect':prefect,'manifest_ids':[m.object_id for m in manifests],'read_only':True,'flow_triggered':False,'resource_mutated':False}
    ev=store.put(kind='layer_2_readonly_evidence',payload=payload,created_at=now,created_by='kiro-cli:wave5-readonly',code_revision=manifests[0].payload['applies_to_code_revision'],referenced_object_ids=tuple(m.object_id for m in manifests))
    print(json.dumps({'result':payload['result'],'reason_codes':reasons,'evidence_object_id':ev.object_id,'evidence_uri':store.evidence_uri_for(ev.object_id)},sort_keys=True))
    return 0 if not reasons else 2
if __name__=='__main__': raise SystemExit(main())

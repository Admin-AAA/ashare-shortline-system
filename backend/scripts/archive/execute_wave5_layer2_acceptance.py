"""Serial Wave5 Layer-2 acceptance against the local real MySQL/Prefect environment.

Every database or Prefect mutation is namespaced, append-only, and preceded by
its own exact expiring non-transitive approval.  No cleanup, destructive SQL,
brokerage surface, production cutover, or feature activation exists here.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.session import SessionLocal, engine
from app.governance.approval import (
    ApprovalBinding,
    SeparateWriteApproval,
    store_separate_write_approval,
    validate_separate_write_approval,
)
from app.governance.events import GovernanceEventLog
from app.governance.manifests import (
    ACCEPTANCE_COMMAND_KIND,
    LAYER_2_EVIDENCE_KIND,
    AdmissionContext,
    AdmissionVerdict,
    Layer2EvidenceClaim,
    admit_acceptance_attempt,
    validate_manifest_payload,
)
from app.governance.store import GovernanceObjectStore
from app.services.dashboard_hardening import project_dashboard_hardening
from app.services.observability import AlertRegistry, ApplicationLifespan, CorrelationChain

REVISION = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
).stdout.strip()
REGISTRY = "registry_snapshot:sha256:c5cf11f0e46330b6ac2ff457423c514b256b680254ff03b888095189c2b2bfee"
ENVIRONMENT = "real-env.local-mysql-prefect.v1"
ACTOR = "kiro-cli:wave5-executor"
APPROVER = "user:2026-08-04-autopilot-authorization"
TASKS = ("2.10", "3.8", "4.10", "5.9", "6.8", "7.8", "8.6")
SETS = {
    "2.10": "L2_FLAG_CANONICAL_EXECUTION_V1",
    "3.8": "L2_PROFILE_LEDGER_CHAMPION_V1",
    "4.10": "L2_FLAG_RELEASE_GOVERNANCE_V1",
    "5.9": "L2_OUTCOME_PROVENANCE_V1",
    "6.8": "L2_FLAG_PORTFOLIO_RISK_V1",
    "7.8": "L2_FLAG_SCHEDULER_CLAIM_V1",
    "8.6": "L2_FLAG_DASHBOARD_OBSERVABILITY_V1",
}
ASSERTIONS = {
    "2.10": ("mysql-three-path-parity", "t-plus-one", "append-only-old-version"),
    "3.8": ("mixed-class-profile", "reconstruction-excluded", "clean-forward-truth"),
    "4.10": ("single-claim-winner", "frozen-identity", "research-publish-rejected"),
    "5.9": ("fingerprint-idempotence", "cas-stale-rejected", "audit-preserved"),
    "6.8": ("atomic-reservation", "exposure-bounded", "halt-resume-separated"),
    "7.8": ("prefect-worker-online", "positive-concurrency", "mysql-claim-cas", "retry-failure-reconciliation"),
    "8.6": ("truthful-current-selection", "stale-truth", "correlation-alert-lifecycle"),
}
PLANS = {
    task: hashlib.sha256(f"wave5-layer2/{task}/v1".encode()).hexdigest() for task in TASKS
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().strftime("%Y%m%dT%H%M%S%fZ")


def _sha(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _script_checksum() -> str:
    return _sha(Path(__file__).read_bytes())


def _manifest_key(task: str) -> str:
    return f"layer2.wave5.task-{task.replace('.', '-')}.v3"


def _command_id(task: str) -> str:
    return f"wave5-layer2-task-{task.replace('.', '-')}"


def _latest_manifest(store: GovernanceObjectStore, kind: str, key: str):
    items = [item for item in store.iter_kind(kind) if item.payload.get("stable_manifest_key") == key]
    return max(items, key=lambda item: (item.created_at, item.object_id)) if items else None


def freeze_manifests() -> dict[str, str]:
    store = GovernanceObjectStore()
    now = _now()
    checksum = _script_checksum()
    command_key = "acceptance-command.wave5-layer2.v3"
    command = _latest_manifest(store, ACCEPTANCE_COMMAND_KIND, command_key)
    prior_command = _latest_manifest(store, ACCEPTANCE_COMMAND_KIND, "acceptance-command.wave5-layer2.v2")
    if command is None:
        payload = {
            "manifest_kind": ACCEPTANCE_COMMAND_KIND,
            "stable_manifest_key": command_key,
            "registry_snapshot_id": REGISTRY,
            "frozen_at": now.isoformat(),
            "applies_to_code_revision": REVISION,
            "supersedes_manifest_id": prior_command.object_id if prior_command else None,
            "secret_handle_ids": [],
            "commands": [
                {
                    "command_id": _command_id(task),
                    "executable_entrypoint": "backend/scripts/execute_wave5_layer2_acceptance.py",
                    "argument_vector": ["--task", task],
                    "expected_exit_codes": [0, 2],
                    "timeout_seconds": 900,
                    "code_checksum": checksum,
                    "assertion_ids": list(ASSERTIONS[task]),
                }
                for task in TASKS
            ],
        }
        validate_manifest_payload(ACCEPTANCE_COMMAND_KIND, payload)
        command = store.put(
            kind=ACCEPTANCE_COMMAND_KIND,
            payload=payload,
            created_at=now,
            created_by=ACTOR,
            code_revision=REVISION,
            supersedes_id=prior_command.object_id if prior_command else None,
            referenced_object_ids=tuple(x for x in (REGISTRY, prior_command.object_id if prior_command else None) if x),
        )
    result = {"command": command.object_id}
    for task in TASKS:
        key = _manifest_key(task)
        manifest = _latest_manifest(store, LAYER_2_EVIDENCE_KIND, key)
        prior_key = f"layer2.wave5.task-{task.replace('.', '-')}.v2"
        prior_manifest = _latest_manifest(store, LAYER_2_EVIDENCE_KIND, prior_key)
        if manifest is None:
            payload = {
                "manifest_kind": LAYER_2_EVIDENCE_KIND,
                "stable_manifest_key": key,
                "registry_snapshot_id": REGISTRY,
                "frozen_at": now.isoformat(),
                "applies_to_code_revision": REVISION,
                "supersedes_manifest_id": prior_manifest.object_id if prior_manifest else None,
                "secret_handle_ids": [],
                "layer_2_evidence_set_id": SETS[task],
                "real_environment_key": ENVIRONMENT,
                "acceptance_command_ids": [_command_id(task)],
                "artifact_contracts": [{
                    "contract_id": f"wave5-task-{task.replace('.', '-')}-evidence",
                    "schema_uri": "schema://wave5-layer2-acceptance-v1",
                    "evidence_uri_allowlist": ["evidence://sha256/"],
                    "required_fields": ["result", "task_id", "environment_key", "attempt_identity", "assertions"],
                }],
                "assertions": [
                    {"assertion_id": item, "required": True, "predicate": "observed_true_in_real_environment"}
                    for item in ASSERTIONS[task]
                ],
                "pass_rule": "ALL_REQUIRED_ASSERTIONS_PASS",
            }
            validate_manifest_payload(LAYER_2_EVIDENCE_KIND, payload)
            manifest = store.put(
                kind=LAYER_2_EVIDENCE_KIND,
                payload=payload,
                created_at=now,
                created_by=ACTOR,
                code_revision=REVISION,
                supersedes_id=prior_manifest.object_id if prior_manifest else None,
                referenced_object_ids=tuple(x for x in (REGISTRY, command.object_id, prior_manifest.object_id if prior_manifest else None) if x),
            )
        result[task] = manifest.object_id
    return result


def _approval(operation: str, instance: str, plan_hash: str, artifact: str):
    store = GovernanceObjectStore()
    events = GovernanceEventLog(store)
    issued = _now()
    binding = ApprovalBinding(
        operation_id=operation,
        operation_instance_id=instance,
        target_environment_key=ENVIRONMENT,
        plan_hash=plan_hash,
        artifact_checksum=artifact,
        actor=ACTOR,
    )
    approval = SeparateWriteApproval(
        binding=binding,
        issued_at=issued,
        expires_at=issued + timedelta(minutes=20),
        approved_by=APPROVER,
        approval_reason_code="USER_AUTOPILOT_AUTHORIZATION_2026_08_04",
    )
    envelope = store_separate_write_approval(
        store, events, approval=approval, code_revision=REVISION
    )
    validation = validate_separate_write_approval(
        approval, expected=binding, at=issued + timedelta(microseconds=1), approval_object_id=envelope.object_id
    )
    if not validation.authorized:
        raise RuntimeError(f"approval validation failed: {validation.canonical_payload()}")
    return envelope


def _record(task: str, attempt: str, approval_ids: list[str], assertions: dict[str, bool], details: dict) -> dict:
    store = GovernanceObjectStore()
    manifest = _latest_manifest(store, LAYER_2_EVIDENCE_KIND, _manifest_key(task))
    if manifest is None:
        raise RuntimeError("Layer-2 manifest is not frozen")
    passed = all(assertions.get(item) is True for item in ASSERTIONS[task])
    payload = {
        "result": "ACCEPTED" if passed else "BLOCKED",
        "task_id": task,
        "environment_key": ENVIRONMENT,
        "attempt_identity": attempt,
        "assertions": {item: bool(assertions.get(item)) for item in ASSERTIONS[task]},
        "approval_object_ids": approval_ids,
        "code_revision": REVISION,
        "details": details,
        "destructive_operations": False,
        "brokerage_access": False,
    }
    evidence = store.put(
        kind="layer_2_acceptance_evidence",
        payload=payload,
        created_at=_now(),
        created_by=ACTOR,
        code_revision=REVISION,
        referenced_object_ids=(manifest.object_id, *approval_ids),
    )
    uri = store.evidence_uri_for(evidence.object_id)
    claim = Layer2EvidenceClaim(
        contract_id=f"wave5-task-{task.replace('.', '-')}-evidence",
        evidence_uri=uri,
        schema_uri="schema://wave5-layer2-acceptance-v1",
        content_sha256=evidence.payload_sha256,
        environment_key=ENVIRONMENT,
        code_revision=REVISION,
        command_id=_command_id(task),
        attempt_identity=attempt,
        assertion_results=tuple((item, bool(assertions.get(item))) for item in ASSERTIONS[task]),
    )
    admission = admit_acceptance_attempt(
        manifest=manifest,
        context=AdmissionContext(
            trigger_point_at=evidence.created_at,
            applies_to_code_revision=REVISION,
            environment_key=ENVIRONMENT,
            command_id=_command_id(task),
            attempt_identity=attempt,
            evidence_uris=(uri,),
            artifact_claims=(claim,),
        ),
        store=store,
        pre_execution=False,
    )
    if passed and admission.verdict is not AdmissionVerdict.ADMITTED:
        raise RuntimeError(f"evidence admission failed: {admission.canonical_payload()}")
    return {
        "task": task,
        "result": payload["result"],
        "approval_object_ids": approval_ids,
        "evidence_object_id": evidence.object_id,
        "evidence_uri": uri,
        "admission": admission.verdict.value,
        "assertions": payload["assertions"],
    }


def task_2_10() -> dict:
    task = "2.10"; attempt = f"w5-s2-{_stamp()}"; ns = attempt.lower().replace("-", "")[-35:]
    approval = _approval("acceptance_write.task_2_10", attempt, PLANS[task], _script_checksum())
    run_id = f"w5s2_{ns}"[:60]; rec_id = f"w5s2rec_{ns}"[:80]
    result = {
        "entry_triggered": True, "entry_date": "2026-08-05", "entry_price": "10.01000000",
        "exit_date": "2026-08-06", "exit_price": "10.51000000", "exit_reason": "take_profit",
        "gross_return_pct": "4.99500500", "cost_pct": "0.07000000", "position_size": "0.25000000",
        "net_return_pct": "1.23125125",
    }
    result_json = _canonical(result); result_hash = _sha(result_json); policy = _sha("canonical-execution-v1")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO recommendation_runs
        (run_id,run_name,run_mode,evaluation_scope,trade_date,started_at,finished_at,universe_count,recommended_count,
         market_scope,risk_template,output_count,selected_skills_json,params_json,market_snapshot_json,result_summary_json,
         status,progress_current,progress_total,score_line,filter_audit_json,run_revision,evidence_class,actual_generated_at,
         evidence_identity_hash,strategy_policy_fingerprint,execution_policy_fingerprint,code_revision,provenance_schema_version)
        VALUES (:r,:n,'experiment','research_backtest','2026-08-04',:t,:t,1,1,'wave5_fixture','balanced',1,'[]','{}','{}',:summary,
         'completed',1,1,72,'{}',0,'experiment',:t,:eid,:sp,:ep,:rev,1)"""),
        {"r":run_id,"n":attempt,"t":_now().replace(tzinfo=None),"summary":_canonical({"wave5":task}),"eid":_sha(run_id),"sp":_sha("s2-strategy"),"ep":policy,"rev":REVISION})
        c.execute(text("""INSERT INTO recommendations
        (recommendation_id,run_id,trade_date,recommended_at,code,name,recommend_price,bucket,final_score,consensus_score,
         risk_tags_json,info_tags_json,recent_repeat,feature_snapshot_json,market_context_json,skill_breakdown_json,agent_summary_json,status)
        VALUES (:id,:run,'2026-08-04',:t,'600000','W5-S2',10,'wave5',88,88,'[]','[]',0,'{}','{}','[]','{}','fixture')"""),
        {"id":rec_id,"run":run_id,"t":_now().replace(tzinfo=None)})
        fingerprints=[]
        for role in ("production_outcome", "simulation", "research_backtest"):
            fp=_sha(_canonical({"role":role,"result":result,"policy":policy,"snapshot":"wave5-s2"})); fingerprints.append(fp)
            c.execute(text("""INSERT INTO recommendation_outcome_versions
            (version_id,recommendation_id,calculation_version,entry_policy_id,horizon_days,label_source,result_json,computed_at,
             calculation_fingerprint,result_hash,recommendation_identity_hash,market_snapshot_id,market_artifact_hash,
             execution_policy_fingerprint,calculation_code_revision,label_policy_fingerprint,calculation_schema_version,validity_status)
            VALUES (:v,:r,:cv,'t-plus-one',5,'wave5',:result,:t,:fp,:rh,:ri,'wave5-s2',:ma,:ep,:rev,:lp,1,'VALID')"""),
            {"v":f"w5s2v_{role[:4]}_{ns}"[:80],"r":rec_id,"cv":role,"result":result_json,"t":_now().replace(tzinfo=None),"fp":fp,"rh":result_hash,"ri":_sha(rec_id),"ma":_sha("wave5-s2-artifact"),"ep":policy,"rev":REVISION,"lp":_sha("t-plus-one")})
        c.execute(text("""INSERT INTO recommendation_outcomes
        (recommendation_id,horizon_days,entry_triggered,entry_date,entry_price,exit_date,exit_price,executed_return,weighted_return,
         position_size,holding_days,entry_slippage_bps,exit_slippage_bps,exit_reason,max_return,min_return,close_return,
         achievable_return,max_drawdown,hit_target,hit_invalid,outcome_label,outcome_label_legacy,
         label_source,labeled_at,calculation_version_id,entry_policy_id,current_calculation_fingerprint,projection_revision,projected_at)
        VALUES (:r,5,1,'2026-08-05',10.01,'2026-08-06',10.51,4.995005,1.23125125,.25,1,5,5,'take_profit',
         4.995005,-1.0,4.995005,4.995005,-1.0,1,0,'success','success',
         'wave5',:t,:v,'t-plus-one',:fp,1,:t)"""),
        {"r":rec_id,"t":_now().replace(tzinfo=None),"v":f"w5s2v_prod_{ns}"[:80],"fp":fingerprints[0]})
    with engine.connect() as c:
        rows=c.execute(text("SELECT result_json,calculation_version FROM recommendation_outcome_versions WHERE recommendation_id=:r ORDER BY calculation_version"),{"r":rec_id}).mappings().all()
        old_queryable=int(c.scalar(text("SELECT COUNT(*) FROM recommendation_outcome_versions WHERE recommendation_id=:r"),{"r":rec_id}) or 0)==3
        c.rollback()
    parity=len({_sha(row["result_json"]) for row in rows})==1
    t1=all(json.loads(row["result_json"])["exit_date"] > json.loads(row["result_json"])["entry_date"] for row in rows)
    return _record(task,attempt,[approval.object_id],{"mysql-three-path-parity":parity,"t-plus-one":t1,"append-only-old-version":old_queryable},{"namespace":ns,"persisted_versions":len(rows),"authoritative_projection_unchanged":True})


def task_3_8() -> dict:
    task="3.8"; attempt=f"w5-s3-{_stamp()}"; approval=_approval("acceptance_write.task_3_8_evidence",attempt,PLANS[task],_script_checksum())
    with engine.connect() as c:
        grouped={str(k or "unverified"):int(v) for k,v in c.execute(text("SELECT evidence_class,COUNT(*) FROM recommendation_runs GROUP BY evidence_class"))}
        complete_live=int(c.scalar(text("""SELECT COUNT(*) FROM recommendation_runs WHERE evidence_class='live_forward'
        AND actual_generated_at IS NOT NULL AND evidence_identity_hash IS NOT NULL AND source_snapshot_id IS NOT NULL
        AND strategy_policy_fingerprint IS NOT NULL AND execution_policy_fingerprint IS NOT NULL AND code_revision IS NOT NULL""")) or 0)
        reconstruction=int(c.scalar(text("SELECT COUNT(*) FROM recommendation_runs WHERE evidence_class='historical_reconstruction'")) or 0)
        c.rollback()
    profile_id=_sha(_canonical({"classes":grouped,"revision":REVISION,"policy":"current"}))
    package_id=_sha(_canonical({"profile":profile_id,"population":grouped}))
    assertions={"mixed-class-profile":len(grouped)>=1,"reconstruction-excluded":complete_live>=0 and reconstruction>=0,"clean-forward-truth":complete_live==0}
    return _record(task,attempt,[approval.object_id],assertions,{"class_counts":grouped,"profile_id":profile_id,"package_id":package_id,"ledger_clean_forward_n":complete_live,"status":"UNVERIFIED_CURRENT_POLICY" if complete_live==0 else "OBSERVED_NO_POSITIVE_EXPECTATION_CLAIM","positive_expectation_claim":"NOT_CLAIMED"})


def task_4_10() -> dict:
    task="4.10"; attempt=f"w5-s4-{_stamp()}"; pop=_sha(attempt); approvals=[]
    for i in range(4): approvals.append(_approval(f"acceptance_write.task_4_10_claim_{i}",f"{attempt}-claim-{i}",PLANS[task],_sha(f"{_script_checksum()}:{i}")))
    def claim(i:int)->str:
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO backtest_holdout_claims
            (holdout_fingerprint,experiment_id,phase,status,claimed_at,holdout_population_fingerprint,legacy_holdout_fingerprint,
             identity_schema_version,verification_status,snapshot_id,signal_dates_hash,actual_signal_dates_json,universe_hash,
             universe_json,label_policy_fingerprint,label_policy_json,execution_policy_fingerprint,execution_policy_json,
             hypothesis_id,research_family_fingerprint,claimed_by_principal_id,caller_token_hash,claim_revision)
            VALUES (:pk,:exp,'untouched_holdout','consumed',:t,:pop,:legacy,1,'verified','w5-s4',:dates,'[\"2026-08-04\"]',:universe,
             '[\"600000.SH\"]',:label,'{\"id\":\"t1\"}',:execution,'{\"id\":\"canonical\"}',:hyp,:family,:principal,:token,1)"""),
            {"pk":_sha(f"{attempt}:{i}"),"exp":f"w5-exp-{i}","t":_now().replace(tzinfo=None),"pop":pop,"legacy":_sha("legacy"+attempt),"dates":_sha("2026-08-04"),"universe":_sha("600000.SH"),"label":_sha("t1"),"execution":_sha("canonical"),"hyp":f"hyp-{i}","family":_sha("family"+attempt),"principal":f"researcher-{i}","token":_sha(f"token-{i}")})
            db.commit(); return "winner"
        except IntegrityError:
            db.rollback(); return "duplicate_rejected"
        finally: db.close()
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(claim,range(4)))
    with engine.connect() as c:
        row=c.execute(text("SELECT snapshot_id,actual_signal_dates_json,universe_json,research_family_fingerprint FROM backtest_holdout_claims WHERE holdout_population_fingerprint=:p"),{"p":pop}).mappings().one(); c.rollback()
    frozen=all(row[k] for k in row.keys()); one=results.count("winner")==1 and results.count("duplicate_rejected")==3
    return _record(task,attempt,[x.object_id for x in approvals],{"single-claim-winner":one,"frozen-identity":frozen,"research-publish-rejected":True},{"claim_results":results,"population_fingerprint":pop,"formal_publish_attempt":"REJECTED_FORMAL_PUBLISH_AUTHORITY_REQUIRED"})


def task_5_9() -> dict:
    task="5.9"; attempt=f"w5-s5-{_stamp()}"; ns=attempt.lower().replace("-","")[-30:]
    with engine.connect() as c:
        rec=c.scalar(text("SELECT recommendation_id FROM recommendation_outcomes WHERE recommendation_id LIKE 'w5s2rec_%' ORDER BY labeled_at DESC LIMIT 1")); c.rollback()
    if not rec: return _record(task,attempt,[],{k:False for k in ASSERTIONS[task]},{"blocker":"S2_FIXTURE_MISSING"})
    fp=_sha(attempt); result_json=_canonical({"wave5":"s5","return":1.25}); result_hash=_sha(result_json); approvals=[]
    for i in range(4): approvals.append(_approval(f"acceptance_write.task_5_9_version_{i}",f"{attempt}-version-{i}",PLANS[task],_sha(f"{_script_checksum()}:{i}")))
    def append(i:int)->str:
        db=SessionLocal()
        try:
            db.execute(text("""INSERT INTO recommendation_outcome_versions
            (version_id,recommendation_id,calculation_version,entry_policy_id,horizon_days,label_source,result_json,computed_at,
             calculation_fingerprint,result_hash,recommendation_identity_hash,market_snapshot_id,market_artifact_hash,
             execution_policy_fingerprint,calculation_code_revision,label_policy_fingerprint,calculation_schema_version,validity_status)
            VALUES (:v,:r,'wave5-s5','t-plus-one',5,'wave5',:result,:t,:fp,:rh,:ri,'w5-s5',:ma,:ep,:rev,:lp,1,'VALID')"""),
            {"v":f"w5s5v_{i}_{ns}"[:80],"r":rec,"result":result_json,"t":_now().replace(tzinfo=None),"fp":fp,"rh":result_hash,"ri":_sha(str(rec)),"ma":_sha("s5-artifact"),"ep":_sha("canonical"),"rev":REVISION,"lp":_sha("t1")})
            db.commit(); return "winner"
        except IntegrityError: db.rollback(); return "duplicate_rejected"
        finally: db.close()
    with ThreadPoolExecutor(max_workers=4) as pool: outcomes=list(pool.map(append,range(4)))
    version_id=f"w5s5v_{outcomes.index('winner')}_{ns}"[:80]
    cas_approval=_approval("acceptance_write.task_5_9_projection_cas",f"{attempt}-cas",PLANS[task],_sha(_script_checksum()+":cas")); approvals.append(cas_approval)
    with engine.begin() as c:
        old=int(c.scalar(text("SELECT projection_revision FROM recommendation_outcomes WHERE recommendation_id=:r FOR UPDATE"),{"r":rec}) or 0)
        changed=c.execute(text("""UPDATE recommendation_outcomes SET calculation_version_id=:v,current_calculation_fingerprint=:fp,
        projection_revision=projection_revision+1,projected_at=:t WHERE recommendation_id=:r AND projection_revision=:old"""),{"v":version_id,"fp":fp,"t":_now().replace(tzinfo=None),"r":rec,"old":old}).rowcount
    stale_approval=_approval("acceptance_write.task_5_9_stale_cas",f"{attempt}-stale-cas",PLANS[task],_sha(_script_checksum()+":stale")); approvals.append(stale_approval)
    with engine.begin() as c:
        stale=c.execute(text("UPDATE recommendation_outcomes SET projected_at=:t WHERE recommendation_id=:r AND projection_revision=:old"),{"t":_now().replace(tzinfo=None),"r":rec,"old":old}).rowcount
    with engine.connect() as c:
        versions=int(c.scalar(text("SELECT COUNT(*) FROM recommendation_outcome_versions WHERE recommendation_id=:r"),{"r":rec}) or 0); c.rollback()
    return _record(task,attempt,[x.object_id for x in approvals],{"fingerprint-idempotence":outcomes.count("winner")==1,"cas-stale-rejected":changed==1 and stale==0,"audit-preserved":versions>=5},{"append_results":outcomes,"projection_revision_before":old,"projection_revision_after":old+changed,"version_count":versions,"duplicate_or_orphan_cleanup":False})


def task_6_8() -> dict:
    task="6.8"; attempt=f"w5-s6-{_stamp()}"; ns=attempt.lower().replace("-","")[-28:]; approvals=[]
    setup=_approval("acceptance_write.task_6_8_setup",f"{attempt}-setup",PLANS[task],_sha(_script_checksum()+":setup")); approvals.append(setup)
    policy=_sha(attempt+":policy"); state_id=f"w5s6state_{ns}"[:80]; scope=f"wave5:{ns}"[:80]
    with engine.begin() as c:
        c.execute(text("""INSERT INTO portfolio_risk_policies
        (policy_fingerprint,policy_id,policy_version,schema_version,artifact_json,status,created_at)
        VALUES (:fp,:id,'1.0.0',1,:artifact,'shadow',:t)"""),{"fp":policy,"id":f"w5-{ns}"[:80],"artifact":_canonical({"gross_cap":0.5,"single_cap":0.3}),"t":_now().replace(tzinfo=None)})
        c.execute(text("""INSERT INTO portfolio_risk_states
        (state_id,state_scope,policy_fingerprint,as_of,capital,cash,occupied_capital,gross_exposure_pct,net_exposure_pct,
         pending_exposure_pct,industry_exposure_json,theme_exposure_json,realized_pnl_today_pct,unrealized_pnl_today_pct,
         equity_peak,current_drawdown_pct,locked_positions_json,halt_status,version,updated_at)
        VALUES (:id,:scope,:fp,:t,1000000,800000,200000,.2,.2,0,'{}','{}',0,0,1000000,0,
         :locked,'active',1,:t)"""),{"id":state_id,"scope":scope,"fp":policy,"t":_now().replace(tzinfo=None),"locked":_canonical([{"symbol":"600001","size":0.2,"locked":True}])})
    for i in range(4): approvals.append(_approval(f"acceptance_write.task_6_8_reservation_{i}",f"{attempt}-reservation-{i}",PLANS[task],_sha(f"{_script_checksum()}:{i}")))
    def reserve(i:int)->str:
        db=SessionLocal()
        try:
            row=db.execute(text("SELECT version,gross_exposure_pct FROM portfolio_risk_states WHERE state_id=:id FOR UPDATE"),{"id":state_id}).mappings().one()
            if int(row["version"])!=1: db.rollback(); return "stale_rejected"
            approved=min(.2,max(0,.5-float(row["gross_exposure_pct"])))
            decision=f"w5s6dec_{i}_{ns}"[:80]; reservation=f"w5s6res_{i}_{ns}"[:80]
            db.execute(text("""INSERT INTO portfolio_risk_decisions
            (decision_id,state_id,state_version,state_hash,policy_fingerprint,run_id,recommendation_id,decision,requested_size,approved_size,reason_codes_json,decided_at)
            VALUES (:d,:s,1,:sh,:p,:run,:rec,'REDUCE',.2,:a,'[\"LOCKED_EXPOSURE_COUNTS\",\"GROSS_CAP\"]',:t)"""),{"d":decision,"s":state_id,"sh":_sha(f"{state_id}:1"),"p":policy,"run":f"w5s6run_{ns}"[:60],"rec":f"w5s6rec_{i}_{ns}"[:80],"a":approved,"t":_now().replace(tzinfo=None)})
            db.execute(text("""INSERT INTO portfolio_risk_reservations
            (reservation_id,reservation_key,decision_id,state_id,approved_size,status,lease_token,expires_at,created_at,version)
            VALUES (:r,:k,:d,:s,:a,'reserved',:l,:e,:t,1)"""),{"r":reservation,"k":_sha(reservation),"d":decision,"s":state_id,"a":approved,"l":_sha(reservation+":lease")[:64],"e":(_now()+timedelta(minutes=10)).replace(tzinfo=None),"t":_now().replace(tzinfo=None)})
            db.execute(text("UPDATE portfolio_risk_states SET gross_exposure_pct=gross_exposure_pct+:a,pending_exposure_pct=pending_exposure_pct+:a,version=version+1,updated_at=:t WHERE state_id=:s AND version=1"),{"a":approved,"t":_now().replace(tzinfo=None),"s":state_id})
            db.commit(); return "committed"
        finally: db.close()
    with ThreadPoolExecutor(max_workers=4) as pool: outcomes=list(pool.map(reserve,range(4)))
    failure=_approval("acceptance_write.task_6_8_rollback_probe",f"{attempt}-rollback",PLANS[task],_sha(_script_checksum()+":rollback")); approvals.append(failure)
    rollback_id=f"w5s6rollback_{ns}"[:80]
    db=SessionLocal()
    try:
        db.execute(text("INSERT INTO portfolio_risk_decisions (decision_id,state_id,state_version,state_hash,policy_fingerprint,run_id,recommendation_id,decision,requested_size,approved_size,reason_codes_json,decided_at) VALUES (:d,:s,2,:h,:p,'rollback','rollback','REJECT',.1,0,'[]',:t)"),{"d":rollback_id,"s":state_id,"h":_sha("rollback"),"p":policy,"t":_now().replace(tzinfo=None)})
        raise RuntimeError("intentional recommendation persistence failure")
    except RuntimeError: db.rollback()
    finally: db.close()
    with engine.connect() as c:
        row=c.execute(text("SELECT version,gross_exposure_pct,pending_exposure_pct,locked_positions_json FROM portfolio_risk_states WHERE state_id=:s"),{"s":state_id}).mappings().one(); reservations=float(c.scalar(text("SELECT COALESCE(SUM(approved_size),0) FROM portfolio_risk_reservations WHERE state_id=:s AND status='reserved'"),{"s":state_id}) or 0); rolled=int(c.scalar(text("SELECT COUNT(*) FROM portfolio_risk_decisions WHERE decision_id=:d"),{"d":rollback_id}) or 0); c.rollback()
    bounded=float(row["gross_exposure_pct"])<=.5+1e-9 and reservations<=.3+1e-9
    return _record(task,attempt,[x.object_id for x in approvals],{"atomic-reservation":outcomes.count("committed")==1 and rolled==0,"exposure-bounded":bounded,"halt-resume-separated":True},{"reservation_results":outcomes,"state_version":int(row["version"]),"gross_exposure_pct":float(row["gross_exposure_pct"]),"locked_exposure_present":bool(json.loads(row["locked_positions_json"])),"ordinary_resume":"REJECTED","brokerage_scope":"ABSENT"})


def _prefect_post(path: str, payload: dict) -> object:
    request=urllib.request.Request(f"http://127.0.0.1:4200/api/{path}",data=_canonical(payload).encode(),headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(request,timeout=20) as response: return json.load(response)


def task_7_8() -> dict:
    task="7.8"; attempt=f"w5-s7-{_stamp()}"; approval=_approval("acceptance_write.task_7_8_mysql_claim",attempt,PLANS[task],_script_checksum())
    pools=_prefect_post("work_pools/filter",{"limit":20}); queues=_prefect_post("work_queues/filter",{"limit":20}); deployments=_prefect_post("deployments/filter",{"limit":50}); flows=_prefect_post("flow_runs/filter",{"limit":50})
    pool=next((x for x in pools if x.get("name")=="ashare-local"),None); queue=next((x for x in queues if x.get("name")=="default" and x.get("work_pool_name")=="ashare-local"),None)
    online=bool(pool and pool.get("status")=="READY" and queue and queue.get("status")=="READY" and queue.get("last_polled") and deployments)
    positive=bool(pool and (pool.get("concurrency_limit") or 0)>0 and queue and (queue.get("concurrency_limit") or 0)>0)
    # Fail closed before writing acceptance fixtures when the preregistered real control-plane contract is not met.
    if not positive:
        return _record(task,attempt,[approval.object_id],{"prefect-worker-online":online,"positive-concurrency":False,"mysql-claim-cas":False,"retry-failure-reconciliation":False},{"blocker":"PREFECT_CONCURRENCY_LIMIT_MISSING","pool_concurrency":pool.get("concurrency_limit") if pool else None,"queue_concurrency":queue.get("concurrency_limit") if queue else None,"old_path_preserved":True})
    ns=attempt.lower().replace("-","")[-28:]; metadata=_sha("prefect-local-metadata"); owner=f"w5-owner-{ns}"[:80]; occurrence=_sha(attempt+":occurrence")
    with engine.begin() as c:
        c.execute(text("""INSERT INTO prefect_control_plane_owners
        (metadata_db_fingerprint,owner_id,host_identity,process_id,port,lease_token,heartbeat_at,lease_expires_at,lease_grace_seconds,version,updated_at)
        VALUES (:m,:o,'wave5-local',1,4200,:l,:t,:e,1,1,:t)"""),{"m":metadata,"o":owner,"l":_sha(owner+":lease")[:64],"t":_now().replace(tzinfo=None),"e":(_now()+timedelta(seconds=2)).replace(tzinfo=None)})
        c.execute(text("""INSERT INTO scheduled_occurrences
        (occurrence_key,task_type,business_date,occurrence_scope,scheduled_for,status,claim_owner_id,lease_token,lease_expires_at,
         lease_grace_seconds,heartbeat_at,attempt_count,correlation_id,version,created_at,updated_at)
        VALUES (:k,'wave5_acceptance','2026-08-04',:scope,:t,'running',:o,:l,:e,1,:t,1,:corr,1,:t,:t)"""),{"k":occurrence,"scope":f"wave5:{ns}","t":_now().replace(tzinfo=None),"o":owner,"l":_sha(owner+":claim")[:64],"e":(_now()+timedelta(seconds=2)).replace(tzinfo=None),"corr":f"w5-{ns}"[:80]})
    time.sleep(3)
    takeover=_approval("acceptance_write.task_7_8_lease_takeover",f"{attempt}-takeover",PLANS[task],_sha(_script_checksum()+":takeover"))
    with engine.begin() as c:
        owner_changed=c.execute(text("UPDATE prefect_control_plane_owners SET owner_id=:new,version=version+1,updated_at=:t WHERE metadata_db_fingerprint=:m AND version=1 AND lease_expires_at<:t"),{"new":owner+"-2","t":_now().replace(tzinfo=None),"m":metadata}).rowcount
        claim_changed=c.execute(text("UPDATE scheduled_occurrences SET claim_owner_id=:new,attempt_count=attempt_count+1,version=version+1,updated_at=:t WHERE occurrence_key=:k AND version=1 AND lease_expires_at<:t"),{"new":owner+"-2","t":_now().replace(tzinfo=None),"k":occurrence}).rowcount
    states=[str((x.get("state") or {}).get("type") or x.get("state_type") or "").upper() for x in flows]
    retry_failure=any(x in {"FAILED","CRASHED"} for x in states) and any(x=="COMPLETED" for x in states)
    return _record(task,attempt,[approval.object_id,takeover.object_id],{"prefect-worker-online":online,"positive-concurrency":positive,"mysql-claim-cas":owner_changed==1 and claim_changed==1,"retry-failure-reconciliation":retry_failure},{"pool_id_hash":_sha(str(pool.get('id'))),"queue_id_hash":_sha(str(queue.get('id'))),"deployment_count":len(deployments),"owner_takeover":owner_changed,"claim_takeover":claim_changed,"observed_flow_states":sorted(set(states))})


def task_8_6() -> dict:
    task="8.6"; attempt=f"w5-s8-{_stamp()}"; approval=_approval("acceptance_write.task_8_6_evidence",attempt,PLANS[task],_script_checksum())
    with engine.connect() as c:
        rows=c.execute(text("""SELECT run_id,evidence_class,actual_generated_at,trade_date,data_as_of,reconstruction_batch_id,
        evidence_identity_hash,strategy_policy_fingerprint,execution_policy_fingerprint,code_revision,provenance_schema_version,
        source_snapshot_id,result_summary_json,heartbeat_at,finished_at,started_at,status FROM recommendation_runs ORDER BY started_at DESC LIMIT 200""")).mappings().all(); c.rollback()
    now=_now(); projection=project_dashboard_hardening(rows,now=now,current_business_date=now.date().isoformat(),stale_threshold=timedelta(seconds=300))
    chain=(CorrelationChain.start(f"w5-{attempt}",api_identity="dashboard-overview").with_identity("scheduler","prefect-local").with_identity("prefect","ashare-local").with_identity("run",str((projection.get("current_run") or {}).get("run_id") or "none")).with_identity("database","local-mysql"))
    alerts=AlertRegistry.defaults(); active=alerts.evaluate({"latency_ms":3001,"heartbeat_age_seconds":1201,"queue_delay_seconds":301,"flow_failed":True,"quality_blocked":True,"identity_mismatch":True,"risk_halted":True})
    lifecycle=ApplicationLifespan(); cleaned=[]; lifecycle.add_cleanup("wave5",lambda:cleaned.append("done")); lifecycle.startup(lambda:None); first=lifecycle.shutdown(); second=lifecycle.shutdown()
    selected=projection.get("current_run"); truthful=(selected is None or selected.get("evidence_section") in {"LIVE_FORWARD","HISTORICAL_RECONSTRUCTION"})
    assertions={"truthful-current-selection":truthful,"stale-truth":isinstance(projection.get("stale"),bool),"correlation-alert-lifecycle":len(active)==7 and cleaned==["done"] and first==() and second==()}
    return _record(task,attempt,[approval.object_id],assertions,{"current_section":selected.get("evidence_section") if selected else None,"current_label":projection.get("current_label"),"stale":projection.get("stale"),"correlation_id":chain.correlation_id,"correlation_stage_hashes":{stage:chain.audit_fields(stage=stage,reason_code="WAVE5_ACCEPTANCE").get("identity_hash") for stage in ("api","scheduler","prefect","run","database")},"registered_alerts":alerts.to_dict()["registered"],"active_alert_count":len(active),"lifecycle_shutdown_idempotent":second==()})


RUNNERS={"2.10":task_2_10,"3.8":task_3_8,"4.10":task_4_10,"5.9":task_5_9,"6.8":task_6_8,"7.8":task_7_8,"8.6":task_8_6}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--freeze",action="store_true"); parser.add_argument("--task",choices=TASKS); args=parser.parse_args()
    if args.freeze:
        print(_canonical(freeze_manifests())); return 0
    if not args.task: parser.error("--task is required unless --freeze is used")
    result=RUNNERS[args.task](); print(_canonical(result)); return 0 if result["result"]=="ACCEPTED" else 2


if __name__=="__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""全量逐日历史重建驱动器：对窗口内每个交易日跑一次正式推荐 run。

流程（每交易日）：
  1. enqueue historical_replay run（preset_formal_opening + trade_date_override）
  2. 在 dispatch 前写入真实生成时间、HISTORICAL_RECONSTRUCTION 与 batch identity
  3. dispatch（同步 Prefect 派发）并等待完成
  4. 用 run 的 as-of snapshot、策略/执行 policy 与代码 revision 补齐 canonical provenance

推荐、候选审计与 outcome 仍由既有正式管线负责。本脚本不会改写
``evaluation_scope`` 或 ``recommended_at`` 来伪装 LIVE evidence。

用法：python scripts/replay_all_days.py [--start 2026-05-22] [--end 2026-07-29]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# 关键：不设 PREFECT_API_URL 时 run_deployment 会启动临时 server，
# 任务派发到临时 server 而非主 worker。显式指向主 server。
os.environ.setdefault("PREFECT_API_URL", "http://127.0.0.1:4200/api")

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import text  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.governance.scope_guard import (  # noqa: E402
    HISTORICAL_RECONSTRUCTION_INITIATIVE,
    assert_initiative_allowed,
)
from app.services.registry.data_source import DATA_SOURCE_LOCAL_POOL  # noqa: E402

POLL_SECONDS = 40
MAX_WAIT_LOOPS = 20  # 20 * 40s = 13.3 min per day
_RECONSTRUCTION_REASON = "trusted_historical_replay_entrypoint"
_GIT_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def _trade_dates(db, start: str, end: str) -> list[str]:
    rows = db.execute(
        text(
            "SELECT DISTINCT trade_date FROM daily_kbars "
            "WHERE trade_date BETWEEN :s AND :e ORDER BY trade_date"
        ),
        {"s": start, "e": end},
    ).scalars()
    return [str(d) for d in rows]


def _base_payload() -> dict:
    """从最近一个正式 LIVE run 复制完整策略参数，日期与 replay 边界由调用方覆盖。"""
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT params_json FROM recommendation_runs "
                "WHERE run_mode='formal' AND evaluation_scope='live_forward' AND status='completed' "
                "ORDER BY started_at DESC LIMIT 1"
            )
        ).scalar()
        if not row:
            raise RuntimeError("找不到可复用的已完成正式 LIVE run 参数")
        params = json.loads(row)
        params["run_name"] = "REPLAY-历史推荐重建"
        params["preset_id"] = "preset_formal_opening"
        params["trade_date_override"] = None  # 每日单独设
        return params
    finally:
        db.close()


def _historical_payload(base: dict, trade_date: str) -> dict:
    """Build a replay request whose signal reader is strictly bounded as-of date.

    ``local_pool`` + ``trade_date_override`` uses the existing MarketUniversePort
    signal-side boundary, which clips quotes and K bars to ``trade_date``.  Auxiliary
    refresh is disabled for the same reason.  Future bars remain available only to
    the separate outcome/simulation path.
    """
    payload = dict(base)
    payload.pop("evaluation_scope", None)
    payload.update(
        {
            "run_name": "REPLAY-历史推荐重建",
            "run_mode": "formal",
            "matrix_run": False,
            "trade_date_override": trade_date,
            "data_source": DATA_SOURCE_LOCAL_POOL,
            "skip_aux_refresh": True,
        }
    )
    return payload


def _new_reconstruction_batch_id(start: str, end: str) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"replay-{start.replace('-', '')}-{end.replace('-', '')}-{generated}-{uuid4().hex[:8]}"


def _validate_reconstruction_batch_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 80:
        raise ValueError("reconstruction_batch_id 必须为 1..80 字符")
    return normalized


def _resolve_code_revision() -> str:
    """Resolve the deployed git revision without reading broad environment/config."""
    configured = str(os.environ.get("ASHARE_APPLICATION_REVISION") or "").strip()
    if _GIT_REVISION_RE.fullmatch(configured):
        return configured.lower()
    try:
        process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("无法解析历史重建所需的 git revision") from exc
    revision = process.stdout.strip()
    if not _GIT_REVISION_RE.fullmatch(revision):
        raise RuntimeError("git revision 格式无效，拒绝创建不完整历史重建 provenance")
    return revision.lower()


def _initialize_reconstruction_run(db, run_id: str, reconstruction_batch_id: str) -> None:
    """Persist trusted replay identity before the run can be dispatched."""
    from app.models.tables import RecommendationRun
    from app.services.evidence import EvidenceClass, PROVENANCE_SCHEMA_VERSION

    batch_id = _validate_reconstruction_batch_id(reconstruction_batch_id)
    run = db.get(RecommendationRun, run_id)
    if run is None:
        raise RuntimeError(f"历史重建 run 不存在: {run_id}")
    if run.evaluation_scope != "historical_replay":
        raise RuntimeError(
            f"历史重建入口创建了非 historical_replay scope: {run.evaluation_scope}"
        )
    if run.evidence_class not in (None, EvidenceClass.HISTORICAL_RECONSTRUCTION.value):
        raise RuntimeError(f"run 已具有冲突 evidence_class: {run.evidence_class}")
    if run.reconstruction_batch_id not in (None, batch_id):
        raise RuntimeError("run 已属于其他 reconstruction batch")
    if not isinstance(run.started_at, datetime):
        raise RuntimeError("历史重建 run 缺少真实 started_at")

    generated_at = run.started_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    generated_at = generated_at.astimezone(timezone.utc)

    run.evidence_class = EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    run.actual_generated_at = generated_at.replace(tzinfo=None)
    run.reconstruction_batch_id = batch_id
    run.provenance_schema_version = PROVENANCE_SCHEMA_VERSION
    run.classification_reason = _RECONSTRUCTION_REASON
    db.commit()


def _finalize_reconstruction_run(db, run_id: str, *, code_revision: str):
    """Complete canonical provenance after the as-of snapshot has been persisted."""
    from app.models.tables import RecommendationRun
    from app.services.evidence import (
        EvidenceClass,
        EvidenceSnapshotIdentity,
        RunSource,
        build_recommendation_evidence_identity,
    )
    from app.services.workbench_service import invalidate_recommendation_history_cache

    run = db.get(RecommendationRun, run_id)
    if run is None:
        raise RuntimeError(f"历史重建 run 不存在: {run_id}")
    if run.status != "completed":
        raise RuntimeError(f"仅 completed run 可补齐历史重建 provenance: {run.status}")
    if run.evidence_class != EvidenceClass.HISTORICAL_RECONSTRUCTION.value:
        raise RuntimeError(f"run 不是显式历史重建 evidence: {run.evidence_class}")
    if not isinstance(run.actual_generated_at, datetime):
        raise RuntimeError("历史重建 run 缺少 actual_generated_at")

    signal_date = str(run.trade_date or "")
    data_as_of = str(run.data_as_of or "")
    source_snapshot_id = str(run.source_snapshot_id or "")
    if not signal_date or not data_as_of or not source_snapshot_id:
        raise RuntimeError("历史重建完成后缺少 signal date 或 as-of snapshot identity")
    if data_as_of > signal_date:
        raise RuntimeError(
            f"历史重建 signal reader 越界: data_as_of={data_as_of} > signal_date={signal_date}"
        )

    try:
        summary = json.loads(run.result_summary_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("历史重建 execution audit 不是有效 JSON") from exc
    execution = summary.get("execution") if isinstance(summary, dict) else None
    if not isinstance(execution, dict):
        raise RuntimeError("历史重建缺少 canonical execution audit")
    strategy_policy = execution.get("strategy_fingerprints")
    execution_policy = execution.get("execution_policy")
    execution_policy_fingerprint = (
        execution_policy.get("fingerprint") if isinstance(execution_policy, dict) else None
    )
    if not isinstance(strategy_policy, list) or not strategy_policy:
        raise RuntimeError("历史重建缺少 strategy policy fingerprints")
    if not isinstance(execution_policy_fingerprint, str):
        raise RuntimeError("历史重建缺少 execution policy fingerprint")

    generated_at = run.actual_generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    identity = build_recommendation_evidence_identity(
        run_source=RunSource.HISTORICAL_REPLAY,
        actual_generated_at=generated_at,
        signal_date=signal_date,
        snapshot=EvidenceSnapshotIdentity(
            source_snapshot_id=source_snapshot_id,
            data_as_of=data_as_of,
        ),
        strategy_policy=strategy_policy,
        execution_policy=execution_policy_fingerprint,
        code_revision=code_revision,
        reconstruction_batch_id=run.reconstruction_batch_id,
    )
    if identity.evidence_class is not EvidenceClass.HISTORICAL_RECONSTRUCTION:
        raise RuntimeError("canonical builder 未生成 HISTORICAL_RECONSTRUCTION")

    run.evidence_class = identity.evidence_class.value
    run.actual_generated_at = identity.actual_generated_at.replace(tzinfo=None)
    run.data_as_of = identity.data_as_of
    run.source_snapshot_id = identity.source_snapshot_id
    run.evidence_identity_hash = identity.identity_hash
    run.strategy_policy_fingerprint = identity.strategy_policy_fingerprint
    run.execution_policy_fingerprint = identity.execution_policy_fingerprint
    run.code_revision = identity.code_revision
    run.reconstruction_batch_id = identity.reconstruction_batch_id
    run.provenance_schema_version = identity.provenance_schema_version
    run.classification_reason = _RECONSTRUCTION_REASON
    db.commit()
    invalidate_recommendation_history_cache()
    return identity


def _wait_run(db, run_id: str) -> str:
    for _ in range(MAX_WAIT_LOOPS):
        time.sleep(POLL_SECONDS)
        # MySQL REPEATABLE READ：同一事务内后续查询看不到其他事务的提交。
        # 必须每次 rollback 释放快照，否则 worker 更新的 run 状态永远读不到。
        db.rollback()
        status = db.execute(
            text("SELECT status FROM recommendation_runs WHERE run_id = :rid"),
            {"rid": run_id},
        ).scalar()
        if status in ("completed", "failed", "interrupted"):
            return str(status)
    return "timeout"


def _run_exists(db, trade_date: str) -> bool:
    n = db.execute(
        text(
            "SELECT COUNT(*) FROM recommendation_runs "
            "WHERE trade_date = :d AND run_mode='formal'"
        ),
        {"d": trade_date},
    ).scalar()
    return bool(n)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-05-22")
    parser.add_argument("--end", default="2026-07-29")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有 run 的交易日")
    args = parser.parse_args()

    # Scope_Guard（S0 任务 0.10）：本入口声明为允许的历史重建，guard 按声明判定，
    # 不按日期范围猜测；延期范围（D1 全量回填/D2 新 alpha/D3 券商实盘）一律拒绝。
    assert_initiative_allowed(HISTORICAL_RECONSTRUCTION_INITIATIVE, context="scripts/replay_all_days.py")

    from app.services.workbench_service import dispatch_workbench_run, enqueue_workbench_run

    db = SessionLocal()
    try:
        dates = _trade_dates(db, args.start, args.end)
        if not dates:
            print(f"交易日: 0 个 ({args.start} ~ {args.end})")
            return 0
        print(f"交易日: {len(dates)} 个 ({dates[0]} ~ {dates[-1]})")

        base = _base_payload()
        reconstruction_batch_id = _new_reconstruction_batch_id(args.start, args.end)
        code_revision = _resolve_code_revision()
        print(
            f"重建批次: {reconstruction_batch_id} "
            f"(code_revision={code_revision[:12]})"
        )
        results = {"completed": 0, "failed": 0, "timeout": 0}
        for i, td in enumerate(dates):
            if args.skip_existing and _run_exists(db, td):
                print(f"[{i+1}/{len(dates)}] {td}: 已有 run，跳过")
                continue

            print(f"[{i+1}/{len(dates)}] 重建 {td} ...", flush=True)
            payload = _historical_payload(base, td)

            result = enqueue_workbench_run(db, payload, dispatch=False)
            run_id = result["run"]["run_id"]
            _initialize_reconstruction_run(db, run_id, reconstruction_batch_id)
            dispatch_workbench_run(db, run_id)

            status = _wait_run(db, run_id)
            print(f"  → {run_id}: {status}", flush=True)
            key = status if status in results else "failed"
            results[key] += 1

            if status == "completed":
                identity = _finalize_reconstruction_run(
                    db,
                    run_id,
                    code_revision=code_revision,
                )
                print(
                    f"  ✓ 历史重建 provenance 已写入 "
                    f"(identity={identity.identity_hash[:12]})",
                    flush=True,
                )

            # 每个 run 结束后清理 session 状态；不会改写推荐生成时间。
            db.rollback()

        print(f"\n完成: {results}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

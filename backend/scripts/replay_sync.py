#!/usr/bin/env python3
"""同步版历史重建：不依赖 Prefect，直接在当前进程运行推荐管线。

与 replay_all_days.py 的区别：
- 不用 Prefect 派发，直接调用 run_workbench(existing_run_id=...) 同步执行
- 代码版本自然一致（同一进程），避免 worker revision 不匹配
- 适用于本地批量回测场景

用法：python scripts/replay_sync.py --start 2026-06-17 --end 2026-09-17
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from sqlalchemy import select, text  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.registry.data_source import DATA_SOURCE_LOCAL_POOL  # noqa: E402

_GIT_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_RECONSTRUCTION_REASON = "trusted_historical_replay_entrypoint"


def _resolve_code_revision() -> str:
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
        raise RuntimeError("无法解析 git revision") from exc
    revision = process.stdout.strip()
    if not _GIT_REVISION_RE.fullmatch(revision):
        raise RuntimeError("git revision 格式无效")
    return revision.lower()


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
    return {
        "run_name": "REPLAY-历史推荐重建",
        "preset_id": "preset_formal_opening",
        "run_mode": "formal",
        "matrix_run": False,
        "trade_date_override": None,
        "data_source": DATA_SOURCE_LOCAL_POOL,
        "skip_aux_refresh": True,
        "market_scope": "all",
        "market_scope_value": None,
        "risk_template": "balanced",
        "score_line": 72.0,
        "output_count": 6,
        "hide_recent_repeats": False,
        "dedup_same_day": False,
        "disable_agent_review": True,
    }


def _historical_payload(base: dict, trade_date: str) -> dict:
    payload = dict(base)
    payload.pop("evaluation_scope", None)
    payload.update({
        "run_name": "REPLAY-历史推荐重建",
        "run_mode": "formal",
        "matrix_run": False,
        "trade_date_override": trade_date,
        "data_source": DATA_SOURCE_LOCAL_POOL,
        "skip_aux_refresh": True,
        "dedup_same_day": False,
        "disable_agent_review": True,
    })
    return payload


def _reserve_reconstruction_run(db, run, identity, trade_date: str) -> None:
    from app.services.evidence import EvidenceClass, PROVENANCE_SCHEMA_VERSION
    from app.services.evidence.reconstruction_batch import (
        ReconstructionBatchError,
        canonical_request_template_fingerprint,
        canonical_policy_fingerprint,
    )

    if str(run.status) != "queued":
        raise ReconstructionBatchError("reconstruction reservation requires a queued run")
    if str(run.run_mode) != "formal" or str(run.evaluation_scope) != "historical_replay":
        raise ReconstructionBatchError("reconstruction reservation requires formal historical replay")
    if str(run.trade_date or "") != trade_date:
        raise ReconstructionBatchError("reconstruction reservation trade date drifted")
    if canonical_request_template_fingerprint(str(run.request_json or "")) != identity.request_template_fingerprint:
        raise ReconstructionBatchError("reconstruction reservation request template drifted")

    try:
        summary = json.loads(run.result_summary_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReconstructionBatchError("reconstruction reservation execution audit is invalid") from exc
    execution = summary.get("execution") if isinstance(summary, dict) else None
    if not isinstance(execution, dict):
        raise ReconstructionBatchError("reconstruction reservation lacks execution audit")

    strategy_fingerprint = canonical_policy_fingerprint(
        execution.get("strategy_fingerprints"), field_name="strategy_policy"
    )
    execution_fingerprint = canonical_policy_fingerprint(
        execution.get("execution_policy"), field_name="execution_policy"
    )
    if strategy_fingerprint != identity.strategy_policy_fingerprint or execution_fingerprint != identity.execution_policy_fingerprint:
        raise ReconstructionBatchError("reconstruction reservation execution policy drifted")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run.evidence_class = EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    run.actual_generated_at = now
    run.reconstruction_batch_id = identity.batch_id
    run.provenance_schema_version = PROVENANCE_SCHEMA_VERSION
    run.classification_reason = _RECONSTRUCTION_REASON


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-06-17")
    parser.add_argument("--end", default="2026-09-17")
    args = parser.parse_args()

    os.environ.setdefault("ASHARE_APPLICATION_REVISION", _resolve_code_revision())

    db = SessionLocal()
    try:
        from app.services.evidence.reconstruction_batch import (
            ReconstructionBatchError,
            build_reconstruction_batch_identity,
            create_or_resume_reconstruction_batch,
            mark_reconstruction_batch_failed,
            publish_reconstruction_batch,
        )
        from app.services.workbench_service import (
            _prepare_workbench_run,
            enqueue_workbench_run,
            run_workbench,
        )
        from app.models.tables import RecommendationRun

        dates = _trade_dates(db, args.start, args.end)
        if not dates:
            print(f"交易日: 0 个 ({args.start} ~ {args.end})")
            return 0
        print(f"交易日: {len(dates)} 个 ({dates[0]} ~ {dates[-1]})")

        base = _base_payload()

        # 构建批次 identity
        payload0 = _historical_payload(base, dates[0])
        _params, request, _selected_skills, _execution_version, execution_audit = _prepare_workbench_run(db, payload0)
        strategy_policy = execution_audit.get("strategy_fingerprints")
        execution_policy = execution_audit.get("execution_policy")
        if not isinstance(strategy_policy, list) or not strategy_policy:
            raise RuntimeError("无法冻结 strategy policy")
        if not isinstance(execution_policy, dict):
            raise RuntimeError("无法冻结 execution policy")

        identity = build_reconstruction_batch_identity(
            expected_trade_dates=dates,
            request_template=request.to_canonical_json(),
            strategy_policy=strategy_policy,
            execution_policy=execution_policy,
            code_revision=_resolve_code_revision(),
        )

        create_or_resume_reconstruction_batch(db, identity)
        db.commit()
        print(f"重建批次: {identity.batch_id} (code_revision={identity.code_revision[:12]})")

        results = {"completed": 0, "failed": 0, "skipped": 0}
        total_start = time.time()

        for i, td in enumerate(dates):
            # 检查是否已有 sealed run
            existing = db.scalar(
                select(RecommendationRun).where(
                    RecommendationRun.reconstruction_batch_id == identity.batch_id,
                    RecommendationRun.trade_date == td,
                )
            )
            if existing is not None and str(existing.status) == "completed":
                from app.services.evidence import EvidenceClass, has_canonical_persisted_identity
                if str(existing.evidence_class) == EvidenceClass.HISTORICAL_RECONSTRUCTION.value and has_canonical_persisted_identity(
                    existing, expected_class=EvidenceClass.HISTORICAL_RECONSTRUCTION
                ):
                    print(f"[{i+1}/{len(dates)}] {td}: 已 sealed，跳过")
                    results["skipped"] += 1
                    continue

            print(f"[{i+1}/{len(dates)}] 重建 {td} ...", end=" ", flush=True)
            day_start = time.time()

            try:
                create_or_resume_reconstruction_batch(db, identity)
                payload = _historical_payload(base, td)
                enqueue_result = enqueue_workbench_run(
                    db,
                    payload,
                    dispatch=False,
                    before_commit=lambda run: _reserve_reconstruction_run(db, run, identity, td),
                )
                run_id = str(enqueue_result["run"]["run_id"])
                db.commit()

                # 同步运行
                await run_workbench(db, payload, existing_run_id=run_id)
                db.commit()

                elapsed = time.time() - day_start

                # 验证
                run = db.get(RecommendationRun, run_id)
                rec_count = db.execute(
                    text("SELECT COUNT(*) FROM recommendations WHERE run_id = :rid"),
                    {"rid": run_id},
                ).scalar()

                if run and str(run.status) == "completed":
                    print(f"completed | {elapsed:.1f}s | 推荐{rec_count}只")
                    results["completed"] += 1
                else:
                    print(f"failed | {elapsed:.1f}s | status={run.status if run else 'N/A'}")
                    results["failed"] += 1
                    mark_reconstruction_batch_failed(db, identity.batch_id, f"{td} run not completed")
                    db.commit()
                    return 1

            except Exception as exc:
                db.rollback()
                elapsed = time.time() - day_start
                print(f"error | {elapsed:.1f}s | {exc}")
                results["failed"] += 1
                mark_reconstruction_batch_failed(db, identity.batch_id, str(exc))
                db.commit()
                return 1

        # 发布批次
        try:
            cohort = publish_reconstruction_batch(db, identity.batch_id)
            db.commit()
        except ReconstructionBatchError as exc:
            db.rollback()
            mark_reconstruction_batch_failed(db, identity.batch_id, str(exc))
            db.commit()
            print(f"\n批次发布失败: {exc}")
            return 1

        total_elapsed = time.time() - total_start
        print(f"\n{'='*60}")
        print(f"历史重建完成！")
        print(f"  总天数: {len(dates)}")
        print(f"  成功: {results['completed']}")
        print(f"  失败: {results['failed']}")
        print(f"  跳过: {results['skipped']}")
        print(f"  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
        print(f"  cohort: {cohort.cohort_identity_hash[:12]}")
        print(f"  runs: {len(cohort.run_ids)}")
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

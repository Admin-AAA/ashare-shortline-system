#!/usr/bin/env python3
"""全量逐日历史重建驱动器：对窗口内每个交易日跑一次正式推荐 run。

流程（每交易日）：
  1. 基于固定日期 manifest、请求模板、策略/执行 policy 与 worker revision 创建或恢复 batch
  2. enqueue historical_replay run，并在同一事务预留 batch/date 与显式 reconstruction 身份
  3. dispatch（同步 Prefect 派发）并等待 worker 在 completion transaction 内写入 canonical provenance
  4. 所有日期 sealed 后发布完整 cohort；失败或超时则将 batch 标为 failed

推荐、候选审计与 outcome 仍由既有正式管线负责。本脚本不会改写
``evaluation_scope`` 或 ``recommended_at`` 来伪装 LIVE evidence。

用法：python scripts/replay_all_days.py [--start 2026-05-22] [--end 2026-07-29]
      [--dates 2026-05-14,2026-05-15] [--rule-only-fallback]
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

# 关键：不设 PREFECT_API_URL 时 run_deployment 会启动临时 server，
# 任务派发到临时 server 而非主 worker。显式指向主 server。
os.environ.setdefault("PREFECT_API_URL", "http://127.0.0.1:4200/api")

BASE_DIR = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select, text  # noqa: E402
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


def _explicit_trade_dates(db, raw: str) -> list[str]:
    """Validate and return an explicit, deduplicated list of indexed trade dates."""
    requested = sorted({part.strip() for part in raw.split(",") if part.strip()})
    if not requested:
        raise ValueError("--dates 至少需要一个 YYYY-MM-DD 日期")

    invalid = []
    for value in requested:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            invalid.append(value)
    if invalid:
        raise ValueError(f"--dates 包含无效日期: {', '.join(invalid)}")

    available = set(_trade_dates(db, requested[0], requested[-1]))
    missing = [value for value in requested if value not in available]
    if missing:
        raise ValueError(f"--dates 包含数据库未索引的交易日: {', '.join(missing)}")
    return requested


def _base_payload(db=None) -> dict:
    """从最近一个 canonical LIVE run 复制策略参数，日期与 replay 边界另行覆盖。"""
    from app.services.evidence.validation import latest_canonical_live_forward_run

    owns_session = db is None
    if db is None:
        db = SessionLocal()
    try:
        run = latest_canonical_live_forward_run(db)
        row = run.params_json if run is not None else None
        if not row:
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
            }
        params = json.loads(row)
        params["run_name"] = "REPLAY-历史推荐重建"
        params["preset_id"] = "preset_formal_opening"
        params["trade_date_override"] = None  # 每日单独设
        # 实盘 10-14 起按单策略逐个 run（每策略 3 只）；若最近 run 是单策略，回放改用
        # preset 的全部现役策略（skills 来自 preset_skills），output_count=3×策略数。
        if len(params.get("skills") or []) <= 1:
            params.pop("skills", None)
            params.pop("single_skill_id", None)
            from app.models.tables import PresetSkill
            n = len(db.scalars(select(PresetSkill.skill_id).where(PresetSkill.preset_id == "preset_formal_opening")).all())
            params["output_count"] = 3 * max(n, 1)
        return params
    finally:
        if owns_session:
            db.close()


def _historical_payload(
    base: dict,
    trade_date: str,
    *,
    disable_agent_review: bool = False,
    attempt_tag: str | None = None,
) -> dict:
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
            "run_name": (
                f"REPLAY-历史推荐重建/{attempt_tag}"
                if attempt_tag
                else "REPLAY-历史推荐重建"
            ),
            "run_mode": "formal",
            "matrix_run": False,
            "trade_date_override": trade_date,
            "data_source": DATA_SOURCE_LOCAL_POOL,
            "skip_aux_refresh": True,
            # 回放 run 之间互相独立：同日已有落库结果（如同 batch 前序 run 或历史
            # 重建）不应触发跨 run 去重自我剔除；实盘去重基数另有 live_forward 过滤。
            "dedup_same_day": False,
            # Provider 欠费/熔断时允许历史重建走显式 rule_only 口径；这不是
            # LIVE 证据，也不会伪装成已完成 LLM 深审。
            "disable_agent_review": bool(disable_agent_review),
        }
    )
    return payload


def _reconstruction_identity(
    db,
    dates: list[str],
    base: dict,
    *,
    disable_agent_review: bool = False,
    attempt_tag: str | None = None,
):
    """Freeze the manifest before any batch/date reservation can be committed."""
    from app.services.evidence.reconstruction_batch import (
        build_reconstruction_batch_identity,
    )
    from app.services.workbench_service import _prepare_workbench_run

    if not dates:
        raise ValueError("历史重建 batch 至少需要一个交易日")
    payload = _historical_payload(
        base,
        dates[0],
        disable_agent_review=disable_agent_review,
        attempt_tag=attempt_tag,
    )
    _params, request, _selected_skills, _execution_version, execution_audit = (
        _prepare_workbench_run(db, payload)
    )
    strategy_policy = execution_audit.get("strategy_fingerprints")
    execution_policy = execution_audit.get("execution_policy")
    if not isinstance(strategy_policy, list) or not strategy_policy:
        raise RuntimeError("历史重建无法冻结 strategy policy")
    if not isinstance(execution_policy, dict):
        raise RuntimeError("历史重建无法冻结 execution policy")
    return build_reconstruction_batch_identity(
        expected_trade_dates=dates,
        request_template=request.to_canonical_json(),
        strategy_policy=strategy_policy,
        execution_policy=execution_policy,
        code_revision=_resolve_code_revision(),
    )


def _reserve_reconstruction_run(db, run, identity, trade_date: str) -> None:
    """Bind the queued row to its manifest before it becomes dispatchable."""
    from app.services.evidence import EvidenceClass, PROVENANCE_SCHEMA_VERSION
    from app.services.evidence.reconstruction_batch import (
        ReconstructionBatchError,
        canonical_request_template_fingerprint,
    )

    if str(run.status) != "queued":
        raise ReconstructionBatchError("reconstruction reservation requires a queued run")
    if str(run.run_mode) != "formal" or str(run.evaluation_scope) != "historical_replay":
        raise ReconstructionBatchError("reconstruction reservation requires formal historical replay")
    if run.evidence_class not in (None, EvidenceClass.HISTORICAL_RECONSTRUCTION.value):
        raise ReconstructionBatchError("reconstruction reservation found conflicting evidence class")
    if run.reconstruction_batch_id not in (None, identity.batch_id):
        raise ReconstructionBatchError("reconstruction reservation found conflicting batch")
    if str(run.trade_date or "") != trade_date:
        raise ReconstructionBatchError("reconstruction reservation trade date drifted")
    if trade_date not in identity.expected_trade_dates:
        raise ReconstructionBatchError("reconstruction reservation date is outside its batch manifest")
    if canonical_request_template_fingerprint(str(run.request_json or "")) != (
        identity.request_template_fingerprint
    ):
        raise ReconstructionBatchError("reconstruction reservation request template drifted")
    try:
        summary = json.loads(run.result_summary_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReconstructionBatchError("reconstruction reservation execution audit is invalid") from exc
    execution = summary.get("execution") if isinstance(summary, dict) else None
    if not isinstance(execution, dict):
        raise ReconstructionBatchError("reconstruction reservation lacks execution audit")
    from app.services.evidence import canonical_policy_fingerprint

    try:
        strategy_fingerprint = canonical_policy_fingerprint(
            execution.get("strategy_fingerprints"),
            field_name="strategy_policy",
        )
        execution_fingerprint = canonical_policy_fingerprint(
            execution.get("execution_policy"),
            field_name="execution_policy",
        )
    except ValueError as exc:
        raise ReconstructionBatchError(
            "reconstruction reservation execution policy drifted"
        ) from exc
    if (
        strategy_fingerprint != identity.strategy_policy_fingerprint
        or execution_fingerprint != identity.execution_policy_fingerprint
    ):
        raise ReconstructionBatchError("reconstruction reservation execution policy drifted")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    run.evidence_class = EvidenceClass.HISTORICAL_RECONSTRUCTION.value
    run.actual_generated_at = now
    run.reconstruction_batch_id = identity.batch_id
    run.provenance_schema_version = PROVENANCE_SCHEMA_VERSION
    run.classification_reason = _RECONSTRUCTION_REASON


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


def _run_exists(db, identity, trade_date: str) -> bool:
    """Resume only the exact sealed run reserved by this deterministic manifest."""
    from app.models.tables import RecommendationRun
    from app.services.evidence import (
        EvidenceClass,
        has_canonical_persisted_identity,
    )

    run = db.scalar(
        select(RecommendationRun).where(
            RecommendationRun.reconstruction_batch_id == identity.batch_id,
            RecommendationRun.trade_date == trade_date,
        )
    )
    return bool(
        run is not None
        and str(run.status) == "completed"
        and str(run.evidence_class) == EvidenceClass.HISTORICAL_RECONSTRUCTION.value
        and has_canonical_persisted_identity(
            run,
            expected_class=EvidenceClass.HISTORICAL_RECONSTRUCTION,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-05-22")
    parser.add_argument("--end", default="2026-07-29")
    parser.add_argument(
        "--dates",
        help="逗号分隔的精确交易日列表；指定后覆盖 --start/--end",
    )
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有 run 的交易日")
    parser.add_argument(
        "--rule-only-fallback",
        action="store_true",
        help="历史重建禁用 LLM 深审，明确记录为 rule_only，适用于模型服务不可用时补样本",
    )
    parser.add_argument(
        "--attempt-tag",
        help="失败批次的 append-only 重试标识；会写入 run_name 并生成新的 manifest",
    )
    args = parser.parse_args()

    # Scope_Guard（S0 任务 0.10）：本入口声明为允许的历史重建，guard 按声明判定，
    # 不按日期范围猜测；延期范围（D1 全量回填/D2 新 alpha/D3 券商实盘）一律拒绝。
    assert_initiative_allowed(HISTORICAL_RECONSTRUCTION_INITIATIVE, context="scripts/replay_all_days.py")
    # 主进程在预留 run 时也必须使用与 Prefect worker 相同的部署 revision；
    # 否则 execution_version 会在入队和执行时产生不同身份。
    os.environ.setdefault("ASHARE_APPLICATION_REVISION", _resolve_code_revision())

    db = SessionLocal()
    try:
        try:
            dates = (
                _explicit_trade_dates(db, args.dates)
                if args.dates
                else _trade_dates(db, args.start, args.end)
            )
        except ValueError as exc:
            print(f"日期参数无效: {exc}")
            return 2
        if not dates:
            window = f"{args.start} ~ {args.end}"
            print(f"交易日: 0 个 ({window})")
            return 0
        print(f"交易日: {len(dates)} 个 ({dates[0]} ~ {dates[-1]})")

        base = _base_payload(db)
        identity = _reconstruction_identity(
            db,
            dates,
            base,
            disable_agent_review=args.rule_only_fallback,
            attempt_tag=args.attempt_tag,
        )
        from app.models.tables import RecommendationRun
        from app.services.evidence.reconstruction_batch import (
            ReconstructionBatchError,
            create_or_resume_reconstruction_batch,
            mark_reconstruction_batch_failed,
            publish_reconstruction_batch,
        )
        from app.services.workbench_service import dispatch_workbench_run, enqueue_workbench_run

        create_or_resume_reconstruction_batch(db, identity)
        db.commit()
        print(
            f"重建批次: {identity.batch_id} "
            f"(code_revision={identity.code_revision[:12]})"
        )
        results = {"completed": 0, "failed": 0, "timeout": 0, "skipped": 0}
        for i, td in enumerate(dates):
            if args.skip_existing and _run_exists(db, identity, td):
                print(f"[{i+1}/{len(dates)}] {td}: 当前 manifest 已 sealed，跳过")
                results["skipped"] += 1
                continue

            existing_status = db.scalar(
                select(RecommendationRun.status).where(
                    RecommendationRun.reconstruction_batch_id == identity.batch_id,
                    RecommendationRun.trade_date == td,
                )
            )
            if existing_status is not None:
                message = f"{td} 已预留给当前 batch，状态={existing_status}；拒绝替换"
                mark_reconstruction_batch_failed(db, identity.batch_id, message)
                db.commit()
                print(f"[{i+1}/{len(dates)}] {message}", flush=True)
                return 1

            print(f"[{i+1}/{len(dates)}] 重建 {td} ...", flush=True)
            payload = _historical_payload(
                base,
                td,
                disable_agent_review=args.rule_only_fallback,
                attempt_tag=args.attempt_tag,
            )
            try:
                create_or_resume_reconstruction_batch(db, identity)
                result = enqueue_workbench_run(
                    db,
                    payload,
                    dispatch=False,
                    before_commit=lambda run: _reserve_reconstruction_run(
                        db,
                        run,
                        identity,
                        td,
                    ),
                )
                run_id = str(result["run"]["run_id"])
                dispatch_workbench_run(db, run_id)
                status = _wait_run(db, run_id)
            except Exception as exc:
                db.rollback()
                mark_reconstruction_batch_failed(db, identity.batch_id, str(exc))
                db.commit()
                print(f"  → {td}: failed ({exc})", flush=True)
                return 1

            print(f"  → {run_id}: {status}", flush=True)
            if status != "completed":
                results[status if status in results else "failed"] += 1
                db.rollback()
                mark_reconstruction_batch_failed(
                    db,
                    identity.batch_id,
                    f"{td} reconstruction {status}",
                )
                db.commit()
                return 1

            if not _run_exists(db, identity, td):
                db.rollback()
                mark_reconstruction_batch_failed(
                    db,
                    identity.batch_id,
                    f"{td} completed without a canonical worker seal",
                )
                db.commit()
                print(f"  → {run_id}: completed but canonical seal is missing", flush=True)
                return 1
            results["completed"] += 1
            db.rollback()

        try:
            cohort = publish_reconstruction_batch(db, identity.batch_id)
            db.commit()
        except ReconstructionBatchError as exc:
            db.rollback()
            mark_reconstruction_batch_failed(db, identity.batch_id, str(exc))
            db.commit()
            print(f"\n批次发布失败: {exc}")
            return 1
        print(
            f"\n完成: {results}; cohort={cohort.cohort_identity_hash[:12]} "
            f"runs={len(cohort.run_ids)}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import Preset, Recommendation, RecommendationRun, ReviewNote, WatchItem
from app.services.data_gap_remediation_service import (
    DEFAULT_DAILY_KBAR_CODE_LIMIT,
    remediate_recommendation_run_auxiliary_data,
    sync_local_pool_kline_gaps,
)
from app.services.data_health_service import diagnose_all
from app.services.data_sources import get_local_pool_status, list_data_sources
from app.services.data_source_health import active_alerts, readiness_check
from app.services.market_pool import refresh_quote_pool
from app.services.recommendations.projection import build_recommendation_explanation
from app.services.scheduler_service import _run_preset_matrix
from app.services.workbench_service import (
    get_recommendation_detail,
    get_run_detail,
    get_run_filter_audit,
    list_presets,
    list_run_recommendations,
    list_runs,
    run_workbench,
)

JsonDict = dict[str, Any]


def _emit(data: Any, *, pretty: bool = False) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None, default=str))


def _compact_run(run: JsonDict) -> JsonDict:
    return {
        "run_id": run.get("run_id"),
        "run_name": run.get("run_name"),
        "preset_id": run.get("preset_id"),
        "preset_name": run.get("preset_name"),
        "run_mode": run.get("run_mode"),
        "trade_date": run.get("trade_date"),
        "status": run.get("status"),
        "recommended_count": run.get("recommended_count"),
        "progress_phase": run.get("progress_phase") or run.get("checkpoint_phase"),
        "progress_pct": run.get("progress_pct"),
        "progress_message": run.get("progress_message"),
        "error_message": run.get("error_message"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
    }


def _compact_recommendation(rec: JsonDict) -> JsonDict:
    feature = rec.get("feature_snapshot") or {}
    return {
        "recommendation_id": rec.get("recommendation_id"),
        "run_id": rec.get("run_id"),
        "trade_date": rec.get("trade_date"),
        "code": rec.get("code"),
        "name": rec.get("name"),
        "industry": rec.get("industry"),
        "final_score": rec.get("final_score"),
        "consensus_score": rec.get("consensus_score"),
        "setup_type": rec.get("setup_type"),
        "status": rec.get("status"),
        "watch_status": rec.get("watch_status"),
        "ideal_buy": rec.get("ideal_buy") or feature.get("ideal_buy"),
        "secondary_buy": rec.get("secondary_buy") or feature.get("secondary_buy"),
        "stop_loss": rec.get("stop_loss") or feature.get("stop_loss"),
        "take_profit": rec.get("take_profit") or feature.get("take_profit"),
        "rationale": rec.get("rationale"),
        "risk_tags": rec.get("risk_tags") or [],
        "info_tags": rec.get("info_tags") or [],
    }


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001 - tolerate legacy/broken JSON in CLI projections
        return default


def _number(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _escape_md(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def _job_root() -> Path:
    root = Path.home() / ".cache" / "ashare-cli" / "jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_paths(job_id: str) -> dict[str, Path]:
    root = _job_root()
    return {
        "meta": root / f"{job_id}.json",
        "stdout": root / f"{job_id}.stdout.json",
        "stderr": root / f"{job_id}.stderr.log",
        "rc": root / f"{job_id}.rc",
    }


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or not path.read_text().strip():
            return default
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - CLI status must tolerate partial writes
        return default


def _pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _load_job(job_id: str) -> JsonDict:
    paths = _job_paths(job_id)
    meta = _read_json_file(paths["meta"], None)
    if not isinstance(meta, dict):
        raise ValueError(f"job not found: {job_id}")
    rc_text = paths["rc"].read_text().strip() if paths["rc"].exists() else ""
    rc = int(rc_text) if rc_text.lstrip("-").isdigit() else None
    running = rc is None and _pid_running(_number(meta.get("pid"), 0))
    stdout = _read_json_file(paths["stdout"], None)
    stderr_tail = ""
    if paths["stderr"].exists():
        stderr_tail = "\n".join(paths["stderr"].read_text(errors="replace").splitlines()[-30:])
    status = "running" if running else ("completed" if rc == 0 else "failed" if rc is not None else "unknown")
    return {
        **meta,
        "status": status,
        "running": running,
        "returncode": rc,
        "stdout_path": str(paths["stdout"]),
        "stderr_path": str(paths["stderr"]),
        "stderr_tail": stderr_tail,
        "result": stdout,
    }


def _with_db(handler: Callable[[Session, argparse.Namespace], Any], args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        result = handler(db, args)
        if result is not None:
            _emit(result, pretty=args.pretty)
        return 0
    finally:
        db.close()


def _list_presets(db: Session, args: argparse.Namespace) -> JsonDict:
    presets = list_presets(db)
    if args.active_only:
        presets = [p for p in presets if p.get("status") == "active"]
    return {"presets": presets, "count": len(presets)}


def _registry_diagnostics(db: Session, args: argparse.Namespace) -> JsonDict:
    from app.engine.skill_loader import load_skills
    from app.services.registry.data_source import DEFAULT_PRESETS
    from app.services.registry.diagnostics import build_registry_diagnostics
    from app.core.config import get_settings

    skills = load_skills(get_settings().skills_dir)
    report = build_registry_diagnostics(
        skills,
        list_presets(db),
        canonical_presets=DEFAULT_PRESETS,
    )
    if args.preset_id:
        report.require_executable(args.preset_id)
    return report.to_dict()


def _list_runs(db: Session, args: argparse.Namespace) -> JsonDict:
    runs = list_runs(db, run_mode=args.run_mode, limit=args.limit)
    payload = [_compact_run(run) for run in runs] if args.compact else runs
    return {"runs": payload, "count": len(payload)}


def _show_run(db: Session, args: argparse.Namespace) -> JsonDict:
    run = get_run_detail(db, args.run_id)
    if run is None:
        raise ValueError(f"run not found: {args.run_id}")
    return _compact_run(run) if args.compact else run


def _run_recommendations(db: Session, args: argparse.Namespace) -> JsonDict:
    recs = list_run_recommendations(db, args.run_id)
    if args.limit:
        recs = recs[: args.limit]
    payload = [_compact_recommendation(rec) for rec in recs] if args.compact else recs
    return {"run_id": args.run_id, "recommendations": payload, "count": len(payload)}


def _run_funnel(db: Session, args: argparse.Namespace) -> JsonDict:
    audit = get_run_filter_audit(db, args.run_id)
    if audit is None:
        raise ValueError(f"run not found: {args.run_id}")
    if args.code:
        rows = audit.get("rows") or []
        audit["rows"] = [row for row in rows if str(row.get("code")) == args.code]
    return audit


def _execute_preset(db: Session, args: argparse.Namespace) -> JsonDict:
    payload: JsonDict = {
        "preset_id": args.preset_id,
        "wait_for_completion": bool(args.wait),
        "async": not bool(args.wait),
    }
    for key in ("run_name", "data_source", "trade_date"):
        value = getattr(args, key)
        if value:
            payload[key] = value
    if args.output_count is not None:
        payload["output_count"] = args.output_count
    if args.score_line is not None:
        payload["score_line"] = args.score_line
    if args.skip_aux_refresh:
        payload["skip_aux_refresh"] = True
    result = asyncio.run(run_workbench(db, payload)) if args.wait else None
    if not args.wait:
        from app.services.workbench_service import enqueue_workbench_run

        result = enqueue_workbench_run(db, payload)
    if result is None:
        raise RuntimeError("run execution returned no result")
    return result


def _wait_run(db: Session, args: argparse.Namespace) -> JsonDict:
    deadline = time.monotonic() + args.timeout
    terminal_status = {"completed", "failed", "interrupted", "cancelled"}
    last: JsonDict | None = None
    while time.monotonic() <= deadline:
        last = get_run_detail(db, args.run_id)
        if last is None:
            raise ValueError(f"run not found: {args.run_id}")
        if last.get("status") in terminal_status:
            return _compact_run(last) if args.compact else last
        time.sleep(args.interval)
        db.expire_all()
    return {"timeout": True, "run": _compact_run(last or {}) if args.compact else last}


def _recommendation_detail(db: Session, args: argparse.Namespace) -> JsonDict:
    rec = get_recommendation_detail(db, args.recommendation_id)
    if rec is None:
        raise ValueError(f"recommendation not found: {args.recommendation_id}")
    return _compact_recommendation(rec) if args.compact else rec


def _explain_recommendation(db: Session, args: argparse.Namespace) -> JsonDict:
    rec = db.get(Recommendation, args.recommendation_id)
    if rec is None:
        raise ValueError(f"recommendation not found: {args.recommendation_id}")
    watch = db.scalar(select(WatchItem).where(WatchItem.source_recommendation_id == args.recommendation_id))
    trace_rows = []
    audit = get_run_filter_audit(db, rec.run_id) or {}
    for row in audit.get("rows") or []:
        if str(row.get("code")) == rec.code:
            trace_rows.append(row)
    explanation = build_recommendation_explanation(rec, trace_rows=trace_rows, watch=watch).model_dump()
    detail = get_recommendation_detail(db, args.recommendation_id) or {}
    return {
        "recommendation": _compact_recommendation(detail),
        "explanation": explanation,
        "agent_logs": detail.get("agent_logs", []),
        "outcome": detail.get("outcome"),
        "review_note": detail.get("review_note"),
        "history": detail.get("history", [])[: args.history_limit],
    }


def _watch_recommendation(db: Session, args: argparse.Namespace) -> JsonDict:
    from app.services.workbench_service import set_watch_status

    result = set_watch_status(db, args.recommendation_id, args.status, args.note)
    if result is None:
        raise ValueError(f"recommendation not found: {args.recommendation_id}")
    return result


def _review_recommendation(db: Session, args: argparse.Namespace) -> JsonDict:
    from app.services.workbench_service import save_review_note

    payload = {
        "user_label": args.user_label,
        "failure_reason": args.failure_reason,
        "review_text": args.text,
    }
    result = save_review_note(db, args.recommendation_id, payload)
    if result is None:
        raise ValueError(f"recommendation not found: {args.recommendation_id}")
    return result


def _data_sources(db: Session, args: argparse.Namespace) -> JsonDict:
    sources = list_data_sources(db)
    return {"sources": sources, "count": len(sources)}


def _pool_status(db: Session, args: argparse.Namespace) -> JsonDict:
    return get_local_pool_status(
        db,
        unified_limit=args.unified_limit,
        unified_offset=args.unified_offset,
        bars_limit=args.bars_limit,
        bars_offset=args.bars_offset,
        include_details=not args.summary_only,
    )


def _refresh_pool(db: Session, args: argparse.Namespace) -> JsonDict:
    quotes, meta = refresh_quote_pool(db)
    return {"refreshed": len(quotes), "meta": meta}


def _sync_pool_gaps(db: Session, args: argparse.Namespace) -> JsonDict:
    return sync_local_pool_kline_gaps(
        db,
        code_limit=args.code_limit,
        days=args.days,
        min_bars=args.min_bars,
        dry_run=args.dry_run,
    )


def _sync_run_aux(db: Session, args: argparse.Namespace) -> JsonDict:
    target_tables = [item.strip() for item in (args.target_tables or "").split(",") if item.strip()] or None
    return remediate_recommendation_run_auxiliary_data(
        db,
        args.run_id,
        target_tables=target_tables,
        code_limit=args.code_limit,
        dry_run=args.dry_run,
    )


def _data_health(db: Session, args: argparse.Namespace) -> JsonDict:
    return diagnose_all(db)


def _alerts(db: Session, args: argparse.Namespace) -> JsonDict:
    alerts = active_alerts(db)
    return {"alerts": alerts, "count": len(alerts)}


def _readiness(db: Session, args: argparse.Namespace) -> JsonDict:
    return readiness_check(db)


def _matrix_payload_from_args(args: argparse.Namespace) -> JsonDict:
    payload: JsonDict = {
        "matrix_batch_name": args.matrix_batch_name,
        "run_name_prefix": args.run_name_prefix or args.matrix_batch_name,
        "data_source": args.data_source,
        "stop_on_error": args.stop_on_error,
    }
    for key in ("output_count", "score_line", "agent_review_limit", "agent_review_concurrency"):
        value = getattr(args, key, None)
        if value is not None:
            payload[key] = value
    if args.skip_aux_refresh:
        payload["skip_aux_refresh"] = True
    if args.disable_agent_review:
        payload["disable_agent_review"] = True
    return payload


def _run_matrix(db: Session, args: argparse.Namespace) -> JsonDict:
    if getattr(args, "async_job", False):
        return _start_matrix_job(args)
    payload = _matrix_payload_from_args(args)
    if not payload.get("matrix_batch_name"):
        stamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d_%H%M%S")
        payload["matrix_batch_name"] = f"CLI预设矩阵 {stamp}"
        payload["run_name_prefix"] = payload["run_name_prefix"] or payload["matrix_batch_name"]
    result = asyncio.run(_run_preset_matrix(db, payload))
    if args.table:
        result["markdown_table"] = _matrix_markdown_table(db, str(result.get("matrix_batch_id") or ""), top=args.top)
    return result


def _latest_matrix_batch_id(db: Session) -> str | None:
    rows = db.scalars(
        select(RecommendationRun)
        .where(RecommendationRun.result_summary_json.contains("matrix_batch_id"))
        .order_by(RecommendationRun.finished_at.desc(), RecommendationRun.started_at.desc())
        .limit(100)
    ).all()
    best: tuple[str, datetime] | None = None
    for run in rows:
        summary = _loads(run.result_summary_json, {})
        batch_id = str(summary.get("matrix_batch_id") or "").strip()
        if not batch_id:
            continue
        ts = run.finished_at or run.started_at or datetime.min
        if best is None or ts > best[1]:
            best = (batch_id, ts)
    return best[0] if best else None


def _matrix_rows(db: Session, batch_id: str, *, top: int = 5) -> list[JsonDict]:
    presets = {p.preset_id: p for p in db.scalars(select(Preset)).all()}
    runs = db.scalars(
        select(RecommendationRun)
        .where(RecommendationRun.result_summary_json.contains(batch_id))
        .order_by(RecommendationRun.started_at.asc())
    ).all()
    rows: list[JsonDict] = []
    for run in runs:
        summary = _loads(run.result_summary_json, {})
        if str(summary.get("matrix_batch_id") or "") != batch_id:
            continue
        preset = presets.get(run.preset_id or "")
        recs = db.scalars(
            select(Recommendation).where(Recommendation.run_id == run.run_id).order_by(Recommendation.final_score.desc())
        ).all()
        pipeline = summary.get("pipeline") if isinstance(summary, dict) else {}
        if not isinstance(pipeline, dict):
            pipeline = {}
        top_recs = [
            {
                "recommendation_id": rec.recommendation_id,
                "code": rec.code,
                "name": rec.name,
                "final_score": rec.final_score,
                "setup_type": rec.setup_type,
            }
            for rec in recs[:top]
        ]
        rows.append(
            {
                "preset_id": run.preset_id,
                "preset_name": preset.name if preset else run.preset_id,
                "run_id": run.run_id,
                "trade_date": run.trade_date,
                "status": run.status,
                "scan_count": _number(pipeline.get("actual_scan_count"), run.universe_count or 0),
                "rule_count": _number(pipeline.get("skill_rule_candidate_count")),
                "gate_count": _number(pipeline.get("skill_gate_candidate_count")),
                "recommended_count": len(recs) if recs else run.recommended_count,
                "average_score": round(sum(float(rec.final_score or 0) for rec in recs) / len(recs), 2) if recs else None,
                "top_recommendations": top_recs,
                "error_message": run.error_message,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }
        )
    return rows


def _matrix_overlap(rows: list[JsonDict]) -> list[JsonDict]:
    by_code: dict[str, JsonDict] = {}
    for row in rows:
        for rec in row.get("top_recommendations") or []:
            code = str(rec.get("code") or "")
            if not code:
                continue
            current = by_code.setdefault(
                code,
                {"code": code, "name": rec.get("name"), "count": 0, "presets": [], "max_score": 0.0},
            )
            current["count"] += 1
            current["max_score"] = max(float(current.get("max_score") or 0), float(rec.get("final_score") or 0))
            preset_name = row.get("preset_name")
            if preset_name and preset_name not in current["presets"]:
                current["presets"].append(preset_name)
    return sorted(
        [item for item in by_code.values() if int(item.get("count") or 0) > 1],
        key=lambda item: (-int(item.get("count") or 0), -float(item.get("max_score") or 0), str(item.get("code") or "")),
    )


def _matrix_markdown_table(db: Session, batch_id: str, *, top: int = 5) -> str:
    rows = _matrix_rows(db, batch_id, top=top)
    lines = [
        "| 预设 | 状态 | 扫描 | 规则门 | 推荐数 | 均分 | Top推荐 | run_id |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        top_text = "；".join(
            f"{rec['code']} {rec['name']} {float(rec.get('final_score') or 0):.1f}"
            for rec in row.get("top_recommendations") or []
        ) or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_md(row.get("preset_name")),
                    _escape_md(row.get("status")),
                    str(row.get("scan_count") or 0),
                    f"{row.get('rule_count') or 0}/{row.get('gate_count') or 0}",
                    str(row.get("recommended_count") or 0),
                    _escape_md(row.get("average_score")),
                    _escape_md(top_text),
                    _escape_md(row.get("run_id")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _matrix_result(db: Session, args: argparse.Namespace) -> JsonDict:
    batch_id = args.batch_id or _latest_matrix_batch_id(db)
    if not batch_id:
        raise ValueError("matrix batch not found")
    rows = _matrix_rows(db, batch_id, top=args.top)
    overlap = _matrix_overlap(rows)
    result: JsonDict = {
        "matrix_batch_id": batch_id,
        "run_count": len(rows),
        "completed_count": sum(1 for row in rows if row.get("status") == "completed"),
        "failed_count": sum(1 for row in rows if row.get("status") == "failed"),
        "recommended_count": sum(_number(row.get("recommended_count")) for row in rows),
        "overlap": overlap,
        "rows": rows,
    }
    if args.table:
        result["markdown_table"] = _matrix_markdown_table(db, batch_id, top=args.top)
    return result


def _start_matrix_job(args: argparse.Namespace) -> JsonDict:
    job_id = f"matrix_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    paths = _job_paths(job_id)
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "matrix-run",
        "--pretty",
        "--matrix-batch-name",
        args.matrix_batch_name,
        "--data-source",
        args.data_source,
        "--top",
        str(args.top),
    ]
    if args.run_name_prefix:
        cmd.extend(["--run-name-prefix", args.run_name_prefix])
    for flag, value in (
        ("--output-count", args.output_count),
        ("--score-line", args.score_line),
        ("--agent-review-limit", args.agent_review_limit),
        ("--agent-review-concurrency", args.agent_review_concurrency),
    ):
        if value is not None:
            cmd.extend([flag, str(value)])
    for flag, enabled in (
        ("--skip-aux-refresh", args.skip_aux_refresh),
        ("--disable-agent-review", args.disable_agent_review),
        ("--stop-on-error", args.stop_on_error),
        ("--table", args.table),
    ):
        if enabled:
            cmd.append(flag)
    shell = f"{shlex.join(cmd)} > {shlex.quote(str(paths['stdout']))} 2> {shlex.quote(str(paths['stderr']))}; echo $? > {shlex.quote(str(paths['rc']))}"
    proc = subprocess.Popen(["/bin/bash", "-lc", shell], cwd=str(HERE))
    meta = {
        "job_id": job_id,
        "kind": "matrix-run",
        "pid": proc.pid,
        "status": "running",
        "started_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "command": " ".join(shlex.quote(part) for part in cmd),
        "stdout_path": str(paths["stdout"]),
        "stderr_path": str(paths["stderr"]),
    }
    paths["meta"].write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def _matrix_job_status(db: Session, args: argparse.Namespace) -> JsonDict:
    job = _load_job(args.job_id)
    result = job.get("result")
    if isinstance(result, dict) and result.get("matrix_batch_id"):
        job["matrix_batch_id"] = result.get("matrix_batch_id")
        if args.table and not result.get("markdown_table"):
            result["markdown_table"] = _matrix_markdown_table(db, str(result.get("matrix_batch_id")), top=args.top)
    return job


def _wait_matrix_job(db: Session, args: argparse.Namespace) -> JsonDict:
    deadline = time.monotonic() + args.timeout
    last: JsonDict | None = None
    while time.monotonic() <= deadline:
        last = _matrix_job_status(db, args)
        if not last.get("running"):
            return last
        time.sleep(args.interval)
    return {"timeout": True, "job": last}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ashare-cli",
        description="A股短线系统 CLI：供 Hermes/自动化调用预设、运行、推荐和解释能力。",
    )
    parser.add_argument("--pretty", action="store_true", help="格式化 JSON 输出")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("presets", help="列出预设")
    p.add_argument("--active-only", action="store_true")
    p.set_defaults(func=_list_presets)

    p = sub.add_parser("registry-diagnostics", help="诊断已加载策略与预设注册（只读）")
    p.add_argument("--preset-id", help="仅校验指定 preset 的 executable closure")
    p.set_defaults(func=_registry_diagnostics)

    p = sub.add_parser("runs", help="列出运行")
    p.add_argument("--run-mode", choices=["formal", "experiment"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--compact", action="store_true", default=True)
    p.set_defaults(func=_list_runs)

    p = sub.add_parser("run", help="查看单次运行")
    p.add_argument("run_id")
    p.add_argument("--compact", action="store_true", default=False)
    p.set_defaults(func=_show_run)

    p = sub.add_parser("execute-preset", help="执行预设")
    p.add_argument("preset_id")
    p.add_argument("--wait", action="store_true", help="同步等待完成；默认入队异步执行")
    p.add_argument("--run-name")
    p.add_argument("--data-source", choices=["local_pool", "local_pool_preferred", "realtime"])
    p.add_argument("--trade-date")
    p.add_argument("--output-count", type=int)
    p.add_argument("--score-line", type=float)
    p.add_argument("--skip-aux-refresh", action="store_true")
    p.set_defaults(func=_execute_preset)

    p = sub.add_parser("wait-run", help="等待运行进入终态")
    p.add_argument("run_id")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--compact", action="store_true", default=True)
    p.set_defaults(func=_wait_run)

    p = sub.add_parser("recommendations", help="查看某次运行的候选/推荐")
    p.add_argument("run_id")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--compact", action="store_true", default=True)
    p.set_defaults(func=_run_recommendations)

    p = sub.add_parser("recommendation", help="查看单条推荐详情")
    p.add_argument("recommendation_id")
    p.add_argument("--compact", action="store_true", default=False)
    p.set_defaults(func=_recommendation_detail)

    p = sub.add_parser("explain", help="解释单条推荐：分数、技能审查、漏斗轨迹、交易计划")
    p.add_argument("recommendation_id")
    p.add_argument("--history-limit", type=int, default=5)
    p.set_defaults(func=_explain_recommendation)

    p = sub.add_parser("funnel", help="查看运行漏斗/候选过滤审计")
    p.add_argument("run_id")
    p.add_argument("--code", help="只看指定股票代码")
    p.set_defaults(func=_run_funnel)

    p = sub.add_parser("watch", help="设置关注/忽略")
    p.add_argument("recommendation_id")
    p.add_argument("--status", default="watching", choices=["watching", "ignored", "none"])
    p.add_argument("--note")
    p.set_defaults(func=_watch_recommendation)

    p = sub.add_parser("review", help="写入复盘标签/备注")
    p.add_argument("recommendation_id")
    p.add_argument("--user-label")
    p.add_argument("--failure-reason")
    p.add_argument("--text")
    p.set_defaults(func=_review_recommendation)

    p = sub.add_parser("data-sources", help="查看数据源健康状态")
    p.set_defaults(func=_data_sources)

    p = sub.add_parser("pool-status", help="查看本地行情池完整性")
    p.add_argument("--unified-limit", type=int, default=50)
    p.add_argument("--unified-offset", type=int, default=0)
    p.add_argument("--bars-limit", type=int, default=50)
    p.add_argument("--bars-offset", type=int, default=0)
    p.add_argument("--summary-only", action="store_true")
    p.set_defaults(func=_pool_status)

    p = sub.add_parser("refresh-pool", help="刷新报价池")
    p.set_defaults(func=_refresh_pool)

    p = sub.add_parser("sync-pool-gaps", help="补齐本地池日K缺口")
    p.add_argument("--code-limit", type=int, default=0)
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--min-bars", type=int, default=30)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_sync_pool_gaps)

    p = sub.add_parser("sync-run-aux", help="补齐某次推荐运行的辅助数据")
    p.add_argument("run_id")
    p.add_argument("--target-tables", help="逗号分隔，如 news,capital_flow")
    p.add_argument("--code-limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_sync_run_aux)

    p = sub.add_parser("data-health", help="查看数据完整性/健康评分")
    p.set_defaults(func=_data_health)

    p = sub.add_parser("alerts", help="查看数据源与系统告警")
    p.set_defaults(func=_alerts)

    p = sub.add_parser("readiness", help="查看系统就绪检查")
    p.set_defaults(func=_readiness)

    p = sub.add_parser("matrix-run", help="一次执行全部启用预设，生成推荐矩阵批次")
    p.add_argument("--async-job", action="store_true", help="后台启动长任务，立即返回 job_id")
    p.add_argument("--matrix-batch-name", default="CLI预设矩阵")
    p.add_argument("--run-name-prefix")
    p.add_argument("--data-source", default="local_pool_preferred", choices=["local_pool", "local_pool_preferred", "realtime"])
    p.add_argument("--output-count", type=int)
    p.add_argument("--score-line", type=float)
    p.add_argument("--skip-aux-refresh", action="store_true")
    p.add_argument("--disable-agent-review", action="store_true")
    p.add_argument("--agent-review-limit", type=int)
    p.add_argument("--agent-review-concurrency", type=int)
    p.add_argument("--stop-on-error", action="store_true")
    p.add_argument("--table", action="store_true", help="同时返回 Markdown 表格")
    p.add_argument("--top", type=int, default=5, help="每个预设展示 Top N 推荐")
    p.set_defaults(func=_run_matrix)

    p = sub.add_parser("matrix-result", help="查看最近或指定批次的推荐矩阵")
    p.add_argument("--batch-id")
    p.add_argument("--table", action="store_true", help="返回 Markdown 表格")
    p.add_argument("--top", type=int, default=5, help="每个预设展示 Top N 推荐")
    p.set_defaults(func=_matrix_result)

    p = sub.add_parser("matrix-status", help="查看异步矩阵任务状态")
    p.add_argument("job_id")
    p.add_argument("--table", action="store_true", help="完成后补充 Markdown 表格")
    p.add_argument("--top", type=int, default=5, help="每个预设展示 Top N 推荐")
    p.set_defaults(func=_matrix_job_status)

    p = sub.add_parser("matrix-wait", help="等待异步矩阵任务完成")
    p.add_argument("job_id")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--interval", type=float, default=10.0)
    p.add_argument("--table", action="store_true", help="完成后补充 Markdown 表格")
    p.add_argument("--top", type=int, default=5, help="每个预设展示 Top N 推荐")
    p.set_defaults(func=_wait_matrix_job)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    pretty_flag = False
    if "--pretty" in raw_argv:
        pretty_flag = True
        raw_argv = [arg for arg in raw_argv if arg != "--pretty"]
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if pretty_flag:
        args.pretty = True
    try:
        return _with_db(args.func, args)
    except Exception as exc:  # noqa: BLE001 - CLI must return structured errors to callers
        _emit({"ok": False, "error": str(exc), "type": exc.__class__.__name__}, pretty=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

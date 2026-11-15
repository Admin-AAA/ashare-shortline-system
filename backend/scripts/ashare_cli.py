from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.tables import Recommendation, ReviewNote, WatchItem
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
from app.services.workbench_service import (
    get_recommendation_detail,
    get_run_detail,
    get_run_filter_audit,
    list_run_recommendations,
    list_runs,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ashare-cli",
        description="A股短线系统 CLI：供 Hermes/自动化调用运行、推荐和解释能力。",
    )
    parser.add_argument("--pretty", action="store_true", help="格式化 JSON 输出")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("runs", help="列出运行")
    p.add_argument("--run-mode", choices=["formal", "experiment"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--compact", action="store_true", default=True)
    p.set_defaults(func=_list_runs)

    p = sub.add_parser("run", help="查看单次运行")
    p.add_argument("run_id")
    p.add_argument("--compact", action="store_true", default=False)
    p.set_defaults(func=_show_run)

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

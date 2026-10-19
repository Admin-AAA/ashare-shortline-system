"""正式出票后的异步 LLM 风险备注（L-171）。

正式 run 在管线内关闭 LLM（disable_agent_review），先按规则出票落库；
本模块在出票完成后对**已出票**股票做 LLM 审查，只写入 agent_summary_json /
risk_tags_json，不改 final_score、不删票、不否决。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _loads(raw: str | None, default: Any) -> Any:
    try:
        return json.loads(raw or "") if raw else default
    except (TypeError, ValueError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


async def attach_llm_risk_notes_for_run(db: Session, run_id: str) -> dict[str, Any]:
    """对已完成 formal run 的推荐异步挂 LLM 风险备注。失败只记日志，不影响出票。"""
    from app.agents.review_runner import run_skill_reviews
    from app.core.config import get_settings
    from app.engine.skill_loader import load_skills
    from app.models.tables import Recommendation, RecommendationRun
    from app.services.runs.skill_gate import resolve_skill_review_concurrency
    from pathlib import Path

    run = db.get(RecommendationRun, run_id)
    if run is None:
        return {"run_id": run_id, "status": "missing_run", "updated": 0}

    recs = (
        db.query(Recommendation)
        .filter(Recommendation.run_id == run_id)
        .all()
    )
    if not recs:
        return {"run_id": run_id, "status": "no_recommendations", "updated": 0}

    selected = _loads(getattr(run, "selected_skills_json", None), [])
    skill_ids = [
        str(row.get("skill_id") or "")
        for row in selected
        if isinstance(row, dict) and row.get("skill_id")
    ]
    skills_dir = Path(__file__).resolve().parents[2] / "skills"
    all_skills = {s.skill_id: s for s in load_skills(skills_dir)}
    review_skills = [all_skills[sid] for sid in skill_ids if sid in all_skills]
    if not review_skills:
        return {"run_id": run_id, "status": "no_skills", "updated": 0}

    snapshot = _loads(getattr(run, "market_snapshot_json", None), {}) or {}
    concurrency = resolve_skill_review_concurrency({}, get_settings())
    limiter = asyncio.Semaphore(max(1, concurrency))
    session_id = f"risk_notes_{run_id}"
    updated = 0
    errors = 0

    for rec in recs:
        stock = _loads(rec.feature_snapshot_json, {}) or {}
        if not stock.get("code"):
            stock = {
                "code": rec.code,
                "name": rec.name,
                "industry": rec.industry,
                "close": rec.recommend_price,
            }
        rule_signals: dict[str, Any] = {}
        breakdown = _loads(rec.skill_breakdown_json, []) or []
        for row in breakdown:
            if isinstance(row, dict) and row.get("strategy_id"):
                rule_signals[str(row["strategy_id"])] = row
        try:
            reviews = await run_skill_reviews(
                stock=stock,
                skills=review_skills,
                snapshot=dict(snapshot),
                rule_signals_by_skill=rule_signals,
                session_id=session_id,
                request_limiter=limiter,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("异步 LLM 风险备注失败 run=%s code=%s: %s", run_id, rec.code, exc)
            errors += 1
            continue

        summary = _loads(rec.agent_summary_json, {}) or {}
        summary["mode"] = "async_risk_notes"
        summary["async_attached"] = True
        summary["reviews"] = []
        risk_tags = list(_loads(rec.risk_tags_json, []) or [])
        for item in reviews:
            review = item.get("review") if isinstance(item, dict) else None
            err = item.get("error") if isinstance(item, dict) else None
            entry = {
                "skill_id": item.get("skill_id") if isinstance(item, dict) else None,
                "error": err,
                "review": review,
            }
            summary["reviews"].append(entry)
            if not isinstance(review, dict):
                continue
            if not summary.get("stance"):
                for key in ("stance", "score_delta", "score", "summary", "action",
                            "position_range", "entry_style", "risks", "supports"):
                    if key in review:
                        summary[key] = review[key]
            stance = str(review.get("stance") or "")
            if stance in {"veto", "oppose"}:
                note = str(review.get("summary") or stance)
                tag = f"LLM风险备注:{stance}:{note}"[:120]
                if tag not in risk_tags:
                    risk_tags.append(tag)
            for risk in (review.get("risks") or [])[:5]:
                tag = f"LLM风险:{risk}"[:120]
                if tag not in risk_tags:
                    risk_tags.append(tag)

        rec.agent_summary_json = _dumps(summary)
        rec.risk_tags_json = _dumps(risk_tags)
        updated += 1

    db.commit()
    return {"run_id": run_id, "status": "ok", "updated": updated, "errors": errors}


def schedule_attach_llm_risk_notes(run_id: str):
    """在独立线程里跑异步挂备注，不阻塞出票调度；返回线程。

    注意：Prefect 每日任务跑在子进程里，子进程退出时 daemon 线程会被直接杀掉（10-14 起审查静默的原因）。
    调用方应在出票全部完成后用 wait_llm_risk_note_threads() 限时等待。"""
    import threading

    def _worker() -> None:
        from app.db.session import SessionLocal

        try:
            with SessionLocal() as db:
                asyncio.run(attach_llm_risk_notes_for_run(db, run_id))
        except Exception:  # noqa: BLE001
            logger.exception("异步 LLM 风险备注线程失败 run=%s", run_id)

    t = threading.Thread(
        target=_worker,
        name=f"llm-risk-notes-{run_id[:12]}",
        daemon=True,
    )
    t.start()
    return t


def wait_llm_risk_note_threads(threads, timeout_seconds: float = 600.0) -> dict[str, Any]:
    """出票后限时等待风险备注线程（共享一个截止时间）。超时只告警，不影响出票结果。"""
    import time

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    pending = []
    for t in [t for t in threads if t is not None]:
        t.join(max(0.0, deadline - time.monotonic()))
        if t.is_alive():
            pending.append(t.name)
    if pending:
        logger.warning("异步 LLM 风险备注超时 %ss 未完成: %s", timeout_seconds, ",".join(pending))
    out = {"waited": len([t for t in threads if t is not None]), "timed_out": pending}
    logger.info("异步 LLM 风险备注等待结束: %s", out)
    return out


__all__ = [
    "attach_llm_risk_notes_for_run",
    "schedule_attach_llm_risk_notes",
    "wait_llm_risk_note_threads",
]

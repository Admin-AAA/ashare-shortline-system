"""正式出票后的 LLM 风险备注（L-171）。

正式 run 在管线内关闭 LLM（disable_agent_review），先按规则出票落库；
本模块在出票完成后对**已出票**股票做 LLM 审查，只写入 agent_summary_json /
risk_tags_json，不改 final_score、不删票、不否决。

2026-10-19 曾停用：原实现用 daemon 线程后台跑，而正式流水线跑在 Prefect 子进程里，
子进程退出会把 daemon 线程直接杀掉，10-14 起审查静默。2026-11-25 恢复，改为在
出票调度里同步 await（每日出票量 0~6 只，并发 10，耗时可忽略），不再依赖线程存活。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# review-health 的 enabled / status 与本开关同源。
LLM_RISK_NOTES_ENABLED = True


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


async def attach_llm_risk_notes(run_id: str) -> dict[str, Any]:
    """出票完成后同步挂 LLM 风险备注；用独立 session，失败只记日志不影响出票。"""
    if not LLM_RISK_NOTES_ENABLED:
        return {"run_id": run_id, "status": "disabled", "updated": 0}

    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            return await attach_llm_risk_notes_for_run(db, run_id)
    except Exception as exc:  # noqa: BLE001 - 备注失败不得回滚已完成的出票
        logger.exception("LLM 风险备注失败 run=%s: %s", run_id, exc)
        return {"run_id": run_id, "status": "failed", "updated": 0, "error": str(exc)[:300]}


__all__ = [
    "LLM_RISK_NOTES_ENABLED",
    "attach_llm_risk_notes",
    "attach_llm_risk_notes_for_run",
]

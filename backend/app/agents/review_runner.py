from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.agents.session_registry import AgentSessionRegistry
from app.agents.agno_skill_agent import run_skill_agent_direct
from app.agents.hallucination_check import verify_review_output
from app.engine.skill_loader import StrategySkill


def _prime_agent_session_context(
    session_id: str,
    stock: dict[str, Any],
    rule_signals_by_skill: dict[str, dict[str, Any]],
) -> None:
    """预热 Agent 会话缓存，让 Tool 调用拿到正式链路上下文。"""
    ctx = AgentSessionRegistry.get(session_id)
    if ctx is None:
        return

    code = str(stock.get("code") or "").strip()
    if code:
        ctx.stocks_cache[code] = dict(stock)

    for skill_id, signal in (rule_signals_by_skill or {}).items():
        if not skill_id or not isinstance(signal, dict) or not code:
            continue
        ctx.strategy_signals_cache[f"{skill_id}:{code}"] = dict(signal)


async def run_skill_reviews(
    stock: dict[str, Any],
    skills: list[StrategySkill],
    snapshot: dict[str, Any],
    rule_signals_by_skill: dict[str, dict[str, Any]],
    session_id: str,
) -> list[dict[str, Any]]:
    """
    对一只股票，并发调用所有已启用的 Skill Agent。

    Args:
        stock: 股票特征数据
        skills: 已加载的所有 Skill（只运行 enabled 且有 skill_body 的）
        snapshot: 市场快照
        rule_signals_by_skill: {skill_id: rule_signal} 映射
        session_id: 会话 ID

    Returns:
        [{"skill_id": str, "skill_name": str, "review": SkillReviewOutput,
          "hallucination_check": dict, "error": str|None}, ...]
    """

    from app.core.config import get_settings

    # 筛选有 SKILL.md 的已启用 Skill
    active_skills = [s for s in skills if s.enabled and s.skill_body.strip()]

    SKILL_TIMEOUT_SECONDS = max(60, int(get_settings().agent_skill_timeout_seconds))
    _prime_agent_session_context(session_id, stock, rule_signals_by_skill)

    async def review_one(skill: StrategySkill, stagger_index: int = 0) -> dict[str, Any]:
        # 交错启动，避免瞬间并发冲击 LLM API
        if stagger_index > 0:
            await asyncio.sleep(0.3 * stagger_index)

        result = {
            "skill_id": skill.skill_id,
            "skill_name": skill.name,
            "skill_hash": skill.skill_hash,
            "review": None,
            "hallucination_check": None,
            "error": None,
        }
        try:
            rule_signal = rule_signals_by_skill.get(skill.skill_id)

            review = await asyncio.wait_for(
                run_skill_agent_direct(
                    skill=skill,
                    stock=stock,
                    snapshot=snapshot,
                    rule_signal=rule_signal,
                ),
                timeout=SKILL_TIMEOUT_SECONDS,
            )
            raw = review.model_dump() if hasattr(review, 'model_dump') else review

            # 验证输出（在字段名标准化之前，hallucination_check 读取 evidence）
            hc = verify_review_output(
                review=raw,
                stock=stock,
                snapshot=snapshot,
                rule_signal=rule_signal,
            )

            # 标准化字段名：SkillReviewOutput 用 evidence/score，下游用 supports/score_delta
            raw.setdefault("runtime", "agent_skill_agno")
            raw.setdefault("supports", list(raw.get("evidence") or []))
            raw["score_delta"] = round(float(raw.get("score", 50)) - 50.0, 2)
            result["review"] = raw
            result["hallucination_check"] = hc

        except asyncio.TimeoutError:
            result["error"] = f"Skill '{skill.name}' 审查超时 (>{SKILL_TIMEOUT_SECONDS}s)，已跳过"
        except Exception as e:
            result["error"] = str(e)

        return result

    # 并发执行所有 Skill Agent（交错启动以减轻 API 瞬时压力）
    tasks = [review_one(s, i) for i, s in enumerate(active_skills)]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return list(results)


async def run_full_review(
    stocks: list[dict[str, Any]],
    skills: list[StrategySkill],
    snapshot: dict[str, Any],
    db: Session,
) -> list[dict[str, Any]]:
    """
    完整推荐审查流程：注册会话 -> 并发审查所有股票 -> 返回结果。

    Args:
        stocks: 候选股票列表
        skills: 已加载的 Skill 列表
        snapshot: 市场快照
        db: SQLAlchemy 数据库会话

    Returns:
        [{**stock, "skill_reviews": [...], "agent_review_score": float, "agent_logs": [...]}, ...]
    """

    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    AgentSessionRegistry.register(session_id, SessionLocal, skills, snapshot)

    try:
        # 为每只股票预计算 rule_signals（由外部 workbench_service 传入更好，
        # 但这里我们接收预计算好的或者自行计算）
        # 注意：rule_signals_by_skill 需要外部传入或在这里计算
        # 简化处理：每个 stock 应带有 pre_computed rule_signals

        reviewed_stocks = []
        for stock in stocks:
            skill_reviews = await run_skill_reviews(
                stock=stock,
                skills=skills,
                snapshot=snapshot,
                rule_signals_by_skill=stock.get("_rule_signals", {}),
                session_id=session_id,
            )

            # 计算 agent_review_score（所有 Skill 评分的加权平均）
            valid_reviews = [r for r in skill_reviews if r.get("review") and not r.get("error")]
            if valid_reviews:
                scores = [r["review"]["score"] for r in valid_reviews]
                confidences = [r["review"]["confidence"] for r in valid_reviews]
                avg_score = sum(s * c for s, c in zip(scores, confidences)) / max(sum(confidences), 0.01)
            else:
                avg_score = 50.0  # 默认中性

            # 构建 Agent 日志（兼容旧 agent_decision_logs 表结构）
            agent_logs = []
            for r in skill_reviews:
                agent_logs.append({
                    "agent_name": f"Skill Agent · {r.get('skill_name', 'Unknown')}",
                    "output": r.get("review") or {"error": r.get("error", "Unknown")},
                })

            reviewed_stocks.append({
                **stock,
                "skill_reviews": skill_reviews,
                "agent_review_score": round(avg_score, 2),
                "agent_logs": agent_logs,
            })

        return reviewed_stocks

    finally:
        AgentSessionRegistry.unregister(session_id)

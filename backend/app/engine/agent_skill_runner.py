from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.agents.agno_skill_agent import SkillReviewOutput, create_skill_agent, run_skill_agent
from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill


OUTPUT_SCHEMA = {
    "stance": "strong_support | support | neutral_support | neutral | oppose | veto",
    "score_delta": "number between -18 and 16",
    "confidence": "number between 0 and 1",
    "lenses": ["Skill 中实际使用的分析镜片"],
    "supports": ["支持该股票进入观察/推荐池的证据"],
    "risks": ["反对、否决或仅观察的风险"],
    "summary": "一句中文审查结论",
    "action": "观察 | 轻仓试错 | 确认后加仓 | 不做",
    "position_range": "建议仓位范围",
    "entry_style": "建议入场方式",
    "invalidation_conditions": ["使当前判断失效的条件"],
    "next_day_plan": ["次日跟踪或执行计划"],
}


@dataclass(frozen=True)
class AgentSkillInvocation:
    """Agno Agent 调用参数（替代原来 HTTP 请求参数包）。"""

    runtime: str
    skill: StrategySkill
    stock: dict[str, Any]
    snapshot: dict[str, Any]
    rule_signal: dict[str, Any] | None
    history: dict[str, Any] | None
    session_id: str


def run_agent_skill(
    skill: StrategySkill,
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None = None,
    history: dict[str, Any] | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """通过 Agno Agent 执行 Skill 审查。

    使用 Agno Agent 框架（内置工具查询、结构化输出和重试机制），
    替代原来 HTTP 直调 DeepSeek API。

    SKILL.md 是最高优先级的方法论来源，Agent 按 Skill 的视角审查候选股票。
    """

    settings = get_settings()
    if settings.agent_skill_runtime != "llm":
        raise RuntimeError("Agent Skill 已配置为强制真实 LLM，请将 ASHARE_AGENT_SKILL_RUNTIME 设为 llm。")
    if not settings.agent_skill_api_key:
        raise RuntimeError("未配置 ASHARE_AGENT_SKILL_API_KEY，无法执行真实 LLM Agent Skill。")

    # 生成会话 ID（若未提供）
    own_session = not bool(session_id)
    if own_session:
        session_id = f"agent_skill_{uuid.uuid4().hex[:16]}"

    # 若是自生成 session，注册到全局注册表，让工具函数可以查库
    if own_session:
        from app.agents.session_registry import AgentSessionRegistry
        from app.db.session import SessionLocal

        AgentSessionRegistry.register(
            session_id=session_id,
            session_factory=lambda: SessionLocal(),
            skills=[skill],
            snapshot=snapshot,
        )

    try:
        invocation = build_agent_skill_invocation(skill, stock, snapshot, rule_signal, history, session_id)
        review_raw = _run_llm_review(invocation)
        review = normalize_agent_review(review_raw)
    finally:
        if own_session:
            AgentSessionRegistry.unregister(session_id)

    return {
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "skill_hash": skill.skill_hash,
        "runtime": review.pop("runtime", invocation.runtime),
        "instruction_digest": {
            "frontmatter": _json_safe(skill.skill_frontmatter),
            "principles": extract_principles(skill.skill_body)[:8],
            "body_hash": skill.skill_hash,
            "reference_files": _json_safe(skill.reference_files[:8]),
            "prompt_chars": len(skill.skill_body),
        },
        "prompt_contract": {
            "uses_skill_md": bool(skill.skill_body.strip()),
            "schema": OUTPUT_SCHEMA,
            "input_sources": _json_safe(_input_sources(stock, snapshot)),
            "history_items": len((history or {}).get("same_stock_history", [])),
        },
        **review,
    }


def build_agent_skill_invocation(
    skill: StrategySkill,
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None = None,
    history: dict[str, Any] | None = None,
    session_id: str = "",
) -> AgentSkillInvocation:
    """构建 Agno Agent 调用参数（而非原来的 HTTP 请求参数）。"""
    if not session_id:
        session_id = f"agent_skill_{uuid.uuid4().hex[:16]}"
    return AgentSkillInvocation(
        runtime="agent_skill_agno",
        skill=skill,
        stock=stock,
        snapshot=snapshot,
        rule_signal=rule_signal,
        history=history,
        session_id=session_id,
    )


def enrich_signal_with_agent_review(signal: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(signal)
    enriched["agent_skill_review"] = review
    enriched["runtime"] = "strategy_yaml+agent_skill"
    score_cap = float(enriched.get("score_cap") or 100.0)
    # 兼容两种字段名：score_delta（旧）或 score（SkillReviewOutput 原始）
    if "score_delta" in review:
        delta = float(review.get("score_delta", 0))
    else:
        delta = float(review.get("score", 50)) - 50.0
    enriched["score"] = max(0.0, min(score_cap, float(enriched.get("score", 0)) + delta))
    enriched["confidence"] = max(float(enriched.get("confidence", 0)), float(review.get("confidence", 0)))
    enriched.setdefault("evidence", [])
    review_runtime = str(review.get("runtime") or "")
    summary = str(review.get("summary", "")).strip()
    if summary and review_runtime != "agent_skill_fallback_prelim" and not bool(review.get("summary_is_warning")):
        enriched["evidence"] = list(enriched["evidence"]) + [summary]
    enriched.setdefault("risk_flags", [])
    enriched["risk_flags"] = list(dict.fromkeys(list(enriched["risk_flags"]) + review.get("risks", [])))
    if review.get("stance") == "veto":
        enriched["hard_veto"] = True
        enriched["veto_reason"] = str(review.get("summary") or "Agent Skill 风险否决")
    if score_cap < 100:
        enriched["risk_flags"] = list(dict.fromkeys(list(enriched["risk_flags"]) + ["样本或证据覆盖不足，Agent 加分受限"]))
    return enriched


def agent_only_signal(skill: StrategySkill, stock: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    if "score_delta" in review:
        score = 50 + float(review.get("score_delta", 0))
    else:
        score = float(review.get("score", 50))
    return {
        "strategy_id": skill.skill_id,
        "strategy_name": skill.name,
        "version_id": f"{skill.skill_id}:{skill.version}:{skill.skill_hash[:8] if skill.skill_hash else 'skillmd'}",
        "phase": "Agent Skill 审查",
        "score": round(max(0.0, min(100.0, score)), 2),
        "confidence": review.get("confidence", 0.45),
        "setup_match": "Skill 方法论匹配" if review.get("stance") in {"support", "strong_support"} else "",
        "entry_rule": _agent_entry_rule(stock, review),
        "invalid_rule": _agent_invalid_rule(review),
        "setup_quality": 55 + max(0.0, float(review.get("score_delta", 0))),
        "hard_veto": review.get("stance") == "veto",
        "veto_reason": "Agent Skill 风险否决" if review.get("stance") == "veto" else "",
        "risk_flags": review.get("risks", []),
        "evidence": review.get("supports", []) + [review.get("summary", "")],
        "weight": 1.0,
        "runtime": "agent_skill_only",
        "agent_skill_review": review,
    }


def normalize_agent_review(review: dict[str, Any]) -> dict[str, Any]:
    stance = str(_required(review, "stance"))
    if stance not in {"strong_support", "support", "neutral_support", "neutral", "oppose", "veto"}:
        raise RuntimeError(f"LLM Agent Skill 返回了无效 stance: {stance}")
    score_delta = _bounded_float(_required(review, "score_delta"), -18.0, 16.0, "score_delta")
    confidence = _bounded_float(_required(review, "confidence"), 0.0, 1.0, "confidence")
    lenses = _required_string_list(review, "lenses")[:8]
    supports = _required_string_list(review, "supports")[:8]
    risks = _required_string_list(review, "risks")[:8]
    summary = str(_required(review, "summary")).strip()
    if not summary:
        raise RuntimeError("LLM Agent Skill 返回的 summary 为空。")
    normalized = {
        **{k: v for k, v in review.items() if k not in {"stance", "score_delta", "confidence", "lenses", "supports", "risks", "summary"}},
        "stance": stance,
        "score_delta": round(score_delta, 2),
        "confidence": round(confidence, 2),
        "lenses": lenses,
        "supports": supports,
        "risks": risks,
        "summary": summary,
    }
    return normalized


def load_reference_snippets(skill: StrategySkill) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for ref in skill.reference_files[:6]:
        ref_path = skill.path / str(ref["path"])
        if not _is_inside(skill.path, ref_path) or not ref_path.exists():
            continue
        try:
            text = ref_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        snippets.append(
            {
                "path": ref["path"],
                "sha256": ref.get("sha256"),
                "excerpt": _trim(text, 1800),
            }
        )
    return snippets


def extract_principles(skill_body: str) -> list[str]:
    lines = []
    for raw_line in skill_body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("-", "*")):
            line = line[1:].strip()
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if len(line) < 4:
            continue
        if len(line) > 140:
            line = line[:140]
        if any(
            keyword in line
            for keyword in ["原则", "只做", "不", "买点", "风险", "主线", "龙头", "情绪", "仓位", "回撤", "Protocol", "步骤", "工作流"]
        ):
            lines.append(_clean_markdown(line))
    return list(dict.fromkeys(lines))[:16]


# ── Agno Agent 审查执行（带重试 + 指数退避） ──────────────────────────


def _run_llm_review(invocation: AgentSkillInvocation) -> dict[str, Any]:
    """通过 Agno Agent 执行 Skill 审查。

    使用 Agno 框架的 create_skill_agent + run_skill_agent。
    Agno Agent 内置工具查询（市场快照、个股详情、数据质量审计、策略信号）
    和重试机制（3 次 + 指数退避）。

    将 Agno 的 SkillReviewOutput 转换为兼容旧接口的 dict 格式。
    """
    settings = get_settings()
    skill = invocation.skill
    stock = invocation.stock
    snapshot = invocation.snapshot
    rule_signal = invocation.rule_signal
    session_id = invocation.session_id

    agent = create_skill_agent(skill, session_id)

    # run_skill_agent 是 async，使用 asyncio.run() 桥接同步调用
    try:
        output: SkillReviewOutput = asyncio.run(
            run_skill_agent(
                agent=agent,
                stock=stock,
                snapshot=snapshot,
                rule_signal=rule_signal,
                session_id=session_id,
                skill_id=skill.skill_id,
            )
        )
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Agno Agent Skill 审查失败: {e}") from e

    # 将 Agno 输出转换为兼容旧格式（score_delta, supports, lenses）
    raw_score = output.score
    score_delta = max(-18.0, min(16.0, raw_score - 50.0))
    summary = output.summary or f"{skill.name} Agent 审查完成"

    return {
        "stance": output.stance,
        "score_delta": round(score_delta, 2),
        "confidence": round(output.confidence, 2),
        "lenses": ["SKILL.md 方法论"],
        "supports": output.evidence,
        "risks": output.risks,
        "summary": summary,
        "market_regime_judgment": output.market_regime_judgment,
        "action": output.action,
        "position_range": output.position_range,
        "entry_style": output.entry_style,
        "invalidation_conditions": output.invalidation_conditions,
        "next_day_plan": output.next_day_plan,
        "raw_score": raw_score,
        "runtime": "agent_skill_agno",
    }


# ── 内部工具函数 ──────────────────────────────────────────────────────


def _compact_stock(stock: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "code",
        "name",
        "industry",
        "price",
        "pct",
        "turnover_rate",
        "amount_yi",
        "free_mv_yi",
        "dist20",
        "dist60",
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "vol5_20",
        "primary_excess20",
        "off_high_pct",
        "max_dd20",
        "upper_shadow_count20",
        "limit_up_days20",
        "news_risk_count",
        "announcement_negative_count",
        "data_source",
        "quote_source",
        "kline_source",
        "latest_trade_date",
        "data_quality",
        "market_regime",
        "is_main_theme",
        "is_shortline_leader_candidate",
        "shortline_leader_score",
        "market_sentiment_phase",
        "limit_up_overheat_score",
        "limit_up_overheat_level",
        "sector_ladder_completeness_score",
        "sector_ladder_level",
        "sector_ladder_limit_up_count",
        "sector_ladder_height",
        "capital_resonance_score",
        "capital_resonance_signal",
        "capital_resonance_source",
        "is_20cm_stock",
        "twenty_cm_risk_score",
        "twenty_cm_risk_gate",
        "twenty_cm_risk_reason",
        "dragon_tiger_buy_dispersion",
        "dragon_tiger_inst_buy_ratio",
        "shortline_factor_explanations",
        "is_limit_up",
        "dragon_tiger_is_on_list",
        "dragon_tiger_recent_count",
        "concept_sector_name",
        "concept_sector_heat_rank",
        "super_large_net_inflow_yi",
        "large_net_inflow_yi",
        "medium_net_inflow_yi",
        "small_net_inflow_yi",
        "main_net_inflow_yi",
        "main_net_inflow_ratio",
        # DSA 评分关键字段（防止 Agent 凭空推断"MA5支撑确认"等）
        "support_ma5", "support_ma10", "dsa_support_score",
        "trend_status", "ma_alignment",
        "macd_status", "macd_signal",
        "rsi_status", "rsi_signal",
        "bias_ma5", "bias_ma10", "bias_ma20",
        "volume_breakout_signal", "volume_shrink_pullback",
        "pos20", "pos60", "pos120",
        "chg20", "chg60",
        "breakout20", "breakout60",
    ]
    return {key: stock.get(key) for key in keys if key in stock}


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "trade_date",
        "sample_count",
        "up_count",
        "down_count",
        "limit_up_count",
        "limit_down_count",
        "main_industries",
        "breadth",
    ]
    return {key: snapshot.get(key) for key in keys if key in snapshot}


def _input_sources(stock: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_source": stock.get("data_source"),
        "quote_source": stock.get("quote_source"),
        "kline_source": stock.get("kline_source"),
        "latest_trade_date": stock.get("latest_trade_date"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_source": (snapshot.get("breadth") or {}).get("data_source"),
        "missing_fields": (stock.get("data_quality") or {}).get("missing_fields", []),
    }


def _agent_entry_rule(stock: dict[str, Any], review: dict[str, Any]) -> str:
    if review.get("entry_style"):
        style = str(review.get("entry_style")).strip()
        position = str(review.get("position_range") or "").strip()
        if position:
            return f"{style}；建议仓位 {position}"
        return style
    if review.get("supports"):
        return "等待 Skill 方法论中的关键买点被盘面确认后再参与"
    return "仅观察，不主动买入"


def _agent_invalid_rule(review: dict[str, Any]) -> str:
    invalidation_conditions = review.get("invalidation_conditions") or []
    if invalidation_conditions:
        return f"若出现失效条件: {str(invalidation_conditions[0])}"
    risks = review.get("risks", [])
    if risks:
        return f"若出现/强化风险: {risks[0]}"
    return "Skill 核心假设失效时退出观察"


def _clean_markdown(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return text.strip()


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 80].rstrip() + "\n\n...[内容已截断，完整内容保存在本地 Skill 包中]..."


def _required(review: dict[str, Any], field: str) -> Any:
    if field not in review:
        raise RuntimeError(f"LLM Agent Skill 返回缺少字段: {field}")
    return review[field]


def _required_string_list(review: dict[str, Any], field: str) -> list[str]:
    value = _required(review, field)
    if not isinstance(value, list):
        raise RuntimeError(f"LLM Agent Skill 返回字段 {field} 必须是数组。")
    return [str(item).strip() for item in value if str(item).strip()]


def _bounded_float(value: Any, minimum: float, maximum: float, field: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"LLM Agent Skill 返回字段 {field} 必须是数字。") from None
    if numeric < minimum or numeric > maximum:
        raise RuntimeError(f"LLM Agent Skill 返回字段 {field} 超出范围 [{minimum}, {maximum}]: {numeric}")
    return numeric


def _is_inside(root: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value

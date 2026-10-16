"""
Skill Agent 工厂 — 每个 Skill 实例化为一个 Agno Agent。

核心设计:
  - Skill 即 Agent。SKILL.md 是 System Prompt，references 是知识库。
  - Agent 拥有查询本地数据池的 Tools，可以主动获取所需数据。
  - 输出结构化 Pydantic 模型，保证 schema 一致性。
  - 内置重试 + 容错 + hallucination 自检提醒。

未来新增 Skill 无需修改此文件——只需在 skills/ 目录放入 SKILL.md + manifest.json。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.engine.skill_loader import StrategySkill

from app.agents.circuit_breaker import check_circuit, record_result
from app.agents.tools import (
    audit_data_quality,
    query_dragon_tiger,
    query_industry_peers,
    query_market_snapshot,
    query_skill_signals,
    query_stock_detail,
    query_stock_history,
)
from app.services.model_router_service import OPENAI_CLIENT_HEADERS, get_active_model_routes
from app.services.llm_review_cache_service import build_review_cache_key, semantic_market_snapshot
from app.services.llm.prompt_context import (
    MARKET_SNAPSHOT_FIELDS,
    RULE_SIGNAL_FIELDS,
    STOCK_FIELDS,
    PromptContextProjection,
    canonical_section_json,
    project_prompt_context,
)
import logging

from app.core import metrics as _metrics  # noqa: E402

logger = logging.getLogger(__name__)


# ── 结构化输出模型 ────────────────────────────────────────────────────

class SkillReviewOutput(BaseModel):
    """Skill Agent 审查输出。与旧 OUTPUT_SCHEMA 兼容，增强为 agent_review_score。"""

    stance: str = Field(
        description="审查立场: strong_support | support | neutral_support | neutral | oppose | veto"
    )
    score: int = Field(ge=0, le=100, description="Agent 综合评分 (0-100)")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 (0-1)")
    evidence: list[str] = Field(default_factory=list, description="支持该股票进入观察/推荐池的证据")
    risks: list[str] = Field(default_factory=list, description="反对、否决或仅观察的风险")
    summary: str = Field(default="", description="一句中文审查结论")
    market_regime_judgment: str = Field(
        default="", description="从 Skill 视角对当前市场状态的判断（如 情绪退潮/分歧转一致/主升加速 等）"
    )
    action: str = Field(default="观察", description="交易动作建议: 观察 | 轻仓试错 | 确认后加仓 | 不做")
    position_range: str = Field(default="0-5%", description="建议仓位范围，如 0%、5-15%、20-40%")
    entry_style: str = Field(default="", description="建议入场方式，如 分歧低吸 / 回封确认 / 突破跟随")
    invalidation_conditions: list[str] = Field(
        default_factory=list,
        description="使当前判断失效的条件，优先写市场、主线、承接、量能等风险触发器",
    )
    next_day_plan: list[str] = Field(
        default_factory=list,
        description="次日跟踪或执行计划，强调竞价、承接、量能、主线反馈",
    )


# 模块级成本累积器（线程安全）
_pending_api_costs: list[dict[str, Any]] = []


def drain_api_costs() -> list[dict[str, Any]]:
    """取出并清空累积的 API 调用成本记录。"""
    global _pending_api_costs
    costs = _pending_api_costs[:]
    _pending_api_costs = []
    return costs


_ROUTE_FAILOVER_ERROR_MARKERS = (
    "insufficient balance",
    "insufficient_balance",
    "quota exceeded",
    "invalid api key",
    "incorrect api key",
    "authentication",
    "unauthorized",
    "forbidden",
    "rate limit",
    "too many requests",
    "model not found",
    "no such model",
    "does not exist",
    "断路器熔断",
    # 服务端暂时不可用（upstream 5xx）：立即换路由而非重试同一不可用上游。
    # 实证 2026-07-21：grok-4.5 503 upstream_unavailable 重试 3 次（~7s）才 failover，
    # 60 候选累积 503 触发 circuit_open 21 次。换路由比重试同一不可用上游更可能成功。
    "upstream_unavailable",
    "service temporarily unavailable",
    "bad gateway",
    "service unavailable",
)


def _route_endpoint(route) -> str:
    base_url = str(route.base_url or "").rstrip("/")
    return f"{base_url} [{route.name}/{route.route_id}]"


def _route_error_status(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status_code == 402 or "insufficient balance" in text or "quota exceeded" in text:
        return "insufficient_balance"
    if status_code in {401, 403} or any(marker in text for marker in ("invalid api key", "incorrect api key", "authentication", "unauthorized", "forbidden")):
        return "auth_error"
    if status_code == 429 or any(marker in text for marker in ("rate limit", "too many requests")):
        return "rate_limited"
    if status_code == 404 or any(marker in text for marker in ("model not found", "no such model", "does not exist")):
        return "model_unavailable"
    if "断路器熔断" in text:
        return "circuit_open"
    return "error"


def _should_failover_immediately(exc: Exception) -> bool:
    text = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    # 401/402/403/404/429：永久性或限流错误，重试当前路由无益，立即换路由。
    # 502/503/504：上游服务暂时不可用，重试同一上游不如换路由（实证 07-21 grok-4.5 503
    # 重试 3 次才 failover，累积触发熔断 21 次）。
    if status_code in {401, 402, 403, 404, 429, 502, 503, 504}:
        return True
    return any(marker in text for marker in _ROUTE_FAILOVER_ERROR_MARKERS)


def _extract_usage_tokens(resp) -> tuple[int | None, int | None, int | None]:
    """从 OpenAI 兼容响应抽取真实 token 用量（含 prompt cache 命中）。

    返回 (prompt_tokens, completion_tokens, cache_hit_tokens)。
    - DeepSeek / OpenAI 在 usage.prompt_tokens_details.cached_tokens 暴露 prompt cache 命中量
    - 部分兼容实现用 prompt_tokens_cache_hit 字段名，二者都兼容
    - 无 usage / 无 details 时返回 (None, None, None)，由调用方回退到 chars/1.8 估算
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None, None, None
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    cache_hit_tokens: int | None = None
    ptd = getattr(usage, "prompt_tokens_details", None)
    if ptd is not None:
        cache_hit_tokens = getattr(ptd, "cached_tokens", None) or getattr(ptd, "prompt_tokens_cache_hit", None)
    return prompt_tokens, completion_tokens, cache_hit_tokens


def _enrich_review_with_cost_and_rule(
    parsed: dict[str, Any],
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cache_hit_tokens: int | None,
    prompt_chars: int,
    completion_chars: int,
    rule_signal: dict[str, Any] | None,
    context_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """持久化前注入真实 token 用量 + 规则基线（additive，不改既有字段）。

    动机（T8）：llm_review_cache 此前只存 LLM review 输出，token 用量与
    规则基线（rule_score / llm_delta）全部丢失 → T8.2（各 skill token 消耗）
    与 T8.1（规则 vs LLM delta）无法跨 run 从缓存统计。本函数把这两项写入
    review 的私有键（下划线前缀），既不影响既有字段，也不 drain 全局
    _pending_api_costs（调用方的成本核算不受影响）。

    返回新 dict，不原地修改 parsed。
    """
    out = dict(parsed)
    out["_token_cost"] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "prompt_tokens_est": int(prompt_chars / 1.8),
        "completion_tokens_est": int(completion_chars / 1.8),
        "total_tokens_real": (prompt_tokens or 0) + (completion_tokens or 0),
        "total_cache_hit_tokens": cache_hit_tokens or 0,
        "prompt_cache_active": bool(cache_hit_tokens and cache_hit_tokens > 0),
    }
    if context_audit is not None:
        out["_prompt_context"] = dict(context_audit)
    if isinstance(rule_signal, dict):
        rs = rule_signal.get("dsa_signal_score")
        if isinstance(rs, (int, float)):
            try:
                score = float(parsed.get("score", 50))
                out["rule_score"] = float(rs)
                out["llm_delta"] = round(score - float(rs), 2)
            except (TypeError, ValueError):
                pass
    return out


def _append_api_cost(
    *,
    route,
    prompt_chars: int,
    completion_chars: int,
    latency_ms: float,
    status: str,
    error_message: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cache_hit_tokens: int | None = None,
    skill_id: str | None = None,
    context_audit: dict[str, Any] | None = None,
) -> None:
    # 估算 token 数：中文约 1.5-2 字符/token，英文约 4 字符/token
    # 混合文本取中间值 1.8 字符/token（仅在 provider 未返回真实用量时作为回退展示）
    prompt_tokens_est = int(prompt_chars / 1.8)
    completion_tokens_est = int(completion_chars / 1.8)
    _pending_api_costs.append({
        "skill_id": skill_id,
        "model": route.model if route is not None else "cache",
        "endpoint": _route_endpoint(route) if route is not None else "cache",
        "prompt_chars": prompt_chars,
        "completion_chars": completion_chars,
        "prompt_tokens_est": prompt_tokens_est,
        "completion_tokens_est": completion_tokens_est,
        # 真实用量（来自 provider usage 字段）；None 表示本次调用未取得真实值
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "latency_ms": round(latency_ms, 1),
        "status": status,
        "error_message": error_message,
        "context_audit": dict(context_audit) if context_audit is not None else None,
    })


def _load_cached_review(cache_key: str) -> dict[str, Any] | None:
    """查 LLM 审查结果缓存。命中返回 review dict，未命中或异常返回 None。"""
    try:
        from app.db.session import SessionLocal
        from app.services.llm_review_cache_service import get_cached_review
        with SessionLocal() as db:
            return get_cached_review(db, cache_key)
    except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
        logger.debug("_load_cached_review: best-effort fallback after %r", _ble_exc)
        return None


def _persist_review_cache(
    skill: StrategySkill,
    cache_key: str,
    code: str,
    trade_date: str | None,
    review: dict[str, Any],
    model: str,
) -> None:
    """写入 LLM 审查结果缓存。失败不影响主流程。"""
    try:
        from app.db.session import SessionLocal
        from app.services.llm_review_cache_service import write_cached_review
        with SessionLocal() as db:
            write_cached_review(
                db,
                cache_key=cache_key,
                skill_id=skill.skill_id,
                skill_hash=skill.skill_hash,
                trade_date=trade_date,
                code=code or None,
                review=review,
                model=model,
            )
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("_persist_review_cache: ignored non-fatal error: %s", exc)


_ACTION_ALIASES = {
    "观望": "观察",
    "观察": "观察",
    "轻仓": "轻仓试错",
    "轻仓试错": "轻仓试错",
    "试错": "轻仓试错",
    "确认参与": "确认后加仓",
    "确认后加仓": "确认后加仓",
    "加仓": "确认后加仓",
    "回避": "不做",
    "不做": "不做",
    "规避": "不做",
}


# ── 内部: 工具注册包装 ────────────────────────────────────────────────

def _register_tools(session_id: str) -> list:
    """为给定会话创建工具函数闭包（绑定 session_id）。"""
    tools_list = []

    # Agno tool 导入（带 fallback 以支持开发期）
    try:
        from agno.tools import tool
    except ImportError:
        # 开发期兼容
        def tool(func):
            return func

    @tool
    def _query_market_snapshot() -> dict:
        return query_market_snapshot.entrypoint(session_id)

    @tool
    def _query_stock_detail(code: str) -> dict:
        return query_stock_detail.entrypoint(code, session_id)

    @tool
    def _query_skill_signals(code: str, skill_id: str) -> dict:
        return query_skill_signals.entrypoint(code, skill_id, session_id)

    @tool
    def _audit_data_quality(code: str) -> dict:
        return audit_data_quality.entrypoint(code, session_id)

    @tool
    def _query_stock_history(code: str, days: int) -> dict:
        return query_stock_history.entrypoint(code, days, session_id)

    @tool
    def _query_dragon_tiger(code: str) -> dict:
        return query_dragon_tiger.entrypoint(code, session_id)

    @tool
    def _query_industry_peers(code: str) -> dict:
        return query_industry_peers.entrypoint(code, session_id)

    tools_list = [
        _query_market_snapshot,
        _query_stock_detail,
        _query_skill_signals,
        _audit_data_quality,
        _query_stock_history,
        _query_dragon_tiger,
        _query_industry_peers,
    ]
    return tools_list


# ── Skill Agent 创建 ──────────────────────────────────────────────────

def _build_system_prompt(skill: StrategySkill) -> str:
    """从 SKILL.md + references 构建 System Prompt。"""
    parts: list[str] = []

    # 主体: SKILL.md
    parts.append(f"""你是 A 股短线研究工作台里" {skill.name} "Skill 的 Agent。

下面是完整的 SKILL.md。你必须以它作为最高优先级的方法论、决策边界、工作流和表达约束。
你不是普通股票打分器；你要完全按照这个 Skill 的视角、框架和术语来审查候选股票。

""")
    parts.append(f"# SKILL.md — {skill.name}\n\n{skill.skill_body}")

    # 参考文献
    if skill.reference_files:
        parts.append("\n\n# 参考文献")
        for ref in skill.reference_files[:6]:
            ref_path = skill.path / str(ref.get("path", ""))
            if not ref_path.exists():
                continue
            try:
                text = ref_path.read_text(encoding="utf-8")
            except Exception as _ble_exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
                logger.debug("_build_system_prompt: best-effort fallback after %r", _ble_exc)
                continue
            # 限制每个参考文件 2000 字符
            if len(text) > 2000:
                text = text[:1920] + "\n... [内容已截断]"
            parts.append(f"\n## {ref['path']}\n{text}")

    # 工作流与输出要求
    parts.append(f"""
# Agent 工作流要求

1. 你必须把输入里的摘要视为线索，而不是最终事实。最终判断以 Tool 返回的数据为准。
2. 在形成结论前，至少按以下顺序完成一次最小审计：
   - `query_market_snapshot`：确认情绪周期、赚钱效应和主线环境；
   - `query_stock_detail(code)`：核对个股量价、均线、换手、资金与关键标签；
   - `audit_data_quality(code)`：确认缺口和可疑字段，禁止忽略数据缺口硬下结论；
   - `query_skill_signals(code, skill_id)`：核对该 Skill 的规则信号、phase、score、evidence；
   - 若 SKILL.md 明确依赖趋势、龙虎榜、板块对比，再补充调用 `query_stock_history`、`query_dragon_tiger`、`query_industry_peers`。
3. 任一必查 Tool 返回 error 时，必须写入 risks，并降低 score 与 confidence；不能用常识补全。
4. 先判断是否需要 veto 或明确回避，再做加分项判断。退潮、非主线、流动性不足时优先考虑否决。

# Agent 输出要求

1. 只使用输入里的事实、Tool 查询到的真实数据、和 Skill 内容。绝不编造实时数据。
2. 可以表达支持、反对或否决，但不要输出具体买入价格。
3. 如果 Skill 要求研究真实数据，而数据不足，必须写进 risks，并降低 score 和 confidence。
4. 特别审计数据质量：缺少新闻/公告/筹码/基本面时，不能以此作为支持证据。
5. 输出必须包含: stance, score(0-100), confidence(0-1), evidence, risks, summary, market_regime_judgment, action, position_range, entry_style, invalidation_conditions, next_day_plan
6. `summary` 必须明确动作倾向（如观察 / 轻仓试错 / 不做），并用 Skill 语言说明原因。
7. `action` 只能从 `观察`、`轻仓试错`、`确认后加仓`、`不做` 中选择。
8. `position_range` 和 `entry_style` **必须按 SKILL.md 中该策略定义的取值范围选择**，不要使用通用默认值。例如隔夜持股法的 entry_style 应从"尾盘竞价/隔夜持有/竞价承接确认"中选，而非通用的"分歧低吸/回封确认"。
""")

    return "\n\n".join(parts)


def _build_user_prompt(
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
    history: dict[str, Any] | None,
    skill_id: str,
    *,
    projection: PromptContextProjection | None = None,
) -> str:
    """构建 User Prompt。

    前缀布局优化：稳定的 market snapshot + rule signal 放最前面，
    变化的 stock 数据放最后。LLM prompt cache 前缀匹配生效。
    """
    context = projection or project_prompt_context(
        stock=stock,
        snapshot=snapshot,
        rule_signal=rule_signal,
        research=history,
        skill_id=skill_id,
    )
    parts: list[str] = []

    # ── 稳定前缀（同一 run 内共享）──
    parts.append(f"# 上下文策略\n{context.policy_id}\n")
    parts.append("# 市场快照\n")
    parts.append(canonical_section_json(context, "market_snapshot"))

    parts.append("\n\n# strategy.yaml 规则信号\n")
    parts.append(canonical_section_json(context, "rule_signal"))

    if context.section("research"):
        parts.append("\n\n# 历史参考\n")
        parts.append(canonical_section_json(context, "research"))

    code = str(context.section("stock").get("code") or "")

    parts.append(f"""

请严格按 SKILL.md 方法论审查以下股票。

先按下面的顺序调用 Tool，再给出结构化审查结果：

1. `query_market_snapshot()`
2. `query_stock_detail("{code}")`
3. `audit_data_quality("{code}")`
4. `query_skill_signals("{code}", "{skill_id}")`
5. 若前面仍不足，再补 `query_stock_history("{code}", 30)`、`query_industry_peers("{code}")`、`query_dragon_tiger("{code}")`

不要跳过前四步；不要只根据 prompt 里的摘要直接下结论。

---

# 候选股票数据（以下内容因股票不同而变化）
{canonical_section_json(context, "stock")}

输出 JSON 格式:
{{
  "stance": "strong_support|support|neutral_support|neutral|oppose|veto",
  "score": 0-100的整数,
  "confidence": 0-1的小数,
  "evidence": ["证据1", ...],
  "risks": ["风险1", ...],
  "summary": "一句中文审查结论",
  "market_regime_judgment": "Skill视角的市场状态判断",
  "action": "观察|轻仓试错|确认后加仓|不做",
  "position_range": "按 SKILL.md 定义（通常为 0%|0-5%|5-15%|15-30%）",
  "entry_style": "按 SKILL.md 定义（策略特定的入场方式）",
  "invalidation_conditions": ["失效条件1", ...],
  "next_day_plan": ["次日计划1", ...]
}}
""")

    return "\n".join(parts)


def _compact_stock(stock: dict[str, Any]) -> dict[str, Any]:
    """精简股票字段，只传关键信息给 Agent。

    关键补充：基础短线策略评分字段（support_ma5/support_ma10/dsa_support_score/
    trend_status/ma_alignment/macd_status/rsi_status）与共享短线因子必须传入，
    防止 Agent 在没有数据情况下凭空推断"MA5支撑确认"等结论。
    """
    keys = STOCK_FIELDS
    return {key: stock.get(key) for key in keys if key in stock}


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """精简市场快照。"""
    return semantic_market_snapshot(snapshot)


def _compact_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """精简规则信号。"""
    keys = RULE_SIGNAL_FIELDS
    return {key: signal.get(key) for key in keys if key in signal}


# ── 公用的 Skill Agent 创建函数 ───────────────────────────────────────

def create_skill_agent(skill: StrategySkill, session_id: str):
    """
    从 StrategySkill 创建 Agno Agent。

    Args:
        skill: 已加载的策略 Skill（含 SKILL.md + references）
        session_id: 会话 ID（用于 Tool 数据查询）

    Returns:
        Agno Agent 实例

    Extensibility:
        未来新增 Skill 只需在 skills/ 目录放入 SKILL.md + manifest.json，
        此函数自动读取并创建对应的 Agent，无需修改代码。
    """
    settings = get_settings()

    system_prompt = _build_system_prompt(skill)
    tools = _register_tools(session_id)

    try:
        from agno.agent import Agent
        from agno.models.openai import OpenAIChat
    except ImportError as e:
        raise RuntimeError(
            "Agno 框架未安装。请运行: pip install 'agno>=2.6.0'"
        ) from e

    # 路由表是唯一真相源：取优先级最高的有效路由（自带 key/base_url/model）。
    # 不再使用全局 env key 或 config 默认 key/url。
    routes = get_active_model_routes()
    if not routes:
        raise RuntimeError("未配置可用模型路由，无法创建 Skill Agent；请先在模型路由表为至少一条路由填写独立 API Key")
    route = routes[0]

    # 截断 system_prompt 到配置限制
    limit = max(4000, settings.agent_skill_prompt_char_limit)
    if len(system_prompt) > limit * 0.6:
        room = int(limit * 0.6)
        system_prompt = system_prompt[: room - 80] + "\n\n...[完整 SKILL.md 保存在本地 Skill 包中]"

    model = OpenAIChat(
        id=route.model,
        api_key=route.api_key,
        base_url=route.base_url,
        temperature=route.temperature if route.temperature is not None else settings.agent_temperature,
        max_tokens=route.max_tokens if route.max_tokens is not None else settings.agent_max_tokens,
        timeout=route.timeout_seconds if route.timeout_seconds is not None else settings.agent_skill_timeout_seconds,
    )

    agent = Agent(
        name=f"Skill-{skill.skill_id}",
        model=model,
        instructions=system_prompt,
        tools=tools,
        description=f"按 {skill.name} 方法论审查 A 股短线候选股票",
        markdown=False,
        debug_mode=False,
    )

    return agent


# ── Skill Agent 执行（带重试） ────────────────────────────────────────

async def run_skill_agent(
    agent,
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
    session_id: str,
    skill_id: str,
) -> SkillReviewOutput:
    """
    执行 Skill Agent 审查，内置重试 + 输出解析。

    Args:
        agent: Agno Agent 实例
        stock: 股票数据
        snapshot: 市场快照
        rule_signal: strategy.yaml 规则信号
        session_id: 会话 ID
        skill_id: 当前 Skill 标识，用于约束 Tool 调用顺序

    Returns:
        SkillReviewOutput 结构化审查结果
    """
    settings = get_settings()
    routes = get_active_model_routes()
    model_id = routes[0].model if routes else "no_active_route"
    max_retries = settings.agent_retry_count
    backoff = settings.agent_retry_backoff
    projection = project_prompt_context(
        stock=stock,
        snapshot=snapshot,
        rule_signal=rule_signal,
        research=None,
        skill_id=skill_id,
    )
    context_audit = projection.audit_summary()
    user_prompt = _build_user_prompt(
        stock,
        snapshot,
        rule_signal,
        None,
        skill_id,
        projection=projection,
    )
    prompt_chars = len(user_prompt)

    # 断路器检查 — 在进入重试循环前快速失败
    check_circuit(model_id)

    # 不传 history（Agent 应该通过 Tool 自己查询历史）
    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            # Agno Agent 调用
            with _metrics.timed("llm_latency", client="agno"):
                response = await agent.arun(input=user_prompt)

            # 尝试解析响应
            content = ""
            if hasattr(response, "content"):
                content = str(response.content)
            else:
                content = str(response)

            # 从响应中提取 JSON（可能被 markdown 包裹）
            parsed = _extract_json(content)

            # 校验并转换为 SkillReviewOutput
            output = _parse_to_output(parsed)

            # 成功：重置断路器计数器
            record_result(model_id, success=True)
            _append_api_cost(
                route=None,
                prompt_chars=prompt_chars,
                completion_chars=len(content),
                latency_ms=0.0,
                status="success",
                skill_id=skill_id,
                context_audit=context_audit,
            )
            return output

        except Exception as e:  # noqa: BLE001 - best-effort fallback, now debug-logged
            logger.debug("run_skill_agent: best-effort fallback after %r", e)
            last_error = str(e)
            # 按错误类型差异化冷却：402/auth/model 永久性错误长冷却，避免无效重试
            record_result(model_id, success=False, error_type=_route_error_status(e))
            if attempt < max_retries:
                wait = backoff ** attempt
                await asyncio.sleep(wait)
                continue

    # 所有重试耗尽
    raise RuntimeError(f"Skill Agent 审查失败 (已重试 {max_retries} 次): {last_error}")


def _extract_json(text: str) -> dict:
    """从 LLM 响应中提取 JSON（兼容 markdown 代码块包裹）。"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个平衡的 { ... } JSON 对象
    try:
        first_brace = text.find("{")
        if first_brace >= 0:
            depth = 0
            for i in range(first_brace, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[first_brace:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            pass
                        break
    except Exception as exc:  # noqa: BLE001 - intentionally non-blocking
        logger.warning("_extract_json: ignored non-fatal error: %s", exc)

    # 最后 fallback：贪婪匹配
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Markdown 格式 fallback：SKILL.md 指示 LLM 输出 ## stance: 看多 格式
    parsed = _parse_markdown_output(text)
    if parsed:
        return parsed

    raise ValueError(f"无法从 Agent 响应中提取 JSON: {text[:200]}...")


def _string_list(value: Any, limit: int = 8) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    return [str(item).strip() for item in items if str(item).strip()][:limit]


def _parse_markdown_output(text: str) -> dict | None:
    """从 markdown 格式的 Agent 响应中提取结构化数据。

    支持 SKILL.md 指示的格式：## stance: 看多 / ## score: 75
    """
    result: dict[str, Any] = {}

    # 提取 ## key: value 或 ## key：value 模式（值不含列表项前缀 - ）
    for match in re.finditer(r"##\s*(\w+)\s*[：:]\s*(.+?)(?:\n|$)", text):
        key = match.group(1).strip().lower()
        value = match.group(2).strip()
        if key in ("stance", "score", "confidence", "summary", "action",
                    "position_range", "entry_style", "market_regime_judgment"):
            # 对 summary 类字段，去掉列表前缀
            if key == "summary":
                value = re.sub(r"^-\s*", "", value)
            result[key] = value
        elif key in ("evidence", "risks", "invalidation_conditions", "next_day_plan"):
            # 提取列表项：包括 header 同行的列表项和后续列表项
            section_start = match.start(2)  # 从 value 开始位置
            section_end = text.find("\n##", match.end())
            if section_end < 0:
                section_end = len(text)
            section = text[section_start:section_end]
            items = [item.strip() for item in re.findall(r"[-*]\s*(.+)", section)]
            result[key] = items

    if not result:
        return None

    # 类型转换
    if "score" in result:
        try:
            result["score"] = int(result["score"])
        except (ValueError, TypeError):
            result["score"] = 50
    if "confidence" in result:
        try:
            result["confidence"] = float(result["confidence"])
        except (ValueError, TypeError):
            result["confidence"] = 0.5

    return result


def _normalize_action(raw_action: Any, stance: str, score: int, confidence: float) -> str:
    text = str(raw_action or "").strip()
    if text in _ACTION_ALIASES:
        action = _ACTION_ALIASES[text]
    elif "加仓" in text or "确认" in text:
        action = "确认后加仓"
    elif "轻仓" in text or "试错" in text:
        action = "轻仓试错"
    elif "观察" in text or "观望" in text:
        action = "观察"
    elif "不做" in text or "回避" in text or "规避" in text:
        action = "不做"
    # fallback by stance
    elif stance == "strong_support" and score >= 80 and confidence >= 0.7:
        action = "确认后加仓"
    elif stance in {"support", "neutral_support"} and score >= 60:
        action = "轻仓试错"
    elif stance in {"oppose", "veto"} or score <= 40:
        action = "不做"
    else:
        action = "观察"

    # stance 一致性校验：neutral/oppose/veto 不应有进攻性行动
    if stance == "neutral" and action in ("确认后加仓", "轻仓试错"):
        action = "观察"
    if stance in ("oppose", "veto") and action != "不做":
        action = "不做"
    return action


def _default_position_range(action: str, score: int, confidence: float) -> str:
    if action == "不做":
        return "0%"
    if action == "确认后加仓":
        return "30-50%" if score >= 85 and confidence >= 0.8 else "20-40%"
    if action == "轻仓试错":
        return "10-20%" if confidence >= 0.7 else "5-15%"
    return "0-5%"


def _default_entry_style(action: str) -> str:
    if action == "确认后加仓":
        return "分歧转一致后确认加仓"
    if action == "轻仓试错":
        return "分歧低吸或小仓跟随"
    if action == "不做":
        return "当前不设买点"
    return "先看竞价与承接，不抢一致"


def _default_invalidation_conditions(action: str, risks: list[str]) -> list[str]:
    if risks:
        return risks[:3]
    if action == "不做":
        return ["主线未强化前，不把观察升级成参与"]
    if action == "确认后加仓":
        return ["若主线分歧后无法转强，取消加仓计划", "若放量滞涨或承接转弱，回到观察"]
    if action == "轻仓试错":
        return ["若次日竞价转弱或承接不足，撤回试错", "若主线掉队，降级为观察"]
    return ["若主线和承接没有同步改善，继续观察"]


def _default_next_day_plan(action: str) -> list[str]:
    if action == "不做":
        return ["只跟踪主线强弱和龙头反馈，没有确认前不参与"]
    if action == "确认后加仓":
        return ["先看竞价是否强于板块平均，再等盘中确认加仓", "若高开低走失去承接，暂停加仓"]
    if action == "轻仓试错":
        return ["仅在分歧转强时小仓参与，不做一致追高", "若承接一般，继续等更清晰信号"]
    return ["先看竞价和量能，再决定是否升级为轻仓试错"]


def _parse_to_output(raw: dict) -> SkillReviewOutput:
    """将 LLM 返回的 dict 解析为 SkillReviewOutput。"""
    stance = str(raw.get("stance", "neutral"))
    valid_stances = {"strong_support", "support", "neutral_support", "neutral", "oppose", "veto"}
    if stance not in valid_stances:
        stance = "neutral"

    score = int(raw.get("score", 50))
    score = max(0, min(100, score))

    confidence = float(raw.get("confidence", 0.5))
    confidence = max(0.0, min(1.0, confidence))

    # 兼容 LLM 自然输出 "supports" 和标准字段名 "evidence"
    evidence = _string_list(raw.get("evidence") or raw.get("supports"), limit=8)
    risks = _string_list(raw.get("risks") or raw.get("risk_flags"), limit=8)
    regime = str(raw.get("market_regime_judgment", "")).strip()
    if not regime:
        # 兼容旧字段名
        regime = str(raw.get("market_regime", "")).strip()

    action = _normalize_action(raw.get("action"), stance, score, confidence)
    position_range = str(raw.get("position_range", "")).strip() or _default_position_range(action, score, confidence)
    entry_style = str(raw.get("entry_style", "")).strip() or _default_entry_style(action)
    invalidation_conditions = _string_list(raw.get("invalidation_conditions"), limit=5) or _default_invalidation_conditions(action, risks)
    next_day_plan = _string_list(raw.get("next_day_plan"), limit=5) or _default_next_day_plan(action)
    summary = str(raw.get("summary", "")).strip()
    if not summary:
        regime_clause = f"，当前更偏{regime}" if regime else ""
        summary = f"{action}{regime_clause}，按 Skill 纪律处理。"

    return SkillReviewOutput(
        stance=stance,
        score=score,
        confidence=confidence,
        evidence=evidence,
        risks=risks,
        summary=summary,
        market_regime_judgment=regime,
        action=action,
        position_range=position_range,
        entry_style=entry_style,
        invalidation_conditions=invalidation_conditions,
        next_day_plan=next_day_plan,
    )


# ── 快速通道：直接 OpenAI 兼容 API 调用（绕过 Agno 工具调用开销） ──

async def run_skill_agent_direct(
    skill: StrategySkill,
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
) -> SkillReviewOutput:
    """
    直接调用 DeepSeek API（单次请求，无工具调用），快速获取审查结果。

    绕过 Agno Agent 框架的工具调用开销（2-3 次 API 往返 → 1 次）。

    带跨 run 持久化结果缓存：相同输入（skill_hash + 股票特征 + 市场状态 + 规则信号）
    直接复用历史 LLM 输出，0 LLM 调用。skill 文件改动 / 市场状态变化 / 规则信号
    变化 → cache_key 自动失效。
    """
    from openai import AsyncOpenAI

    settings = get_settings()

    code = str(stock.get("code") or "")
    trade_date = str(snapshot.get("trade_date") or "") or None
    routes = get_active_model_routes()
    if not routes:
        raise RuntimeError("Skill Agent 未配置可用模型路由，请先在数据源页配置模型与 Key")

    projection = project_prompt_context(
        stock=stock,
        snapshot=snapshot,
        rule_signal=rule_signal,
        research=None,
        skill_id=skill.skill_id,
    )
    context_audit = projection.audit_summary()
    user_prompt = _build_direct_prompt(
        stock,
        snapshot,
        rule_signal,
        skill,
        projection=projection,
    )
    system_prompt = _build_direct_system(skill)
    prompt_chars = len(system_prompt) + len(user_prompt)
    max_retries = settings.agent_retry_count
    backoff = settings.agent_retry_backoff
    route_errors: list[str] = []

    for route in routes:
        cache_key = build_review_cache_key(
            skill_hash=skill.skill_hash,
            stock=stock,
            snapshot=snapshot,
            rule_signal=rule_signal,
            provider=route.provider,
            model=route.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_context_fingerprint=projection.projected_fingerprint,
        )
        cached_review = _load_cached_review(cache_key)
        if cached_review is not None:
            _append_api_cost(
                route=route,
                prompt_chars=0,
                completion_chars=0,
                latency_ms=0,
                status="cache_hit",
                error_message=None,
                skill_id=skill.skill_id,
            )
            return _parse_to_output(cached_review)
        route_key = route.circuit_key
        try:
            check_circuit(route_key)
        except Exception as exc:  # noqa: BLE001 - logged or recorded, non-blocking by design
            error_message = f"{route.name} ({route.model}) 熔断: {exc}"
            _append_api_cost(
                route=route,
                prompt_chars=prompt_chars,
                completion_chars=0,
                latency_ms=0,
                status="circuit_open",
                error_message=error_message,
                skill_id=skill.skill_id,
            )
            route_errors.append(error_message)
            continue

        last_error = ""
        last_status = "error"
        last_latency_ms = 0.0

        route_timeout = route.timeout_seconds if route.timeout_seconds is not None else settings.agent_skill_timeout_seconds
        async with AsyncOpenAI(
            api_key=route.api_key,
            base_url=route.base_url,
            timeout=route_timeout * 3,
            default_headers=OPENAI_CLIENT_HEADERS,
        ) as client:
            for attempt in range(max_retries + 1):
                t0 = asyncio.get_event_loop().time()
                try:
                    resp = await client.chat.completions.create(
                        model=route.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=route.temperature if route.temperature is not None else settings.agent_temperature,
                        max_tokens=route.max_tokens if route.max_tokens is not None else settings.agent_max_tokens,
                        response_format={"type": "json_object"},
                        extra_body={"prompt_cache_key": (skill.skill_hash or skill.skill_id)[:64]},
                    )
                    elapsed_ms = (asyncio.get_event_loop().time() - t0) * 1000
                    _metrics.observe("llm_latency", elapsed_ms / 1000, client="openai", model=route.model)
                    content = resp.choices[0].message.content or ""
                    parsed = json.loads(content)
                    result = _parse_to_output(parsed)
                    # 捕获真实 token 用量（含 prompt cache 命中），用于成本核算与 T8 prompt cache 验证。
                    # 此前 resp.usage 被丢弃，只能靠 chars/1.8 估算，无法验证 prompt_cache_key 是否生效。
                    _pt, _ct, _ch = _extract_usage_tokens(resp)
                    _append_api_cost(
                        route=route,
                        prompt_chars=prompt_chars,
                        completion_chars=len(content),
                        latency_ms=elapsed_ms,
                        status="success",
                        prompt_tokens=_pt,
                        completion_tokens=_ct,
                        cache_hit_tokens=_ch,
                        skill_id=skill.skill_id,
                    )
                    record_result(route_key, success=True)
                    # 持久化前注入真实 token 用量 + 规则基线（T8 跨 run 可追踪）
                    parsed = _enrich_review_with_cost_and_rule(
                        parsed, _pt, _ct, _ch, prompt_chars, len(content), rule_signal,
                        context_audit=context_audit,
                    )
                    _persist_review_cache(skill, cache_key, code, trade_date, parsed, route.model)
                    return result
                except Exception as exc:  # noqa: BLE001 - best-effort fallback, now debug-logged
                    logger.debug("run_skill_agent_direct: best-effort fallback after %r", exc)
                    last_latency_ms = (asyncio.get_event_loop().time() - t0) * 1000
                    last_error = str(exc)
                    last_status = _route_error_status(exc)
                    if _should_failover_immediately(exc):
                        break
                    if attempt < max_retries:
                        await asyncio.sleep(backoff ** attempt)
                        continue
                    break

        record_result(route_key, success=False, error_type=last_status)
        error_message = (
            f"{route.name} ({route.model}) 失败: {last_error}"
            if last_error
            else f"{route.name} ({route.model}) 未返回结果"
        )
        _append_api_cost(
            route=route,
            prompt_chars=prompt_chars,
            completion_chars=0,
            latency_ms=last_latency_ms,
            status=last_status,
            error_message=error_message,
            skill_id=skill.skill_id,
        )
        route_errors.append(error_message)

    tried_count = len(routes)
    tail = " | ".join(route_errors[-3:]) if route_errors else "无可用错误详情"
    raise RuntimeError(f"Skill Agent 直接审查失败：已尝试 {tried_count} 个模型路由；{tail}")


def load_cached_skill_review(
    *,
    skill: StrategySkill,
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
) -> SkillReviewOutput | None:
    """Probe semantic cache keys without consuming an upstream request permit."""
    routes = get_active_model_routes()
    if not routes:
        return None
    system_prompt = _build_direct_system(skill)
    projection = project_prompt_context(
        stock=stock,
        snapshot=snapshot,
        rule_signal=rule_signal,
        research=None,
        skill_id=skill.skill_id,
    )
    user_prompt = _build_direct_prompt(stock, snapshot, rule_signal, skill, projection=projection)
    for route in routes:
        cache_key = build_review_cache_key(
            skill_hash=skill.skill_hash,
            stock=stock,
            snapshot=snapshot,
            rule_signal=rule_signal,
            provider=route.provider,
            model=route.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_context_fingerprint=projection.projected_fingerprint,
        )
        cached_review = _load_cached_review(cache_key)
        if cached_review is None:
            continue
        _append_api_cost(
            route=route,
            prompt_chars=0,
            completion_chars=0,
            latency_ms=0,
            status="cache_hit",
            error_message=None,
            skill_id=skill.skill_id,
        )
        return _parse_to_output(cached_review)
    return None


# system prompt 进程级缓存：key=skill_hash，value=拼接好的 system prompt。
# skill 文件改动后 skill_hash 变化，缓存自动失效。避免同一 skill 在一次 run 内
# 对 N 只股票重复拼接 + 重复读参考文件。
_direct_system_cache: dict[str, str] = {}


def _build_direct_system(skill: StrategySkill) -> str:
    """构建精简版 System Prompt（不引导工具调用）。按 skill_hash 冻结。"""
    key = skill.skill_hash or skill.skill_id
    cached = _direct_system_cache.get(key)
    if cached is not None:
        return cached
    md_body = skill.skill_body.strip()
    if len(md_body) > 6000:
        md_body = md_body[:6000] + "\n\n... (SKILL.md 内容已按长度截断)"

    ref_text = ""
    for ref in skill.reference_files[:3]:
        ref_path = skill.path / ref["path"]
        if ref_path.exists() and ref_path.stat().st_size < 50000:
            content = ref_path.read_text(encoding="utf-8", errors="replace")[:3000]
            ref_text += f"\n\n## 参考: {ref['path']}\n{content}"

    result = f"""你是 A 股短线交易系统的 Skill Agent，严格按以下方法论审查股票。

{md_body}
{ref_text}

输出 JSON 格式审查结果，包含: stance, score(0-100), confidence(0-1), evidence, risks, summary, market_regime_judgment, action, position_range, entry_style, invalidation_conditions, next_day_plan。
不要编造数据，数据不足时降低 confidence 并在 risks 中注明。
技术面结论必须引用候选股票数据里的具体字段和值；如果 rsi_6/rsi_12/rsi_24、MACD 或均线字段已提供，不得声称这些技术指标缺失或只能推断。
`action` 只能从 `观察`、`轻仓试错`、`确认后加仓`、`不做` 中选择。"""
    _direct_system_cache[key] = result
    return result


def _build_direct_prompt(
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
    skill: StrategySkill,
    *,
    projection: PromptContextProjection | None = None,
) -> str:
    """构建一次性审查 Prompt（包含所有数据，无需工具查询）。

    前缀分层（DeepSeek prompt cache 是逐字节前缀匹配）：
      - 稳定前缀：market snapshot + 静态审查指引（同一 run 内所有股票共享）
      - 变化后缀：rule_signal + 候选股票数据（每只股票不同）

    关键：rule_signal 含 evidence/risk_flags 等 list，每只股票都不同，
    必须放在 stock 数据之前但仍属变化区，避免污染稳定前缀。所有 json.dumps
    用 sort_keys=True 保证 key 顺序稳定，避免 dict 插入顺序差异打断前缀。
    """
    context = projection or project_prompt_context(
        stock=stock,
        snapshot=snapshot,
        rule_signal=rule_signal,
        research=None,
        skill_id=skill.skill_id,
    )
    parts = []

    # ── 稳定前缀（同一 run 内所有股票共享，prompt cache 命中关键）──
    parts.append(f"# 上下文策略\n{context.policy_id}\n")
    parts.append("# 市场快照\n")
    parts.append(canonical_section_json(context, "market_snapshot"))

    parts.append(f"""

请严格按 {skill.name} 方法论审查以下股票。市场数据、规则评分均已提供——不需要额外查询。
所有趋势、均线、MACD、RSI、量能、乖离、支撑描述都必须落到候选股票数据中的字段和值。
如果字段存在，例如 rsi_12、macd_dif、ma5，不要写"未提供""只能推断"。

直接输出 JSON 审查结果（不要 markdown 包裹）：
{{"stance": "...", "score": 75, "confidence": 0.8, "evidence": [...], "risks": [...], "summary": "...", "market_regime_judgment": "...", "action": "轻仓试错", "position_range": "5-15%", "entry_style": "分歧低吸", "invalidation_conditions": [...], "next_day_plan": [...]}}

---

# strategy.yaml 规则评分（以下内容因股票不同而变化）
""")
    parts.append(canonical_section_json(context, "rule_signal"))

    parts.append("\n\n# 候选股票数据\n")
    parts.append(canonical_section_json(context, "stock"))

    return "\n\n".join(parts)

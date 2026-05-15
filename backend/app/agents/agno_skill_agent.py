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
            except Exception:
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
""")

    return "\n\n".join(parts)


def _build_user_prompt(
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
    history: dict[str, Any] | None,
    skill_id: str,
) -> str:
    """构建 User Prompt。"""
    parts: list[str] = []

    # 股票事实
    parts.append("# 候选股票\n")
    parts.append(json.dumps(_compact_stock(stock), ensure_ascii=False, indent=2))

    # 市场快照
    parts.append("\n\n# 市场快照\n")
    parts.append(json.dumps(_compact_snapshot(snapshot), ensure_ascii=False, indent=2))

    # 策略规则信号
    parts.append("\n\n# strategy.yaml 规则信号\n")
    if rule_signal:
        parts.append(json.dumps(_compact_signal(rule_signal), ensure_ascii=False, indent=2))
    else:
        parts.append('{"runtime": "agent_only", "note": "该 Skill 无可执行 strategy.yaml，仅 Agent 审查"}')

    # 历史复盘
    if history:
        parts.append("\n\n# 历史参考\n")
        parts.append(json.dumps(history, ensure_ascii=False, indent=2))

    code = str(stock.get("code") or "")

    parts.append(f"""

请严格按 SKILL.md 方法论审查以上股票。

先按下面的顺序调用 Tool，再给出结构化审查结果：

1. `query_market_snapshot()`
2. `query_stock_detail("{code}")`
3. `audit_data_quality("{code}")`
4. `query_skill_signals("{code}", "{skill_id}")`
5. 若前面仍不足，再补 `query_stock_history("{code}", 30)`、`query_industry_peers("{code}")`、`query_dragon_tiger("{code}")`

不要跳过前四步；不要只根据 prompt 里的摘要直接下结论。

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
  "position_range": "0%|0-5%|5-15%|20-40%",
  "entry_style": "分歧低吸|回封确认|突破跟随|当前不设买点",
  "invalidation_conditions": ["失效条件1", ...],
  "next_day_plan": ["次日计划1", ...]
}}
""")

    return "\n".join(parts)


def _compact_stock(stock: dict[str, Any]) -> dict[str, Any]:
    """精简股票字段，只传关键信息给 Agent。

    关键补充：DSA 评分字段（support_ma5/support_ma10/dsa_support_score/
    trend_status/ma_alignment/macd_status/rsi_status/buy_signal）必须传入，
    防止 Agent 在没有数据情况下凭空推断"MA5支撑确认"等结论。
    """
    keys = [
        "code", "name", "industry", "price", "pct",
        "turnover_rate", "amount_yi", "free_mv_yi",
        "dist20", "dist60", "ma5", "ma10", "ma20", "ma60",
        "vol5_20", "off_high_pct", "max_dd20",
        "upper_shadow_count20", "limit_up_days20",
        "primary_excess20", "primary_excess60",
        "industry_capacity_like_count", "industry_positive_excess20_count",
        "data_source", "quote_source", "kline_source",
        "latest_trade_date", "data_quality",
        "_is_main", "_is_leader_candidate", "_sentiment_phase",
        "_consensus_signal", "_dragon_tiger_dispersion", "_dragon_tiger_inst_ratio",
        "is_limit_up", "concept_sector_name", "concept_sector_heat_rank",
        "dragon_tiger_is_on_list", "dragon_tiger_recent_count",
        "main_net_inflow_yi", "main_net_inflow_ratio",
        "super_large_net_inflow_yi", "large_net_inflow_yi", "small_net_inflow_yi",
        "latest_news", "news_risk_count", "positive_catalyst_count",
        # DSA 评分关键字段（防止 Agent 幻觉）
        "support_ma5", "support_ma10", "dsa_support_score",
        "dsa_trend_score", "dsa_bias_score", "dsa_volume_score",
        "dsa_macd_score", "dsa_rsi_score", "dsa_signal_score",
        "trend_status", "ma_alignment",
        "macd_status", "macd_signal",
        "macd_dif", "macd_dea", "macd_bar",
        "rsi_6", "rsi_12", "rsi_24", "rsi_status", "rsi_signal",
        "buy_signal", "bias_ma5", "bias_ma10", "bias_ma20",
        "volume_status", "volume_trend", "volume_ratio_5d",
        "volume_ratio_vs_5d", "volume_ratio_vs_20d",
        "volume_breakout_signal", "volume_shrink_pullback",
        "pos20", "pos60", "pos120",
        "chg20", "chg60",
        "breakout20", "breakout60",
    ]
    return {key: stock.get(key) for key in keys if key in stock}


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """精简市场快照。"""
    keys = [
        "trade_date", "sample_count",
        "up_count", "down_count", "limit_up_count", "limit_down_count",
        "main_industries", "breadth",
    ]
    return {key: snapshot.get(key) for key in keys if key in snapshot}


def _compact_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """精简规则信号。"""
    keys = ["phase", "score", "setup_match", "evidence", "risk_flags",
            "score_cap", "runtime"]
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
        from agno.models.deepseek import DeepSeek
    except ImportError as e:
        raise RuntimeError(
            "Agno 框架未安装。请运行: pip install 'agno>=2.6.0'"
        ) from e

    # 截断 system_prompt 到配置限制
    limit = max(4000, settings.agent_skill_prompt_char_limit)
    if len(system_prompt) > limit * 0.6:
        room = int(limit * 0.6)
        system_prompt = system_prompt[: room - 80] + "\n\n...[完整 SKILL.md 保存在本地 Skill 包中]"

    model = DeepSeek(
        id=settings.agent_model,
        api_key=settings.agent_skill_api_key,
        temperature=settings.agent_temperature,
        max_tokens=settings.agent_max_tokens,
        timeout=settings.agent_skill_timeout_seconds,
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
    model_id = settings.agent_model
    max_retries = settings.agent_retry_count
    backoff = settings.agent_retry_backoff
    user_prompt = _build_user_prompt(stock, snapshot, rule_signal, None, skill_id)

    # 断路器检查 — 在进入重试循环前快速失败
    check_circuit(model_id)

    # 不传 history（Agent 应该通过 Tool 自己查询历史）
    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            # Agno Agent 调用
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
            return output

        except Exception as e:
            last_error = str(e)
            record_result(model_id, success=False)
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
    except Exception:
        pass

    # 最后 fallback：贪婪匹配
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

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


def _normalize_action(raw_action: Any, stance: str, score: int, confidence: float) -> str:
    text = str(raw_action or "").strip()
    if text in _ACTION_ALIASES:
        return _ACTION_ALIASES[text]
    if "加仓" in text or "确认" in text:
        return "确认后加仓"
    if "轻仓" in text or "试错" in text:
        return "轻仓试错"
    if "观察" in text or "观望" in text:
        return "观察"
    if "不做" in text or "回避" in text or "规避" in text:
        return "不做"

    if stance == "strong_support" and score >= 80 and confidence >= 0.7:
        return "确认后加仓"
    if stance in {"support", "neutral_support"} and score >= 60:
        return "轻仓试错"
    if stance in {"oppose", "veto"} or score <= 40:
        return "不做"
    return "观察"


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

    evidence = _string_list(raw.get("evidence"), limit=8)
    risks = _string_list(raw.get("risks"), limit=8)
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
    """
    from openai import AsyncOpenAI

    settings = get_settings()
    model_id = settings.agent_model

    # 熔断器检查：连续失败过多则直接拒绝，避免雪崩
    check_circuit(model_id)

    user_prompt = _build_direct_prompt(stock, snapshot, rule_signal, skill)
    system_prompt = _build_direct_system(skill)

    async with AsyncOpenAI(
        api_key=settings.agent_skill_api_key,
        base_url=settings.agent_skill_base_url,
        timeout=settings.agent_skill_timeout_seconds * 3,
    ) as client:
        max_retries = settings.agent_retry_count
        backoff = settings.agent_retry_backoff

        last_error = ""
        for attempt in range(max_retries + 1):
            t0 = asyncio.get_event_loop().time()
            try:
                resp = await client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=settings.agent_temperature,
                    max_tokens=settings.agent_max_tokens,
                    response_format={"type": "json_object"},
                )
                elapsed_ms = (asyncio.get_event_loop().time() - t0) * 1000
                content = resp.choices[0].message.content or ""
                parsed = json.loads(content)
                result = _parse_to_output(parsed)
                _pending_api_costs.append({
                    "model": model_id,
                    "endpoint": settings.agent_skill_base_url,
                    "prompt_chars": len(system_prompt) + len(user_prompt),
                    "completion_chars": len(content),
                    "latency_ms": round(elapsed_ms, 1),
                    "status": "success",
                })
                record_result(model_id, success=True)
                return result
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    await asyncio.sleep(backoff ** attempt)
                continue

    # 最后尝试也失败了，记录错误成本和熔断器状态
    record_result(model_id, success=False)
    _pending_api_costs.append({
        "model": model_id,
        "endpoint": settings.agent_skill_base_url,
        "prompt_chars": len(system_prompt) + len(user_prompt),
        "completion_chars": 0,
        "latency_ms": 0,
        "status": "error",
        "error_message": last_error,
    })
    raise RuntimeError(f"Skill Agent 直接审查失败 (已重试 {max_retries} 次): {last_error}")


def _build_direct_system(skill: StrategySkill) -> str:
    """构建精简版 System Prompt（不引导工具调用）。"""
    md_body = skill.skill_body.strip()
    # 限制长度避免超时
    if len(md_body) > 6000:
        md_body = md_body[:6000] + "\n\n... (SKILL.md 内容已按长度截断)"

    ref_text = ""
    for ref in skill.reference_files[:3]:
        ref_path = skill.path / ref["path"]
        if ref_path.exists() and ref_path.stat().st_size < 50000:
            content = ref_path.read_text(encoding="utf-8", errors="replace")[:3000]
            ref_text += f"\n\n## 参考: {ref['path']}\n{content}"

    return f"""你是 A 股短线交易系统的 Skill Agent，严格按以下方法论审查股票。

{md_body}
{ref_text}

输出 JSON 格式审查结果，包含: stance, score(0-100), confidence(0-1), evidence, risks, summary, market_regime_judgment, action, position_range, entry_style, invalidation_conditions, next_day_plan。
不要编造数据，数据不足时降低 confidence 并在 risks 中注明。
技术面结论必须引用候选股票数据里的具体字段和值；如果 rsi_6/rsi_12/rsi_24、MACD 或均线字段已提供，不得声称这些技术指标缺失或只能推断。
`action` 只能从 `观察`、`轻仓试错`、`确认后加仓`、`不做` 中选择。"""


def _build_direct_prompt(
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
    skill: StrategySkill,
) -> str:
    """构建一次性审查 Prompt（包含所有数据，无需工具查询）。"""
    parts = []

    parts.append("# 候选股票数据\n")
    parts.append(json.dumps(_compact_stock(stock), ensure_ascii=False, indent=2))

    parts.append("\n\n# 市场快照\n")
    parts.append(json.dumps(_compact_snapshot(snapshot), ensure_ascii=False, indent=2))

    parts.append("\n\n# strategy.yaml 规则评分\n")
    if rule_signal:
        parts.append(json.dumps(_compact_signal(rule_signal), ensure_ascii=False, indent=2))
    else:
        parts.append('{"runtime": "agent_only"}')

    parts.append(f"""

请严格按 {skill.name} 方法论审查以上股票。市场数据、股票特征、规则评分均已提供——不需要额外查询。
所有趋势、均线、MACD、RSI、量能、乖离、支撑描述都必须落到候选股票数据中的字段和值。
如果字段存在，例如 rsi_12、macd_dif、ma5，不要写“未提供”“只能推断”。

直接输出 JSON 审查结果（不要 markdown 包裹）：
{{"stance": "...", "score": 75, "confidence": 0.8, "evidence": [...], "risks": [...], "summary": "...", "market_regime_judgment": "...", "action": "轻仓试错", "position_range": "5-15%", "entry_style": "分歧低吸", "invalidation_conditions": [...], "next_day_plan": [...]}}
""")
    return "\n\n".join(parts)

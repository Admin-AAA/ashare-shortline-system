"""
Agent 输出可追溯性验证。

验证 Skill Agent 的审查输出是否基于输入数据，而不是凭空编造。
不依赖任何 LLM——纯代码层规则检查。

设计原则:
  - 只标记可疑内容，不做自动修正
  - 发现的问题写入 hallucination_flags，供上层调低置信度或拒收
  - 新 Skill 自动适用（基于通用字段匹配）
"""

from __future__ import annotations

from typing import Any


def verify_review_output(
    review: dict[str, Any],
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    验证 Agent 审查输出是否可追溯到输入数据。

    Args:
        review: Agent 输出的 SkillReviewOutput (dict 形式)
        stock: 传入的股票数据
        snapshot: 传入的市场快照
        rule_signal: 传入的规则信号

    Returns:
        {
            "flags": [...],          # 可疑标记列表
            "severity": "low|medium|high",
            "verified_evidence": int,  # 可追溯的证据数
            "total_evidence": int,     # 总证据数
            "verified_risks": int,     # 可追溯的风险数
            "total_risks": int,        # 总风险数
            "adjustment_advice": str,  # 调整建议
        }
    """
    flags: list[str] = []
    severity = "low"

    # ── 构建可追溯字段集合 ──
    traceable_values = _build_traceable_set(stock)

    # ── 检查 evidence ──
    evidence_list = review.get("evidence", [])
    verified_evidence = 0
    for item in evidence_list:
        item_str = str(item).strip()
        if not item_str:
            continue
        if _is_traceable(item_str, traceable_values, stock, snapshot, rule_signal):
            verified_evidence += 1
        else:
            flags.append(f"证据无法追溯: {item_str[:80]}")

    # ── 检查 risks ──
    risks_list = review.get("risks", [])
    verified_risks = 0
    for item in risks_list:
        item_str = str(item).strip()
        if not item_str:
            continue
        if _is_traceable(item_str, traceable_values, stock, snapshot, rule_signal):
            verified_risks += 1
        else:
            flags.append(f"风险无法追溯: {item_str[:80]}")

    # ── 检查特定幻觉模式 ──

    # 1. 编造新闻事件
    news_keywords = ["公告", "新闻", "披露", "发布", "宣布", "年报", "季报", "业绩预告", "重组", "停牌"]
    for item in evidence_list + risks_list:
        item_str = str(item).strip()
        for kw in news_keywords:
            if kw in item_str:
                # 检查是否在规则信号的 evidence/risk_flags 中
                if not _in_rule_signal(item_str, rule_signal):
                    flags.append(f"疑似编造新闻: {item_str[:100]}")
                    severity = _raise_severity(severity, "medium")
                    break

    # 2. 编造具体数值（不在输入数据中）
    numeric_match = _find_unverified_numbers(evidence_list + risks_list, stock)
    for match in numeric_match:
        flags.append(f"数值无法追溯: {match}")
        severity = _raise_severity(severity, "low")

    # 3. 提到不在候选中的其他股票
    other_symbols = _find_external_symbols(evidence_list + risks_list, stock.get("code", ""))
    for symbol in other_symbols:
        flags.append(f"引用外部股票: {symbol}")

    # 4. 市场状态判断与快照矛盾
    regime = review.get("market_regime_judgment", "")
    if regime:
        breadth = snapshot.get("breadth", {})
        snapshot_regime = breadth.get("regime", "") if isinstance(breadth, dict) else ""
        # 如果 Agent 说"主升加速"但快照显示"退潮"，标记矛盾
        if snapshot_regime and _regime_conflict(regime, snapshot_regime):
            flags.append(f"市场状态判断与快照矛盾: Agent={regime}, Snapshot={snapshot_regime}")
            severity = _raise_severity(severity, "high")

    # ── 计算可追溯率 ──
    total_evidence = len(evidence_list)
    total_risks = len(risks_list)

    # 调整建议
    if severity == "high":
        adjustment = "Agent 输出存在严重不可追溯内容，建议大幅降低置信度或拒收此审查结果"
    elif severity == "medium":
        adjustment = "Agent 输出部分内容无法追溯，建议降低 confidence 0.1-0.2"
    elif flags:
        adjustment = "Agent 输出存在少量无法追溯内容，可正常使用但需留意"
    else:
        adjustment = "Agent 输出全部可追溯，质量良好"

    return {
        "flags": flags,
        "severity": severity,
        "verified_evidence": verified_evidence,
        "total_evidence": total_evidence,
        "verified_risks": verified_risks,
        "total_risks": total_risks,
        "adjustment_advice": adjustment,
    }


def _build_traceable_set(stock: dict[str, Any]) -> set[str]:
    """从股票数据中构建可追溯的字符串集合。"""
    values: set[str] = set()

    def _add(val: Any) -> None:
        if val is None:
            return
        s = str(val).strip()
        if s and len(s) > 1:
            values.add(s)
            # 也加入去掉负号的版本
            if s.startswith("-"):
                values.add(s[1:])

    # 基本字段
    for key in ["code", "name", "industry"]:
        _add(stock.get(key))

    # 数值字段（保留 2 位小数版本）
    for key in [
        "price", "pct", "turnover_rate", "amount_yi", "free_mv_yi",
        "dist20", "dist60", "ma5", "ma10", "ma20", "ma60",
        "vol5_20", "off_high_pct", "max_dd20",
    ]:
        val = stock.get(key)
        if val is not None:
            try:
                s = f"{float(val):.2f}"
                values.add(s)
                s1 = f"{float(val):.1f}"
                values.add(s1)
            except (TypeError, ValueError):
                _add(val)

    # 整数/计数字段
    for key in ["upper_shadow_count20", "limit_up_days20", "sample_count"]:
        val = stock.get(key)
        if val is not None:
            _add(str(val))

    # data_quality
    dq = stock.get("data_quality")
    if isinstance(dq, dict):
        for k, v in dq.items():
            _add(str(v))

    return values


def _is_traceable(
    statement: str,
    traceable_values: set[str],
    stock: dict[str, Any],
    snapshot: dict[str, Any],
    rule_signal: dict[str, Any] | None,
) -> bool:
    """检查一个证据/风险声明是否可追溯到输入数据。"""

    # 1. 精确子串匹配
    for tv in traceable_values:
        if tv in statement and len(tv) >= 3:
            return True

    # 2. 行业名匹配
    industry = str(stock.get("industry", ""))
    if industry and industry in statement:
        return True

    # 3. 股票名/代码匹配
    name = str(stock.get("name", ""))
    code = str(stock.get("code", ""))
    if name and name in statement:
        return True
    if code and code in statement:
        return True

    # 4. 通用技术术语（放行，这些不需要追溯到具体数值）
    generic_terms = [
        "均线", "多头", "空头", "金叉", "死叉", "MACD", "RSI", "KDJ",
        "量能", "缩量", "放量", "换手", "乖离", "回撤", "支撑", "压力",
        "上影线", "下影线", "突破", "跌破", "反弹", "回调",
        "龙头", "主线", "情绪", "分歧", "一致", "筹码", "合力",
        "涨停", "跌停", "高开", "低开", "资金", "流入", "流出",
        "风险", "仓位", "止损", "止盈",
    ]
    for term in generic_terms:
        if term in statement:
            return True

    # 5. 在规则信号的 evidence/risk_flags 中
    if _in_rule_signal(statement, rule_signal):
        return True

    # 默认不可追溯
    return False


def _in_rule_signal(statement: str, rule_signal: dict[str, Any] | None) -> bool:
    """检查声明是否出现在规则信号中。"""
    if rule_signal is None:
        return False
    for field in ["evidence", "risk_flags", "phase", "setup_match", "bucket_preference"]:
        value = rule_signal.get(field)
        if isinstance(value, str) and len(value) >= 3 and value in statement:
            return True
        if isinstance(value, list):
            for item in value:
                item_str = str(item).strip()
                if len(item_str) >= 3 and item_str in statement:
                    return True
    return False


def _find_unverified_numbers(
    statements: list[str],
    stock: dict[str, Any],
) -> list[str]:
    """查找声明中无法追溯到股票数据的数值。"""
    import re as _re

    unverified: list[str] = []
    traceable = _build_traceable_set(stock)

    for stmt in statements:
        # 找浮点数（如 3.5%、12.8亿）
        numbers = _re.findall(r"\d+\.?\d*", str(stmt))
        for num in numbers:
            num_str = num.strip()
            if len(num_str) >= 2 and any(c.isdigit() for c in num_str):
                # 检查是否存在于 traceable 集合
                found = False
                for tv in traceable:
                    if num_str in tv or tv in num_str:
                        found = True
                        break
                if not found:
                    unverified.append(f"'{num_str}' in '{str(stmt)[:60]}'")

    return unverified[:5]  # 最多 5 条


def _find_external_symbols(
    statements: list[str],
    current_code: str,
) -> list[str]:
    """查找声明中引用非本股票的其他股票代码。"""
    import re as _re

    external: list[str] = []
    # A 股代码模式 (6 位数字)
    pattern = _re.compile(r"\b(\d{6})\b")
    for stmt in statements:
        for match in pattern.finditer(str(stmt)):
            code = match.group(1)
            if code != current_code and code[:1] in {"0", "3", "6"}:
                external.append(code)
    return list(dict.fromkeys(external))[:3]


def _regime_conflict(agent_regime: str, snapshot_regime: str) -> bool:
    """检测 Agent 市场状态与快照的严重矛盾。"""
    bullish = {"主升加速", "加速初期", "一致加速", "启动", "趋势跟踪"}
    bearish = {"退潮", "分歧", "明确转弱", "风险否决"}

    agent_lower = agent_regime.lower()
    snap_lower = snapshot_regime.lower()

    # 检查明显矛盾
    if any(b in agent_lower for b in ["主升", "加速", "启动"]) and any(b in snap_lower for b in ["退潮", "ma20偏下", "空头"]):
        return True
    if any(b in agent_lower for b in ["退潮", "转弱"]) and any(b in snap_lower for b in ["主升", "加速", "ma20偏上"]):
        return True
    return False


def _raise_severity(current: str, new: str) -> str:
    """升级严重级别。"""
    rank = {"low": 0, "medium": 1, "high": 2}
    if rank.get(new, 0) > rank.get(current, 0):
        return new
    return current

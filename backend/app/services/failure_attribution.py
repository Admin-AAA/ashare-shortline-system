from __future__ import annotations

from typing import Any


def classify_false_positive(
    *,
    reason: Any = None,
    executed: bool | None = None,
    outcome_label: Any = None,
    exit_reason: Any = None,
    user_label: Any = None,
    net_return_pct: float | None = None,
) -> dict[str, str]:
    """把误判原因归一到稳定的类别，便于聚合和筛选。"""
    text = _combined_text(reason, outcome_label, exit_reason, user_label)

    if _contains(text, "信号当时真实不成立", "信号不成立", "信号失真", "setup_invalid", "signal_invalid"):
        return _item("signal_invalid", "信号当时不成立", "signal")
    if _contains(text, "未触发", "未建仓", "未命中买点", "not_triggered", "entry_not"):
        return _item("entry_not_triggered", "未触发买点", "entry")
    if _contains(text, "一字涨停", "买不进", "无流动性", "无成交", "停牌", "不可真实成交", "无法真实成交", "limit_up", "no_liquidity"):
        return _item("entry_execution_blocked", "入场不可成交", "entry")
    if executed is False and not text:
        return _item("entry_not_triggered", "未触发买点", "entry")
    if _contains(text, "跌停锁死", "一字跌停", "卖不出", "无法卖出", "limit_down"):
        return _item("exit_execution_blocked", "出场不可成交", "exit")
    if _contains(text, "成本后", "净收益不为正", "扣费", "滑点"):
        return _item("cost_drag", "成本滑点吞噬", "post_trade")
    if _contains(text, "大盘", "市场", "情绪", "分歧", "指数", "板块"):
        return _item("market_context", "市场环境拖累", "context")
    if _contains(text, "承接", "走弱", "强转弱", "冲高回落", "开盘"):
        return _item("weak_followthrough", "承接转弱", "signal")
    if _contains(text, "止损", "跌破", "stop_loss"):
        return _item("stop_loss", "止损退出", "exit")
    if str(user_label or "").strip() == "false_positive":
        return _item("manual_review", "人工复盘标记", "manual")
    if net_return_pct is not None and net_return_pct <= 0:
        return _item("negative_return", "收益未转正", "post_trade")
    return _item("other", "其他误判", "other")


def _combined_text(*values: Any) -> str:
    return " ".join(str(value or "").strip().lower() for value in values if value is not None)


def _contains(text: str, *needles: str) -> bool:
    return any(needle.lower() in text for needle in needles)


def _item(category: str, label: str, stage: str) -> dict[str, str]:
    return {
        "category": category,
        "category_label": label,
        "stage": stage,
    }

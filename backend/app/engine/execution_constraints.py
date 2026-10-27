"""成交可执行性约束：判断某一日的推荐 / bar 是否「买得进 / 卖得出」。

本模块聚焦**成交语义**（流动性缺失、入场 / 出场被一字板挡住等），而把纯粹的**涨跌停 /
板位规则**下沉到唯一深模块 `app.engine.board_rules`（一字涨停 / 跌停判定、涨跌停价、板位
幅度）。因此这里通过 import 复用 board_rules，不再各自维护涨跌停副本。
"""
from __future__ import annotations

from typing import Any

from app.engine.board_rules import (
    is_one_word_limit_down_bar,
    is_one_word_limit_up_bar,
)


def entry_no_liquidity_reason_from_card(card: dict[str, Any]) -> str | None:
    price_present = any(_present(card, key) for key in ("recommend_price", "price", "close"))
    price = _as_float(card.get("recommend_price") or card.get("price") or card.get("close"))
    if price_present and price <= 0:
        return "入场日价格无效，无法真实成交"

    amount_present = _present(card, "amount_yi")
    volume_present = _present(card, "volume")
    if amount_present and volume_present and _as_float(card.get("amount_yi")) <= 0 and _as_float(card.get("volume")) <= 0:
        return "入场日无成交量或成交额，无法真实成交"
    return None


def entry_execution_block_reason_from_bar(code: str, bar: Any, *, is_st: bool = False) -> str | None:
    if no_liquidity_reason_from_bar(bar, side="entry"):
        return "入场日无成交量或价格无效，无法真实成交"
    if is_one_word_limit_up_bar(code, bar, is_st=is_st):
        return "入场日一字涨停，按真实成交约束视为买不进"
    return None


def exit_execution_block_reason_from_bar(code: str, bar: Any) -> str | None:
    if no_liquidity_reason_from_bar(bar, side="exit"):
        return "出场日无成交量或价格无效，无法真实卖出"
    if is_one_word_limit_down_bar(code, bar):
        return "出场日一字跌停，按真实成交约束视为卖不出"
    return None


def no_liquidity_reason_from_bar(bar: Any, *, side: str) -> str | None:
    volume = _as_float(getattr(bar, "volume", 0))
    amount = _as_float(getattr(bar, "amount", 0))
    close = _as_float(getattr(bar, "close", 0))
    open_price = _as_float(getattr(bar, "open", 0))
    if volume > 0 and amount > 0 and close > 0 and open_price > 0:
        return None
    if side == "exit":
        return "出场日无成交量或价格无效，无法真实卖出"
    return "入场日无成交量或价格无效，无法真实成交"


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _present(row: dict[str, Any], key: str) -> bool:
    return key in row and row.get(key) not in (None, "")

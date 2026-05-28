from __future__ import annotations

from typing import Any


def limit_pct_for_code(code: str) -> float:
    normalized = str(code or "")
    if normalized.startswith(("300", "301", "688")):
        return 20.0
    if normalized.startswith(("8", "4")):
        return 30.0
    return 10.0


def limit_price(prev_close: float, pct: float, *, up: bool) -> float:
    multiplier = 1 + pct / 100.0 if up else 1 - pct / 100.0
    return round(prev_close * multiplier, 2)


def previous_close_from_price(close: Any, pct_chg: Any) -> float:
    close_value = _as_float(close)
    pct_value = _as_float(pct_chg)
    if close_value <= 0 or pct_value <= -99.0:
        return 0.0
    return close_value / (1 + pct_value / 100.0)


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


def is_one_word_limit_up_card(card: dict[str, Any]) -> bool:
    if not bool(card.get("is_limit_up")):
        return False
    if not all(_present(card, key) for key in ("open", "high", "low", "close", "pct")):
        return False
    prev_close = previous_close_from_price(card.get("close") or card.get("price") or card.get("recommend_price"), card.get("pct"))
    return is_one_word_limit_up_values(
        str(card.get("code") or ""),
        prev_close=prev_close,
        open_price=card.get("open"),
        high=card.get("high"),
        low=card.get("low"),
        close=card.get("close"),
    )


def entry_execution_block_reason_from_bar(code: str, bar: Any) -> str | None:
    if no_liquidity_reason_from_bar(bar, side="entry"):
        return "入场日无成交量或价格无效，无法真实成交"
    if is_one_word_limit_up_bar(code, bar):
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


def is_one_word_limit_up_bar(code: str, bar: Any) -> bool:
    prev_close = previous_close_from_price(getattr(bar, "close", 0), getattr(bar, "pct_chg", 0))
    return is_one_word_limit_up_values(
        code,
        prev_close=prev_close,
        open_price=getattr(bar, "open", 0),
        high=getattr(bar, "high", 0),
        low=getattr(bar, "low", 0),
        close=getattr(bar, "close", 0),
    )


def is_one_word_limit_down_bar(code: str, bar: Any) -> bool:
    prev_close = previous_close_from_price(getattr(bar, "close", 0), getattr(bar, "pct_chg", 0))
    return is_one_word_limit_down_values(
        code,
        prev_close=prev_close,
        open_price=getattr(bar, "open", 0),
        high=getattr(bar, "high", 0),
        low=getattr(bar, "low", 0),
        close=getattr(bar, "close", 0),
    )


def is_one_word_limit_up_values(
    code: str,
    *,
    prev_close: float,
    open_price: Any,
    high: Any,
    low: Any,
    close: Any,
) -> bool:
    if prev_close <= 0:
        return False
    up_limit = limit_price(prev_close, limit_pct_for_code(code), up=True)
    return (
        _as_float(open_price) >= up_limit * 0.995
        and _as_float(low) >= up_limit * 0.995
        and _as_float(high) >= up_limit * 0.995
        and _as_float(close) >= up_limit * 0.995
    )


def is_one_word_limit_down_values(
    code: str,
    *,
    prev_close: float,
    open_price: Any,
    high: Any,
    low: Any,
    close: Any,
) -> bool:
    if prev_close <= 0:
        return False
    down_limit = limit_price(prev_close, limit_pct_for_code(code), up=False)
    return (
        _as_float(open_price) <= down_limit * 1.005
        and _as_float(high) <= down_limit * 1.005
        and _as_float(low) <= down_limit * 1.005
        and _as_float(close) <= down_limit * 1.005
    )


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _present(row: dict[str, Any], key: str) -> bool:
    return key in row and row.get(key) not in (None, "")

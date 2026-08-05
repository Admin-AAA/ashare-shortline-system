"""板位与涨跌停规则：把 A 股涨跌停价、板位（主板 / 创业 / 科创 / 北交）识别与一字板
判定收拢为唯一深模块。

此前这些规则散落多处且已漂移：
- `services/execution_constraints.py` 持有**代码感知**（按板位取 10% / 20% / 30% 涨跌停
  幅度）的一字涨停 / 一字跌停判定（open / low / high / close 四点齐平），服务于「推荐成交
  约束」。
- `services/overnight_holding_service.py` 另有一份 `_is_one_word_limit_up(bar, up_limit)`：
  涨停价由调用方给定（`_main_board_up_limit(前一日实际收盘)`，**主板固定 10%**），且**只看
  open / low 两点**，服务于「隔夜持有扫描」。
- `services/shortline_factors.py` 的 `_is_twenty_cm_stock` 负责 20cm（创业 / 科创 / 北交）
  识别。

两处一字板判定**语义确实不同**（涨停价来源、板位幅度、齐平点数三者都不同），故这里以
**两个清晰命名的函数**并存、各自行为原样保留，而不强行合并（合并必然改变行为）：
- `is_one_word_limit_up_bar(code, bar)`：代码感知、按 pct_chg 反推前收、四点齐平（推荐成交
  约束口径）。
- `is_one_word_limit_up_open_low(bar, up_limit)`：调用方给定涨停价、仅看 open / low 两点
  （隔夜持有扫描口径）。

- 纯计算，无数据库 / 网络 / Tushare 依赖，可 in-process 单测。
- 行为与原各副本逐字一致（重构基线：相同输入相同输出）。
"""
from __future__ import annotations

from typing import Any

from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY

__all__ = [
    "MAIN_BOARD_LIMIT_UP_PCT",
    "limit_pct_for_code",
    "limit_pct_for_stock",
    "is_price_limit_move",
    "limit_price",
    "previous_close_from_price",
    "main_board_up_limit",
    "is_one_word_limit_up_values",
    "is_one_word_limit_down_values",
    "is_one_word_limit_up_bar",
    "is_one_word_limit_down_bar",
    "is_one_word_limit_up_card",
    "is_one_word_limit_up_open_low",
    "is_twenty_cm_stock",
]

_BOARD_LIMITS = DEFAULT_EXECUTION_POLICY.board_limits
_LIMIT_TOLERANCE = _BOARD_LIMITS.limit_detection_tolerance_ratio
MAIN_BOARD_LIMIT_UP_PCT = _BOARD_LIMITS.main_board_pct / 100.0


def limit_pct_for_code(code: str) -> float:
    """按证券代码前缀返回 canonical policy 的板位涨跌停幅度。"""
    normalized = str(code or "")
    if normalized.startswith(("300", "301")):
        return _BOARD_LIMITS.growth_board_pct
    if normalized.startswith(("688", "689")):
        return _BOARD_LIMITS.star_market_pct
    if normalized.startswith(("8", "4")):
        return _BOARD_LIMITS.beijing_exchange_pct
    return _BOARD_LIMITS.main_board_pct


def limit_pct_for_stock(stock: dict[str, Any]) -> float:
    """按股票完整身份返回涨跌停幅度；ST 优先，其次北交所和代码板位。"""
    name = str(stock.get("name") or "").upper()
    if bool(stock.get("is_st")) or "ST" in name:
        return _BOARD_LIMITS.st_pct
    if str(stock.get("exchange") or "").upper() == "BJ" or str(stock.get("market") or "") == "北交所":
        return _BOARD_LIMITS.beijing_exchange_pct
    return limit_pct_for_code(str(stock.get("code") or ""))


def is_price_limit_move(stock: dict[str, Any], *, up: bool) -> bool:
    """按板位判断涨跌幅是否已触及涨/跌停，保留 0.2 个百分点的行情舍入容差。"""
    threshold = limit_pct_for_stock(stock) - 0.2
    pct = _as_float(stock.get("pct", stock.get("pct_chg", 0)))
    return pct >= threshold if up else pct <= -threshold


def limit_price(prev_close: float, pct: float, *, up: bool) -> float:
    """由前收盘价与涨跌停幅度（百分数）算出涨停价（up=True）或跌停价（up=False）。"""
    multiplier = 1 + pct / 100.0 if up else 1 - pct / 100.0
    return round(prev_close * multiplier, 2)


def previous_close_from_price(close: Any, pct_chg: Any) -> float:
    """由当日收盘价与涨跌幅反推前收盘价；数据无效（价格<=0 或跌幅<=-99%）时返回 0。"""
    close_value = _as_float(close)
    pct_value = _as_float(pct_chg)
    if close_value <= 0 or pct_value <= -99.0:
        return 0.0
    return close_value / (1 + pct_value / 100.0)


def main_board_up_limit(prev_close: float) -> float:
    """主板（10%）涨停价。

    数值等价于 `limit_price(prev_close, 10, up=True)`，此处保留独立实现，与隔夜持有扫描
    原口径（`round(prev_close * (1 + 0.10), 2)`）逐字一致，避免任何浮点差异。
    """
    return round(prev_close * (1 + MAIN_BOARD_LIMIT_UP_PCT), 2)


def is_one_word_limit_up_card(card: dict[str, Any]) -> bool:
    """卡片（推荐/展示 dict）口径的一字涨停判定：代码感知板位幅度、四点齐平。"""
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


def is_one_word_limit_up_bar(code: str, bar: Any) -> bool:
    """日 K bar 口径的一字涨停判定（推荐成交约束口径）。

    前收由 `close/(1+pct_chg/100)` 反推，板位幅度按代码前缀（10/20/30%）判定，要求
    open / low / high / close 四点全部贴合涨停价。
    """
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
    """日 K bar 口径的一字跌停判定：与 is_one_word_limit_up_bar 镜像（下限方向）。"""
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
    """一字涨停核心判定：四点（open/low/high/close）均 >= 涨停价 * 0.995。"""
    if prev_close <= 0:
        return False
    up_limit = limit_price(prev_close, limit_pct_for_code(code), up=True)
    return (
        _as_float(open_price) >= up_limit * (1 - _LIMIT_TOLERANCE)
        and _as_float(low) >= up_limit * (1 - _LIMIT_TOLERANCE)
        and _as_float(high) >= up_limit * (1 - _LIMIT_TOLERANCE)
        and _as_float(close) >= up_limit * (1 - _LIMIT_TOLERANCE)
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
    """一字跌停核心判定：四点（open/high/low/close）均 <= 跌停价 * 1.005。"""
    if prev_close <= 0:
        return False
    down_limit = limit_price(prev_close, limit_pct_for_code(code), up=False)
    return (
        _as_float(open_price) <= down_limit * (1 + _LIMIT_TOLERANCE)
        and _as_float(high) <= down_limit * (1 + _LIMIT_TOLERANCE)
        and _as_float(low) <= down_limit * (1 + _LIMIT_TOLERANCE)
        and _as_float(close) <= down_limit * (1 + _LIMIT_TOLERANCE)
    )


def is_one_word_limit_up_open_low(bar: Any, up_limit: float) -> bool:
    """一字涨停判定（隔夜持有扫描口径）：调用方给定涨停价 `up_limit`，只要求 open 与 low
    两点都贴合涨停价（>= up_limit * 0.995）。

    与 `is_one_word_limit_up_bar` 的差异（故两者语义不同、并存而非合并）：
    (1) 涨停价由调用方给定（通常 `main_board_up_limit(前一日实际收盘)`，主板固定 10%），
        不按当日 pct_chg 反推；
    (2) 只看 open / low 两点，不看 high / close（缩量封板等由调用方另行判定）。
    """
    return (
        _as_float(bar.open) >= up_limit * (1 - _LIMIT_TOLERANCE)
        and _as_float(bar.low) >= up_limit * (1 - _LIMIT_TOLERANCE)
    )


def is_twenty_cm_stock(stock: dict[str, Any]) -> tuple[bool, str]:
    """20cm（高波动）标的识别：优先看市场板块（market/board）文字，回退代码前缀 / 交易所。

    返回 (是否 20cm, 判定依据文本)。创业 / 科创 20cm、北交 30cm 高波动均返回 True。
    """
    code = str(stock.get("code") or "")
    exchange = str(stock.get("exchange") or "").upper()
    market = str(stock.get("market") or stock.get("board") or "")
    if "创业" in market or code.startswith(("300", "301")):
        return True, "创业板 20cm 标的"
    if "科创" in market or code.startswith(("688", "689")):
        return True, "科创板 20cm 标的"
    if "北交" in market or exchange == "BJ" or code.startswith(("8", "4")):
        return True, "北交所高波动标的"
    if not exchange and not market:
        return False, "缺少板块信息，按代码前缀未识别为 20cm"
    return False, "非 20cm 标的"


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _present(row: dict[str, Any], key: str) -> bool:
    return key in row and row.get(key) not in (None, "")

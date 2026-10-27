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

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY

__all__ = [
    "MAIN_BOARD_LIMIT_UP_PCT",
    "ST_MAIN_BOARD_RULE_CHANGE_DATE",
    "ST_MAIN_BOARD_PCT_AFTER_CHANGE",
    "limit_pct_for_code",
    "limit_pct_for_stock",
    "limit_pct_from_limit_prices",
    "st_main_board_pct_on",
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

# 主板 ST 涨跌停幅度的制度变更：自 2026-07-06 起由 5% 放宽为 10%（stk_limits 实测：该日起
# 180 只主板 ST 的 up/down 比值由约 1.105 变为约 1.222）。创业板 / 科创板 ST 一直是 20%、
# 北交所 ST 一直是 30%，不受此影响。
# 注意：execution policy 工件（v1..v5，fingerprint 已锁定）里的 `st_pct=5.0` 保持不动，
# 语义是「变更前的主板 ST 幅度」；变更后的幅度在这里按交易日取，避免为一个常量重发策略版本。
ST_MAIN_BOARD_RULE_CHANGE_DATE = "2026-07-06"
ST_MAIN_BOARD_PCT_AFTER_CHANGE = 10.0

_CST = timezone(timedelta(hours=8))


def limit_pct_for_code(code: str) -> float:
    """按证券代码前缀返回 canonical policy 的板位涨跌停幅度。"""
    normalized = str(code or "")
    if normalized.startswith(("300", "301")):
        return _BOARD_LIMITS.growth_board_pct
    if normalized.startswith(("688", "689")):
        return _BOARD_LIMITS.star_market_pct
    if normalized.startswith(("8", "4", "920")):  # 920xxx 是北交所 2024 起的新代码段
        return _BOARD_LIMITS.beijing_exchange_pct
    return _BOARD_LIMITS.main_board_pct


def _iso_trade_date(value: Any) -> str | None:
    """把 trade_date（str / date / datetime / 'YYYYMMDD'）归一成 ISO 日期串；无法识别返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()[:10]
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def st_main_board_pct_on(trade_date: Any = None) -> float:
    """主板 ST 股在 trade_date 的涨跌停幅度（百分数）：2026-07-06 前 5%，之后 10%。

    trade_date 缺失或无法解析时按「今天」（Asia/Shanghai）取，即当前生效规则。
    """
    iso = _iso_trade_date(trade_date) or datetime.now(_CST).date().isoformat()
    if iso >= ST_MAIN_BOARD_RULE_CHANGE_DATE:
        return ST_MAIN_BOARD_PCT_AFTER_CHANGE
    return _BOARD_LIMITS.st_pct


def limit_pct_for_stock(stock: dict[str, Any], trade_date: Any = None) -> float:
    """按股票完整身份 + 交易日返回涨跌停幅度（百分数）。

    先定板位：北交所 30%、创业板 / 科创板 20%（ST 同样 20%，ST 的特殊幅度只适用于主板）；
    主板且为 ST 时按交易日取 `st_main_board_pct_on`（2026-07-06 前 5%，之后 10%）；其余主板 10%。
    trade_date 取参数，其次 stock 里的 trade_date，都没有则按今天。
    """
    name = str(stock.get("name") or "").upper()
    is_st = bool(stock.get("is_st")) or "ST" in name
    code = str(stock.get("code") or "")
    if str(stock.get("exchange") or "").upper() == "BJ" or str(stock.get("market") or "") == "北交所":
        return _BOARD_LIMITS.beijing_exchange_pct
    board_pct = limit_pct_for_code(code)
    if board_pct != _BOARD_LIMITS.main_board_pct:
        return board_pct  # 创业 / 科创 / 北交代码：ST 不改变幅度
    if is_st:
        return st_main_board_pct_on(trade_date if trade_date is not None else stock.get("trade_date"))
    return board_pct


def limit_pct_on(code: str, trade_date: Any = None, *, is_st: bool = False) -> float:
    """按代码 + 交易日 + 是否 ST 取涨跌停幅度（百分数）：非 ST 等同 limit_pct_for_code；
    主板 ST 走 st_main_board_pct_on（2026-07-06 前 5%，之后 10%）。"""
    return limit_pct_for_stock({"code": code, "is_st": bool(is_st)}, trade_date)


_CANONICAL_LIMIT_PCTS = (5.0, 10.0, 20.0, 30.0)


def limit_pct_from_limit_prices(up_limit: Any, down_limit: Any, *, tolerance: float = 0.6) -> float | None:
    """由 stk_limits 的实际涨停价 / 跌停价反推幅度（百分数）：p = (up/down - 1) / (up/down + 1)。

    结果吸附到 5/10/20/30，偏差超过 tolerance 个百分点（如新股前几日无幅度护栏）返回 None，
    由调用方回退到规则。涨跌停价四舍五入到分，故比值有微小误差，吸附即可吸收。
    """
    up, down = _as_float(up_limit), _as_float(down_limit)
    if up <= 0 or down <= 0 or up <= down:
        return None
    ratio = up / down
    pct = (ratio - 1.0) / (ratio + 1.0) * 100.0
    nearest = min(_CANONICAL_LIMIT_PCTS, key=lambda cand: abs(cand - pct))
    return nearest if abs(nearest - pct) <= tolerance else None


def is_price_limit_move(stock: dict[str, Any], *, up: bool, trade_date: Any = None) -> bool:
    """按板位 / 交易日判断涨跌幅是否已触及涨/跌停，保留 0.2 个百分点的行情舍入容差。"""
    threshold = limit_pct_for_stock(stock, trade_date) - 0.2
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


def is_one_word_limit_up_bar(code: str, bar: Any, *, is_st: bool = False) -> bool:
    """日 K bar 口径的一字涨停判定（推荐成交约束口径）。

    前收由 `close/(1+pct_chg/100)` 反推，板位幅度按代码前缀（10/20/30%）判定；ST 股按 bar 的
    交易日取 ST 幅度（limit_pct_on），要求 open / low / high / close 四点全部贴合涨停价。
    """
    prev_close = previous_close_from_price(getattr(bar, "close", 0), getattr(bar, "pct_chg", 0))
    return is_one_word_limit_up_values(
        code,
        prev_close=prev_close,
        open_price=getattr(bar, "open", 0),
        high=getattr(bar, "high", 0),
        low=getattr(bar, "low", 0),
        close=getattr(bar, "close", 0),
        limit_pct=limit_pct_on(code, getattr(bar, "trade_date", None), is_st=True) if is_st else None,
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
    limit_pct: float | None = None,
) -> bool:
    """一字涨停核心判定：四点（open/low/high/close）均 >= 涨停价 * 0.995。

    limit_pct 不传时按代码前缀取板位幅度；ST 股由调用方用 limit_pct_on(code, 交易日, is_st=True) 传入。
    """
    if prev_close <= 0:
        return False
    up_limit = limit_price(prev_close, limit_pct if limit_pct else limit_pct_for_code(code), up=True)
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
    if "北交" in market or exchange == "BJ" or code.startswith(("8", "4", "920")):
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

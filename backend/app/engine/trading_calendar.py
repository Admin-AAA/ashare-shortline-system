"""交易日历助手：把「最新可用交易日」与「交易日归一化」的推导收拢为唯一深模块。

此前 `latest_allowed_bar_date` / `latest_allowed_quote_date` / `previous_weekday`
在 market_data、market_pool、data_gap_remediation_service、data_sources 四处逐字复制，
`normalize_trade_date`（把杂乱日期表示归一为 ISO `YYYY-MM-DD`）同样散落多处，
任何一处修改都必须同步多份，极易漂移。这里把它们收敛为单一实现（deep module）：
接口窄（四个纯函数、只吃 date/原始值、只吐字符串/日期），实现体承担周末回退、
盘后 17:30 数据就绪判定、日期字符串归一等全部日历细节，调用方无需再关心这些复杂度。

- 纯计算，无数据库 / 网络 / Tushare 依赖，可 in-process 单测。
- 行为与原副本逐字一致（重构基线：相同输入相同输出）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

__all__ = [
    "latest_allowed_bar_date",
    "latest_allowed_quote_date",
    "normalize_trade_date",
    "previous_weekday",
]


def latest_allowed_bar_date(today: date | None = None) -> str:
    """最新可用「日K收盘」交易日（ISO 字符串）。

    未显式给定 today 时以当前时刻推导：工作日 17:30 之前视为当日 daily 尚未就绪
    （Tushare 盘后约 17:00 后才出当日收盘数据），回退到上一交易日；周六/周日
    分别回退到周五。
    """
    if today is None:
        now = datetime.now()
        today = now.date()
        if today.weekday() < 5 and (now.hour < 17 or (now.hour == 17 and now.minute < 30)):
            # 17:30 前视为当日 daily 未就绪（Tushare 盘后约 17:00 后才出当日收盘数据）
            today = previous_weekday(today)
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def latest_allowed_quote_date(today: date | None = None) -> str:
    """最新可用「行情报价」交易日（ISO 字符串）。

    与 bar 日期的差异：报价（实时/快照）不受盘后 17:30 就绪窗口约束，工作日直接
    取当日，仅对周末做回退。
    """
    today = today or datetime.now().date()
    if today.weekday() == 5:
        return (today - timedelta(days=1)).isoformat()
    if today.weekday() == 6:
        return (today - timedelta(days=2)).isoformat()
    return today.isoformat()


def previous_weekday(day: date) -> date:
    """返回 day 之前最近的一个工作日（跳过周六、周日）。"""
    current = day - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def normalize_trade_date(value: Any) -> str:
    """把任意交易日表示归一为 ISO `YYYY-MM-DD` 字符串。

    兼容三类输入：已带连字符的 `2026-05-28`（截前 10 位）、纯数字 `20260528`
    （切分补连字符）、以及其它杂散字符串（截前 10 位兜底）。空值返回空串。

    此前在 market_pool / data_gap_remediation_service / data_sources /
    market_auxiliary 等处逐字复制，收敛到此单一深模块。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return raw[:10]

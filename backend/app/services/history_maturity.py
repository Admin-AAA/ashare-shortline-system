"""历史成熟度深模块：把「历史 K 线成熟度」判定收拢为唯一实现。

此前 `_load_history_maturity_map` / `_is_natural_short_history` 在 market_pool 与
data_gap_remediation_service 两处逐字复制（其中成熟度 map 的两份仅在过滤方式上
漂移：一份 SQL 侧 `WHERE code IN (...)`，一份 Python 侧集合过滤——对相同 DB 状态
返回值等价）。这里收敛为单一深模块（deep module）：接口窄（两个函数、显式吃 db
session），实现体承担建仓日/首根 K 线日/最新 K 线覆盖等成熟度细节，调用方经 import
获得，代码中不再存在本地副本。

规范实现取 SQL 侧过滤版（`WHERE DailyKbar.code IN (normalized_codes)`）：与 Python
侧过滤版对相同 DB 状态输出一致，但把过滤下推数据库，查询更高效。

- local-substitutable：依赖 SQLAlchemy Session，可用内存 sqlite 造数据 in-process 单测。
- `normalize_trade_date` 复用 `app.engine.trading_calendar` 单一来源，不再本地重造。

注：本模块暂落 `services/history_maturity.py`；S5 拆 market_pool 为包时归位到
`services/market_pool/history_maturity.py`（见 design §3/§7、需求 2.4）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.trading_calendar import normalize_trade_date
from app.models.tables import DailyKbar, Stock

__all__ = [
    "load_history_maturity_map",
    "is_natural_short_history",
]


def load_history_maturity_map(db: Session, codes: list[str]) -> dict[str, dict[str, str | None]]:
    """加载给定代码集合的历史成熟度画像：首根 K 线日 + 上市日。

    返回 `{code: {"first_bar_date": ISO|None, "list_date": ISO|None}}`。过滤下推到
    SQL（`WHERE code IN (...)`），空代码集合直接返回空 dict。
    """
    normalized_codes = [str(code or "").strip() for code in codes if str(code or "").strip()]
    if not normalized_codes:
        return {}
    result: dict[str, dict[str, str | None]] = {}
    first_bar_rows = db.execute(
        select(
            DailyKbar.code.label("code"),
            func.min(DailyKbar.trade_date).label("first_bar_date"),
        )
        .where(DailyKbar.code.in_(normalized_codes))
        .group_by(DailyKbar.code)
    ).all()
    for row in first_bar_rows:
        result[str(row.code)] = {
            "first_bar_date": normalize_trade_date(row.first_bar_date),
            "list_date": None,
        }
    stock_rows = db.execute(
        select(Stock.code, Stock.list_date).where(Stock.code.in_(normalized_codes))
    ).all()
    for row in stock_rows:
        item = result.setdefault(str(row.code), {"first_bar_date": None, "list_date": None})
        item["list_date"] = normalize_trade_date(row.list_date)
    return result


def is_natural_short_history(
    *,
    bar_count: int,
    latest_bar_date: str | None,
    quote_date: str,
    first_bar_date: str | None,
    list_date: str | None,
) -> bool:
    """判定「自然次新股」：K 线虽短但覆盖到报价日、且首根 K 线不晚于上市日。

    满足全部条件才为真：有 K 线、最新 K 线日不早于报价日、首根 K 线日不晚于上市日。
    据此把「新股天然短历史」与「数据缺口导致的短历史」区分开。
    """
    return bool(
        0 < int(bar_count or 0)
        and latest_bar_date
        and quote_date
        and latest_bar_date >= quote_date
        and first_bar_date
        and list_date
        and first_bar_date <= list_date
    )

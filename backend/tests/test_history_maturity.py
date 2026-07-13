"""`app.services.market_pool.history_maturity` 深模块单测。

历史成熟度收口后的唯一实现（原 market_pool / data_gap 两份副本已删，见需求 2.4/2.5）。
`load_history_maturity_map` 触库，故用内存 sqlite 造数据 in-process 验证（禁止真实 MySQL）：
仅建 DailyKbar / Stock 两张表，规避 tables.py 中 MySQL 方言类型在 sqlite 的编译问题。
`is_natural_short_history` 为纯函数，直接对真/假分支断言。

关联需求：2.4（历史成熟度只在单一深模块）、2.5（调用方经 import 获得）、
1.1（行为不变基线：SQL 侧过滤版与原 Python 侧过滤版对相同 DB 状态等价）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import Base
from app.models.tables import DailyKbar, Stock
from app.services.market_pool.history_maturity import (
    is_natural_short_history,
    load_history_maturity_map,
)


# ── 内存 sqlite 夹具：只建历史成熟度所需的两张表 ──────────────────────────

@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[DailyKbar.__table__, Stock.__table__])
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _add_bar(db: Session, code: str, trade_date: str) -> None:
    db.add(DailyKbar(code=code, trade_date=trade_date))


def _add_stock(db: Session, code: str, list_date: str | None) -> None:
    db.add(Stock(code=code, name=f"股票{code}", list_date=list_date))


# ── load_history_maturity_map：典型 ───────────────────────────────────────

def test_maturity_map_typical(db: Session):
    """有多根 K 线 + 上市日：first_bar_date 取最早 K 线日，list_date 归一。"""
    _add_bar(db, "000001", "2026-05-28")
    _add_bar(db, "000001", "2026-04-24")  # 最早
    _add_bar(db, "000001", "2026-05-06")
    _add_stock(db, "000001", "2026-04-24")
    db.commit()

    result = load_history_maturity_map(db, ["000001"])

    assert result == {"000001": {"first_bar_date": "2026-04-24", "list_date": "2026-04-24"}}


def test_maturity_map_normalizes_compact_list_date(db: Session):
    """Stock.list_date 存紧凑格式时，map 输出归一为 ISO。"""
    _add_bar(db, "600000", "2026-03-02")
    _add_stock(db, "600000", "20260301")  # 紧凑 -> 期望 2026-03-01
    db.commit()

    result = load_history_maturity_map(db, ["600000"])

    assert result["600000"]["first_bar_date"] == "2026-03-02"
    assert result["600000"]["list_date"] == "2026-03-01"


# ── load_history_maturity_map：边界 ───────────────────────────────────────

def test_maturity_map_empty_codes_returns_empty(db: Session):
    """空代码集合直接返回空 dict，不查库。"""
    _add_bar(db, "000001", "2026-05-28")
    db.commit()

    assert load_history_maturity_map(db, []) == {}


def test_maturity_map_blank_codes_are_filtered(db: Session):
    """全为空白的代码归一后为空集合 -> 返回空 dict。"""
    assert load_history_maturity_map(db, ["", "   ", None]) == {}  # type: ignore[list-item]


def test_maturity_map_filters_to_requested_codes_only(db: Session):
    """库里有多只股票，只返回被请求的代码（SQL 侧 WHERE code IN 过滤正确）。"""
    _add_bar(db, "000001", "2026-05-28")
    _add_bar(db, "600000", "2026-05-28")
    _add_bar(db, "300750", "2026-05-28")
    _add_stock(db, "000001", "2026-01-01")
    _add_stock(db, "600000", "2026-01-01")
    _add_stock(db, "300750", "2026-01-01")
    db.commit()

    result = load_history_maturity_map(db, ["000001", "300750"])

    assert set(result.keys()) == {"000001", "300750"}


def test_maturity_map_bars_without_stock_row(db: Session):
    """有 K 线但无 Stock 记录：first_bar_date 有值，list_date 为 None。"""
    _add_bar(db, "000001", "2026-05-28")
    _add_bar(db, "000001", "2026-05-20")
    db.commit()

    result = load_history_maturity_map(db, ["000001"])

    assert result == {"000001": {"first_bar_date": "2026-05-20", "list_date": None}}


def test_maturity_map_stock_without_bars(db: Session):
    """有 Stock 记录但无 K 线：first_bar_date 为 None，list_date 有值（setdefault 路径）。"""
    _add_stock(db, "000001", "2026-04-24")
    db.commit()

    result = load_history_maturity_map(db, ["000001"])

    assert result == {"000001": {"first_bar_date": None, "list_date": "2026-04-24"}}


def test_maturity_map_code_with_neither_absent(db: Session):
    """既无 K 线也无 Stock 的被请求代码，不出现在结果里。"""
    _add_bar(db, "000001", "2026-05-28")
    _add_stock(db, "000001", "2026-04-24")
    db.commit()

    result = load_history_maturity_map(db, ["000001", "999999"])

    assert "999999" not in result
    assert set(result.keys()) == {"000001"}


# ── is_natural_short_history：真/假分支 ──────────────────────────────────

def test_natural_short_history_true():
    """K 线短但覆盖报价日、首根 K 线不晚于上市日 -> 自然次新股。"""
    assert is_natural_short_history(
        bar_count=22,
        latest_bar_date="2026-05-28",
        quote_date="2026-05-28",
        first_bar_date="2026-04-24",
        list_date="2026-04-24",
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        # 最新 K 线日早于报价日 -> 数据缺口，非自然短历史
        dict(bar_count=22, latest_bar_date="2026-05-20", quote_date="2026-05-28",
             first_bar_date="2026-04-24", list_date="2026-04-24"),
        # 首根 K 线晚于上市日 -> 缺早期数据，非自然短历史
        dict(bar_count=22, latest_bar_date="2026-05-28", quote_date="2026-05-28",
             first_bar_date="2026-05-01", list_date="2026-04-24"),
        # 无 K 线
        dict(bar_count=0, latest_bar_date="2026-05-28", quote_date="2026-05-28",
             first_bar_date="2026-04-24", list_date="2026-04-24"),
        # 缺 latest_bar_date
        dict(bar_count=22, latest_bar_date=None, quote_date="2026-05-28",
             first_bar_date="2026-04-24", list_date="2026-04-24"),
        # 缺 list_date
        dict(bar_count=22, latest_bar_date="2026-05-28", quote_date="2026-05-28",
             first_bar_date="2026-04-24", list_date=None),
    ],
)
def test_natural_short_history_false_cases(kwargs):
    assert not is_natural_short_history(**kwargs)


def test_natural_short_history_always_returns_bool():
    """无论输入，返回值恒为 bool 类型。"""
    for latest in ["2026-05-28", "2026-05-01", None]:
        for first in ["2026-04-24", "2026-06-01", None]:
            out = is_natural_short_history(
                bar_count=10,
                latest_bar_date=latest,
                quote_date="2026-05-28",
                first_bar_date=first,
                list_date="2026-04-24",
            )
            assert isinstance(out, bool)

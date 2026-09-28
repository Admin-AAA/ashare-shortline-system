"""upper_shadow_count20 必须真的有人算：本地指标补数独占写入这一列。

背景数据墙：这一列从 2026-09-09 起到 09-24 每一天全表恒为 0。唯一的写入点是同步侧的
`int(bar.get("upper_shadow_count20") or 0)`，而 Tushare 从不返回这个本地派生指标，于是每次
插入都写 0；L-144 已把它从 upsert 的 update 列表摘掉「交给 backfill 独占」，但
backfill_indicators 根本不计算这一列——等于没人算它。6 个策略 prefilter 的长上影上限条件
因此静默失效（0 永远满足）。

本测试锁定三件事：
  1. 补数会写入这一列，且与消费侧同一个计算口径
  2. 0 是合法值（无长上影），不能因此被判成缺失而跳过
  3. 该列不受「ma 缺失才补」门控，已有 ma 的行照样会被重算覆盖
"""
from __future__ import annotations

from app.models.tables import DailyKbar
from app.services.indicator_computer import upper_shadow_count
from scripts.backfill_indicators import backfill_date

TEST_CODE = "889901"
TEST_DATES = [f"2099-01-{day:02d}" for day in range(1, 26)]
TARGET_DATE = TEST_DATES[-1]


def _long_upper_shadow_bar(trade_date: str) -> dict:
    """上影线占全幅 0.7、实体很小——命中 upper_shadow_count 的 0.45 阈值。"""
    return {"trade_date": trade_date, "open": 10.0, "high": 11.0, "low": 9.9, "close": 10.05}


def _plain_bar(trade_date: str) -> dict:
    """几乎没有上影线的阳线。"""
    return {"trade_date": trade_date, "open": 10.0, "high": 10.6, "low": 9.9, "close": 10.55}


def _seed(db, shapes: list[dict], *, with_ma: bool) -> None:
    for shape in shapes:
        db.add(
            DailyKbar(
                code=TEST_CODE,
                trade_date=shape["trade_date"],
                open=shape["open"],
                high=shape["high"],
                low=shape["low"],
                close=shape["close"],
                volume=100000.0,
                amount=1000000.0,
                pct_chg=0.5,
                turnover_rate=1.0,
                ma5=10.0 if with_ma else None,
                ma10=10.0 if with_ma else None,
                ma20=10.0 if with_ma else None,
                ma60=10.0 if with_ma else None,
                upper_shadow_count20=0,
            )
        )
    db.flush()


def test_backfill_writes_upper_shadow_count(db):
    """补数后该列等于近 20 根按消费侧口径算出的值，不再恒为 0。"""
    shapes = [_long_upper_shadow_bar(date) for date in TEST_DATES[:8]]
    shapes += [_plain_bar(date) for date in TEST_DATES[8:]]
    _seed(db, shapes, with_ma=False)

    backfill_date(db, TARGET_DATE)

    row = db.query(DailyKbar).filter(
        DailyKbar.code == TEST_CODE, DailyKbar.trade_date == TARGET_DATE
    ).one()
    expected = upper_shadow_count(shapes[-20:])
    assert expected > 0, "构造数据本身应含长上影，否则断言失去意义"
    assert row.upper_shadow_count20 == expected


def test_zero_is_a_legal_value_not_a_gap(db):
    """全是无上影的 bar 时写 0——这是真实结论，不能当成缺失。"""
    shapes = [_plain_bar(date) for date in TEST_DATES]
    _seed(db, shapes, with_ma=False)

    backfill_date(db, TARGET_DATE)

    row = db.query(DailyKbar).filter(
        DailyKbar.code == TEST_CODE, DailyKbar.trade_date == TARGET_DATE
    ).one()
    assert row.upper_shadow_count20 == 0


def test_column_is_refreshed_even_when_ma_is_already_present(db):
    """ma 已就位的行会跳过「缺失才补」门控，这一列仍必须被重算覆盖。"""
    shapes = [_long_upper_shadow_bar(date) for date in TEST_DATES]
    _seed(db, shapes, with_ma=True)

    backfill_date(db, TARGET_DATE)

    row = db.query(DailyKbar).filter(
        DailyKbar.code == TEST_CODE, DailyKbar.trade_date == TARGET_DATE
    ).one()
    assert row.upper_shadow_count20 == upper_shadow_count(shapes[-20:])

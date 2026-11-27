"""QVIX 波动率数据源单测：字段映射、pct_chg、升序、增量去重、--since 过滤。

不联网：monkeypatch ``qvix._ak`` 返回一个假 akshare 模块。落库用内存 sqlite，只建
IndexDaily 一张表（其余表带 MySQL 专有类型，不创建）。
"""
from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.session import Base
from app.models.tables import IndexDaily, TradeCalendar
from app.services.datasource import qvix


def _fake_frame() -> pd.DataFrame:
    # 升序，含一个 NaN close 之外的正常值；date 是 datetime.date（与实测一致）
    return pd.DataFrame({
        "date": [_dt.date(2026, 11, 11), _dt.date(2026, 11, 12), _dt.date(2026, 11, 13)],
        "open": [16.0, 16.5, 16.59],
        "high": [16.4, 17.0, 17.18],
        "low": [15.8, 16.1, 16.11],
        "close": [16.2, 16.5, 17.17],
    })


@pytest.fixture
def fake_ak(monkeypatch):
    calls: list[str] = []

    def _make(name):
        def _fn():
            calls.append(name)
            return _fake_frame()
        return _fn

    fake = SimpleNamespace(
        index_option_50etf_qvix=_make("index_option_50etf_qvix"),
        index_option_300etf_qvix=_make("index_option_300etf_qvix"),
        index_option_300index_qvix=_make("index_option_300index_qvix"),
    )
    monkeypatch.setattr(qvix, "_ak", lambda: fake)
    return calls


@pytest.fixture
def sqlite_db():
    engine = create_engine("sqlite://")
    IndexDaily.__table__.create(engine)
    # 交易日历为空：期权日行情计算补数没有交易日可补，这里只测 optbbs 路径
    TradeCalendar.__table__.create(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_fetch_qvix_maps_fields_and_pct_chg(fake_ak):
    rows = qvix.fetch_qvix("QVIX300.SH")
    assert [r["trade_date"] for r in rows] == ["2026-11-11", "2026-11-12", "2026-11-13"]
    assert all(r["ts_code"] == "QVIX300.SH" for r in rows)
    assert all(r["volume"] == 0.0 for r in rows)
    # 首日无昨收 -> pct_chg 0；其后按相邻收盘价
    assert rows[0]["pct_chg"] == 0.0
    assert rows[1]["pct_chg"] == pytest.approx(round((16.5 / 16.2 - 1) * 100, 4))
    assert rows[2]["pct_chg"] == pytest.approx(round((17.17 / 16.5 - 1) * 100, 4))
    assert rows[2]["close"] == pytest.approx(17.17)
    assert fake_ak == ["index_option_300etf_qvix"]


def test_fetch_qvix_unknown_code_raises(fake_ak):
    with pytest.raises(KeyError):
        qvix.fetch_qvix("NOPE.SH")


def test_fetch_qvix_drops_zero_rows(fake_ak, monkeypatch):
    """上游缺失日填 0（300 系上市前整段为 0）：零值行必须丢弃，pct_chg 跨缺口按有效昨收算。"""
    frame = pd.DataFrame({
        "date": [_dt.date(2019, 12, 20), _dt.date(2026, 11, 11), _dt.date(2026, 11, 12), _dt.date(2026, 11, 13)],
        "open": [0.0, 16.0, 0.0, 16.59],
        "high": [0.0, 16.4, 0.0, 17.18],
        "low": [0.0, 15.8, 0.0, 16.11],
        "close": [0.0, 16.2, 0.0, 17.17],
    })
    monkeypatch.setattr(qvix, "_ak", lambda: SimpleNamespace(index_option_300etf_qvix=lambda: frame))
    rows = qvix.fetch_qvix("QVIX300.SH")
    assert [r["trade_date"] for r in rows] == ["2026-11-11", "2026-11-13"]
    assert all(r["close"] > 0 for r in rows)
    # 首个有效行 pct_chg=0；跨缺口后按 16.2 昨收计算，而不是被 0 值污染
    assert rows[0]["pct_chg"] == 0.0
    assert rows[1]["pct_chg"] == pytest.approx(round((17.17 / 16.2 - 1) * 100, 4))


def test_fetch_qvix_dedups_same_day(fake_ak, monkeypatch):
    """上游同日重复行（盘中快照+收盘修正）：保留最后一条有效行，不撞唯一键。"""
    frame = pd.DataFrame({
        "date": [_dt.date(2026, 11, 11), _dt.date(2026, 11, 12), _dt.date(2026, 11, 12)],
        "open": [16.0, 16.21, 16.71],
        "high": [16.4, 17.48, 16.86],
        "low": [15.8, 15.82, 15.81],
        "close": [16.2, 15.92, 15.9],
    })
    monkeypatch.setattr(qvix, "_ak", lambda: SimpleNamespace(index_option_50etf_qvix=lambda: frame))
    rows = qvix.fetch_qvix("QVIX50.SH")
    assert [r["trade_date"] for r in rows] == ["2026-11-11", "2026-11-12"]
    assert rows[1]["close"] == pytest.approx(15.9)  # 保留收盘修正行
    assert rows[1]["pct_chg"] == pytest.approx(round((15.9 / 16.2 - 1) * 100, 4))


def test_ensure_qvix_daily_incremental_dedup(fake_ak, sqlite_db):
    first = qvix.ensure_qvix_daily(sqlite_db, ["QVIX50.SH"])
    assert first == {"QVIX50.SH": 3}
    # 再跑一次：全部已存在，写 0 行（不重复、不报错）
    second = qvix.ensure_qvix_daily(sqlite_db, ["QVIX50.SH"])
    assert second == {"QVIX50.SH": 0}
    stored = sqlite_db.execute(select(IndexDaily).where(IndexDaily.ts_code == "QVIX50.SH")).scalars().all()
    assert len(stored) == 3


def test_ensure_qvix_daily_since_filter(fake_ak, sqlite_db):
    counts = qvix.ensure_qvix_daily(sqlite_db, ["QVIX300.SH"], since="2026-11-12")
    assert counts == {"QVIX300.SH": 2}
    dates = sorted(
        r.trade_date for r in
        sqlite_db.execute(select(IndexDaily).where(IndexDaily.ts_code == "QVIX300.SH")).scalars().all()
    )
    assert dates == ["2026-11-12", "2026-11-13"]


def test_latest_qvix(fake_ak, sqlite_db):
    qvix.ensure_qvix_daily(sqlite_db, ["QVIX300IDX.SH"])
    latest = qvix.latest_qvix(sqlite_db, "QVIX300IDX.SH")
    assert latest["trade_date"] == "2026-11-13"
    assert latest["close"] == pytest.approx(17.17)


def test_qvix_routed_and_switchable():
    """market.qvix 必须在网关路由集合里，provider 已注册且可被数据源管理单独启停。"""
    from app.services.datasource import topology, wrap
    from app.services.datasource.gateway import ALWAYS_GATEWAY

    assert qvix.DATASET in ALWAYS_GATEWAY
    assert qvix.PROVIDER in wrap.PROVIDERS
    item_keys = topology.gateway_item_keys()
    assert (qvix.DATASET, qvix.PROVIDER) in item_keys

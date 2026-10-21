"""数据网关拓扑 + 开关（配置页「数据源管理」）单测。"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import DataSourceConfig, DataSourceHealth
from app.services import data_sources as ds
from app.services.datasource import gateway as gw
from app.services.datasource import topology


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:")
    DataSourceConfig.__table__.create(engine)
    DataSourceHealth.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()


def test_switch_rejects_undeclared_key(db_session):
    assert ds.set_gateway_switch(db_session, "nope", "quote", False) is False
    assert db_session.get(DataSourceConfig, {"source": "nope", "source_type": "quote"}) is None


def test_switch_survives_registry_orphan_cleanup(db_session):
    # sina/quote 不在七类注册表里：禁用行若被孤儿清理删掉，新浪会静默回流。
    assert ds.set_gateway_switch(db_session, "sina", "quote", False) is True
    ds.sync_data_source_configs(db_session)
    row = db_session.get(DataSourceConfig, {"source": "sina", "source_type": "quote"})
    assert row is not None and row.enabled is False
    # 网关开关读取同一行：provider 被跳过
    gw._ENABLED_CACHE.update(value=None, expires_at=0.0)
    from app.services.datasource.sina_realtime import SinaProvider

    assert gw.provider_enabled(SinaProvider(), {("sina", "quote")}) is False


def test_topology_reflects_switch_and_breakers(db_session, monkeypatch):
    now = time.time()
    monkeypatch.setattr(topology, "_breaker_rows", lambda: [
        ("tushare:promax:rt_k", now + 600, "rate_limit"),  # 路由熔断 → 实时行情 rt_k 显示熔断
        ("tushare:quantdata:daily_basic", now - 10, "probe_failed"),  # 冷却已过 → 待探测
    ])
    monkeypatch.setattr(ds, "_provider_health_rows", lambda: [])
    ds.set_gateway_switch(db_session, "xiaoshi", "quote", False)

    t = topology.build_topology(db_session)
    by = {d["key"]: {p["name"]: p for p in d["providers"]} for d in t["datasets"]}
    assert by["quote.realtime"]["tushare_rt_k"]["breaker"]["state"] == "open"
    assert by["quote.realtime"]["sina_realtime"]["breaker"]["state"] == "closed"
    assert by["tushare.*"]["quantdata"]["breaker"]["state"] == "half_open"
    assert by["tushare.*"]["promax"]["breaker"]["state"] == "open"
    # 同一开关的所有使用位置都反映禁用
    assert by["quote.realtime"]["xiaoshi_snapshot"]["switches"][0]["enabled"] is False
    assert by["quote.daily"]["xiaoshi_quote"]["switches"][0]["enabled"] is False
    # 腾讯行情受 cutover flag 控制，默认不在链上
    assert by["quote.realtime"]["tencent_qt"]["active"] is False

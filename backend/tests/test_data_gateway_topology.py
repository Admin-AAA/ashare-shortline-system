"""数据网关拓扑 + 开关（配置页「数据源管理」）单测。"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import DataGatewayItemSwitch, DataSourceConfig, DataSourceHealth
from app.services import data_sources as ds
from app.services.datasource import gateway as gw
from app.services.datasource import topology


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    DataSourceConfig.__table__.create(engine)
    DataSourceHealth.__table__.create(engine)
    DataGatewayItemSwitch.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    # 网关热加载读库走 app.db.session.SessionLocal：指向同一个内存库，测的是真实读开关路径。
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    gw.invalidate_switch_caches()
    db = factory()
    yield db
    db.close()
    gw.invalidate_switch_caches()


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
    # ASHARE_DATASOURCE_CUTOVER 固定为空，不读本机 .env（本机开了 quote.realtime.tencent 时腾讯会在链上，断言随环境变）
    orig_flag_set = gw._flag_set
    monkeypatch.setattr(gw, "_flag_set", lambda attr: set() if attr == "datasource_cutover" else orig_flag_set(attr))
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


# ── 数据项级开关：只在一个数据类型里禁用一个源 ─────────────────────────────


def test_item_switch_rejects_unknown_item(db_session):
    assert ds.set_gateway_item_switch(db_session, "quote.realtime", "nope", False) is False
    # Tushare Pro 路由列只有源总开关，不接受数据项级开关
    assert ds.set_gateway_item_switch(db_session, "tushare.*", "promax", False) is False
    assert db_session.query(DataGatewayItemSwitch).count() == 0


def test_item_switch_does_not_touch_shared_source_switch(db_session, monkeypatch):
    monkeypatch.setattr(topology, "_breaker_rows", lambda: [])
    monkeypatch.setattr(ds, "_provider_health_rows", lambda: [])
    # 在「实时行情」里禁用 Promax rt_k：只写数据项行，promax/tushare_provider 总开关不动
    assert ds.set_gateway_item_switch(db_session, "quote.realtime", "tushare_rt_k", False) is True
    assert db_session.get(DataSourceConfig, {"source": "promax", "source_type": "tushare_provider"}) is None
    # 「随路由」、没有源开关的 Tushare stk_mins 也能单独禁用
    assert ds.set_gateway_item_switch(db_session, "bar.minute", "tushare_stk_mins", False) is True

    by = {d["key"]: {p["name"]: p for p in d["providers"]} for d in topology.build_topology(db_session)["datasets"]}
    rtk = by["quote.realtime"]["tushare_rt_k"]
    assert rtk["item_enabled"] is False and rtk["source_enabled"] is True
    assert by["bar.minute"]["tushare_stk_mins"]["item_enabled"] is False
    assert by["tushare.*"]["promax"]["source_enabled"] is True
    assert by["tushare.*"]["promax"]["item_switchable"] is False
    assert by["quote.realtime"]["sina_realtime"]["item_enabled"] is True
    # 重新启用
    assert ds.set_gateway_item_switch(db_session, "quote.realtime", "tushare_rt_k", True) is True
    by = {d["key"]: {p["name"]: p for p in d["providers"]} for d in topology.build_topology(db_session)["datasets"]}
    assert by["quote.realtime"]["tushare_rt_k"]["item_enabled"] is True


class _FakeProvider:
    config_keys = (("shared", "quote"),)  # 两个数据类型共用同一个源总开关

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def supports(self, dataset, query):
        return True

    def fetch(self, dataset, query):
        self.calls.append(dataset)
        return [{"src": self.name}]


def test_item_disable_in_dataset_a_keeps_same_source_in_dataset_b(db_session, monkeypatch):
    from app.services.datasource import cache as _c

    monkeypatch.setattr(_c, "fresh_get", lambda *a, **k: None)
    monkeypatch.setattr(_c, "fresh_put", lambda *a, **k: None)
    monkeypatch.setattr(_c, "stale_put", lambda *a, **k: None)
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    gw.reset_all_breakers()
    shared, backup = _FakeProvider("shared_src"), _FakeProvider("backup_src")
    gw_a = gw.DatasetGateway([shared, backup])
    gw_b = gw.DatasetGateway([shared, backup])

    # 直接写库（假数据集不在拓扑里，绕过 set_gateway_item_switch 的白名单），走真实的热加载读库
    db_session.add(DataGatewayItemSwitch(dataset_key="test.a", provider="shared_src", enabled=False))
    db_session.commit()
    gw.invalidate_switch_caches()

    assert gw_a.fetch_first("test.a", {"q": 1}).provider == "backup_src"
    assert gw_b.fetch_first("test.b", {"q": 1}).provider == "shared_src"
    assert shared.calls == ["test.b"]
    # 源总开关仍然生效：整源关闭时两个数据类型都跳过
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: {("shared", "quote")})
    assert gw_b.fetch_first("test.b", {"q": 2}).provider is None


def test_item_disable_in_wrap_dataset_keeps_other_dataset(db_session, monkeypatch):
    from app.services.datasource import wrap

    wrap._breakers.clear()
    monkeypatch.setattr(wrap, "_limiter", lambda n, r: type("L", (), {"acquire": lambda self: None})())
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    # 东财同时服务「公告补数」和「新闻公告」；只在公告补数里禁用
    assert ds.set_gateway_item_switch(db_session, "ann.list", "eastmoney", False) is True

    with pytest.raises(gw.DataSourceError) as e:
        wrap.gw_call("ann.list", "eastmoney", lambda: [1])
    assert e.value.kind is gw.ErrorKind.DISABLED
    assert wrap.gw_call("aux.news", "eastmoney", lambda: [2]) == [2]


def test_backup_item_switch_covers_whole_tushare_family(db_session, monkeypatch):
    from app.services.datasource import akshare_backup

    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    assert akshare_backup.backup_enabled() is True
    assert ds.set_gateway_item_switch(db_session, "tushare.backup", "akshare", False) is True
    assert akshare_backup.backup_enabled() is False
    cands = akshare_backup.backup_gateway()._candidates("tushare.margin_detail", {})
    assert "akshare" not in [p.name for p in cands]

"""数据源管理（provider 启停 + 测试探针）单测。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import Base, DataSourceConfig, DataSourceHealth
from app.services import data_sources as ds


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    # 只建需要的两张表，避免其它表的 MySQL 专有定义在 sqlite 下报错
    DataSourceConfig.__table__.create(engine)
    DataSourceHealth.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()


def _stub_providers(monkeypatch, names):
    """让 _known_provider_names 返回指定集合，绕过真实 tushare provider。"""
    monkeypatch.setattr(ds, "_known_provider_names", lambda: set(names))


def test_set_provider_enabled_creates_row_and_returns_entry(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["gw_b"])
    monkeypatch.setattr(
        ds, "_provider_health_rows",
        lambda: [{"provider_name": "gw_b", "enabled": False, "status": "disabled"}],
    )
    # 避免触碰真实 provider 缓存
    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "invalidate_provider_enabled_override", lambda: None)

    out = ds.set_provider_enabled(db_session, "gw_b", False)
    assert out is not None and out["provider_name"] == "gw_b"

    row = db_session.get(DataSourceConfig, {"source": "gw_b", "source_type": "tushare_provider"})
    assert row is not None and row.enabled is False


def test_set_provider_enabled_unknown_returns_none(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["gw_a"])
    out = ds.set_provider_enabled(db_session, "nonexist", True)
    assert out is None
    # 未接入不应落库
    assert db_session.get(DataSourceConfig, {"source": "nonexist", "source_type": "tushare_provider"}) is None


def test_set_provider_enabled_toggles_existing_row(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["gw_a"])
    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "invalidate_provider_enabled_override", lambda: None)
    monkeypatch.setattr(ds, "_provider_health_rows", lambda: [{"provider_name": "gw_a", "enabled": True}])

    db_session.add(DataSourceConfig(source="gw_a", source_type="tushare_provider", enabled=True, priority=0))
    db_session.commit()
    ds.set_provider_enabled(db_session, "gw_a", False)
    row = db_session.get(DataSourceConfig, {"source": "gw_a", "source_type": "tushare_provider"})
    assert row.enabled is False


def test_test_provider_realtime_quote_path(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["realtime_quote"])
    # realtime_quote 探针走 fetch_realtime_quotes；mock 成返回有数据
    import app.services.realtime_quote_client as rq
    monkeypatch.setattr(rq, "fetch_realtime_quotes", lambda codes, use_cache=False: {"000001": {"price": 11.0}})

    out = ds.test_provider(db_session, "realtime_quote")
    assert out["ok"] is True
    assert out["status"] == "healthy"
    assert out["latency_ms"] is not None

    health = db_session.get(DataSourceHealth, {"source": "realtime_quote", "source_type": "tushare_provider"})
    assert health is not None and health.success_count == 1 and health.last_error is None


def test_test_provider_realtime_quote_empty_is_degraded(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["realtime_quote"])
    import app.services.realtime_quote_client as rq
    monkeypatch.setattr(rq, "fetch_realtime_quotes", lambda codes, use_cache=False: {})

    out = ds.test_provider(db_session, "realtime_quote")
    assert out["ok"] is False
    assert out["status"] == "degraded"
    health = db_session.get(DataSourceHealth, {"source": "realtime_quote", "source_type": "tushare_provider"})
    assert health.failure_count == 1 and health.last_error


def test_test_provider_unknown_raises_keyerror(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["gw_a"])

    class _Router:
        _providers = {}

    class _Pro:
        _router = _Router()

    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "get_pro", lambda: _Pro())

    with pytest.raises(KeyError):
        ds.test_provider(db_session, "nonexist")


def test_test_provider_xiaoshi_probes_quote_and_history_catalog(db_session, monkeypatch):
    """探针必须探 xiaoshi 真正提供的能力，不能探已退役的逐股票日线端点。

    背景数据墙：小石 v3 把日线/分钟历史改成 catalog + R2 下载契约，
    `/api/v3/data/kline/{code}` 随 `/api/v3/data/*` 整族退役，且
    `stability.legacy_aliases = false` 不保留别名。继续探那个地址会恒报
    `HTTP 404: /api/v3/data/kline/000001`，把「能力已迁移」误报成「连接失败」（L-149）。
    """
    calls: list[str] = []

    class _Client:
        def get_manifest(self):
            calls.append("manifest")
            return {"version": "2026-08-14.1"}

        def get_quant_skill(self, *, version, expected_sha256):
            calls.append(f"skill:{version}:{expected_sha256}")
            return SimpleNamespace(version=version, sha256=expected_sha256)

        def get_quote(self, code):
            calls.append(f"quote:{code}")
            return {"code": code, "price": 10.0, "quote_time": "2026-08-16T15:00:00+08:00"}

        def get_history_catalog(self, **params):
            calls.append(f"history_catalog:{params.get('product')}:{params.get('market')}")
            return {"total": 5845, "count": 1, "items": [{"dataset": "adjustment-factors"}]}

    monkeypatch.setattr("app.services.xiaoshi_client.get_xiaoshi_client", lambda: _Client())
    monkeypatch.setattr(
        ds,
        "get_settings",
        lambda: SimpleNamespace(
            xiaoshi_skill_version="2026-08-14.1",
            xiaoshi_skill_sha256="expected-sha",
        ),
    )

    out = ds.test_provider(db_session, "xiaoshi")

    assert out["ok"] is True
    assert out["details"] == {
        "manifest_version": "2026-08-14.1",
        "quote_time": "2026-08-16T15:00:00+08:00",
        "history_catalog_total": 5845,
        "history_delivery": "catalog+download-session",
    }
    # Skill 契约校验（整份 SKILL.md 下载 + SHA-256）有意不在数据通路探针里：它验证的是
    # Skill 完整性而非数据通路，且重下整份文本是延迟大头。
    assert calls == [
        "manifest",
        "quote:000001",
        "history_catalog:stock:CN",
    ]
    for source_type in ("quote", "kline"):
        health = db_session.get(DataSourceHealth, {"source": "xiaoshi", "source_type": source_type})
        assert health is not None and health.success_count == 1


def test_probe_pro_provider_uses_lightweight_stock_basic_request(monkeypatch):
    captured = {}

    class _Provider:
        def request(self, api_name, *, params=None, fields=""):
            captured.update(api_name=api_name, params=params, fields=fields)
            return {
                "code": 0,
                "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]},
            }

    class _Router:
        _providers = {"promax": _Provider()}

    class _Pro:
        _router = _Router()

    import app.services.tushare_client as tc

    monkeypatch.setattr(tc, "get_pro", lambda: _Pro())

    assert ds._probe_pro_provider("promax") == (True, None)
    assert captured == {
        "api_name": "stock_basic",
        "params": {"ts_code": "000001.SZ"},
        "fields": "ts_code",
    }


def _empty_sources(monkeypatch):
    """让 sync 的 desired（quote/kline/... 七类）全为空，desired_set 为空集。
    这样所有非豁免 config 行都是孤儿，专门测豁免逻辑。"""
    for name in ("_quote_sources", "_kline_sources"):
        monkeypatch.setattr(ds, name, lambda settings: [])
    from app.services import market_auxiliary as ma
    for name in (
        "_capital_flow_sources", "_concept_sector_sources",
        "_dragon_tiger_sources", "_limit_up_sources", "_news_sources",
    ):
        monkeypatch.setattr(ma, name, lambda settings: [])


def test_sync_orphan_cleanup_spares_tushare_provider_rows(db_session, monkeypatch):
    """孤儿清理必须豁免 tushare_provider：set_provider_enabled 写的运行时启停覆盖行
    不在 quote/kline/... 七类 desired 中，若被按孤儿删除，「禁用脏源」会静默回滚。
    """
    _empty_sources(monkeypatch)

    # 预置：一个 provider 覆盖行（模拟 set_provider_enabled 禁用 gw_a）+ 一个孤儿 config
    db_session.add(DataSourceConfig(source="gw_a", source_type="tushare_provider", enabled=False, priority=100))
    db_session.add(DataSourceConfig(source="ghost", source_type="quote", enabled=True, priority=99))
    db_session.commit()

    ds.sync_data_source_configs(db_session)

    # provider 覆盖行保留（豁免）；非 provider 孤儿行被删
    provider_row = db_session.get(DataSourceConfig, {"source": "gw_a", "source_type": "tushare_provider"})
    assert provider_row is not None
    assert provider_row.enabled is False  # 禁用状态未被回滚
    assert db_session.get(DataSourceConfig, {"source": "ghost", "source_type": "quote"}) is None


def test_realtime_quote_disabled_override_blocks_fetch(monkeypatch):
    """realtime_quote 的禁用开关必须被 fetch 消费：禁用时直接降级为空，不发爬虫请求。"""
    import app.services.realtime_quote_client as rq

    called = {"realtime": False}

    def _boom(*a, **k):  # 若走到真实 ts.realtime_quote 会抛
        called["realtime"] = True
        raise AssertionError("禁用后不应调用 ts.realtime_quote")

    import tushare as ts
    monkeypatch.setattr(ts, "realtime_quote", _boom)
    monkeypatch.setattr(rq, "_enabled_override", lambda: False)

    rows = rq.fetch_realtime_quotes(["000001"], use_cache=False)
    assert rows == {}
    assert called["realtime"] is False


def test_realtime_quote_health_reads_disabled_override(monkeypatch):
    """get_realtime_quote_health 的 enabled/status 必须反映 DB 覆盖，而非只看 token。"""
    import app.services.realtime_quote_client as rq

    class _S:
        tushare_realtime_quote_token = "fake-token"  # token 在 → configured=True，但运行时禁用
    monkeypatch.setattr(rq, "get_settings", lambda: _S())
    monkeypatch.setattr(rq, "_enabled_override", lambda: False)

    health = rq.get_realtime_quote_health()
    assert health["enabled"] is False
    assert health["configured"] is True
    assert health["status"] == "disabled"


def test_provider_health_only_projects_configured_sources(monkeypatch):
    """管理页只展示当前配置的 Provider，旧 Key 为空时不能显示为 disabled。"""
    import app.services.realtime_quote_client as rq
    import app.services.tushare_client as tc

    class _Settings:
        tushare_promax_api_key = "promax-key"
        tushare_promax_rate_limit_per_minute = 180
        tushare_promax_max_concurrency = 4
        tushare_realtime_quote_token = ""
        tushare_quantdata_token = "quantdata"
        tushare_quantdata_rate_limit_per_minute = 120

    class _Pro:
        def provider_health_snapshot(self):
            return []

    settings = _Settings()
    monkeypatch.setattr(tc, "get_settings", lambda: settings)
    monkeypatch.setattr(tc, "_provider_enabled_override", lambda: {"promax": False})
    monkeypatch.setattr(tc, "_provider_priority_override", lambda: {})
    monkeypatch.setattr(tc, "get_pro", lambda: _Pro())
    monkeypatch.setattr(rq, "get_settings", lambda: settings)
    monkeypatch.setattr(rq, "_enabled_override", lambda: True)

    rows = tc.get_tushare_provider_health()
    by_name = {row["provider_name"]: row for row in rows}

    assert set(by_name) == {"promax", "quantdata"}
    assert by_name["promax"]["configured"] is True
    assert by_name["promax"]["enabled"] is False


def test_list_data_sources_excludes_provider_history(db_session, monkeypatch):
    """历史 tushare_provider 配置/健康行不应污染逻辑数据源列表。"""
    _empty_sources(monkeypatch)
    db_session.add(DataSourceConfig(source="gw_a", source_type="tushare_provider", enabled=False, priority=0))
    db_session.add(DataSourceHealth(source="gw_a", source_type="tushare_provider"))
    db_session.commit()

    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "get_tushare_provider_health", lambda: [])

    rows = ds.list_data_sources(db_session)
    assert all(row["source_type"] != "tushare_provider" for row in rows)
    assert all(row["source"] != "gw_a" for row in rows)


def test_management_sources_include_xiaoshi_market_source(db_session, monkeypatch):
    db_session.add_all(
        [
            DataSourceConfig(source="xiaoshi", source_type="quote", enabled=True, priority=0),
            DataSourceConfig(source="xiaoshi", source_type="kline", enabled=True, priority=0),
            DataSourceHealth(
                source="xiaoshi",
                source_type="quote",
                success_count=3,
                failure_count=1,
                latency_ms=800.0,
                last_success_at=datetime(2026, 8, 16, 10, 0, 0),
            ),
            DataSourceHealth(
                source="xiaoshi",
                source_type="kline",
                success_count=2,
                failure_count=0,
                latency_ms=900.0,
                last_success_at=datetime(2026, 8, 16, 10, 1, 0),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(ds, "sync_data_source_configs", lambda _db: None)
    monkeypatch.setattr(
        ds,
        "_provider_health_rows",
        lambda: [{"provider_name": "promax", "source": "promax", "priority": 0}],
    )
    monkeypatch.setattr(
        ds,
        "get_settings",
        lambda: SimpleNamespace(xiaoshi_api_key="configured", xiaoshi_max_qps=30),
    )

    rows = ds.list_management_sources(db_session)
    xiaoshi = next(row for row in rows if row["provider_name"] == "xiaoshi")

    assert xiaoshi["source_type"] == "market_data_provider"
    assert xiaoshi["configured"] is True
    assert xiaoshi["enabled"] is True
    assert xiaoshi["status"] == "healthy"
    assert xiaoshi["success_count"] == 5
    assert xiaoshi["failure_count"] == 1
    assert xiaoshi["latency_ms"] == 900.0
    assert "分钟历史:R2" in xiaoshi["apis"]
    assert not any("全市场快照" in api for api in xiaoshi["apis"])  # 能力已声明不可用（L-155）


def test_set_provider_enabled_toggles_xiaoshi_quote_and_kline(db_session, monkeypatch):
    db_session.add_all(
        [
            DataSourceConfig(source="xiaoshi", source_type="quote", enabled=True, priority=0),
            DataSourceConfig(source="xiaoshi", source_type="kline", enabled=True, priority=0),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(ds, "sync_data_source_configs", lambda _db: None)
    monkeypatch.setattr(
        ds,
        "get_settings",
        lambda: SimpleNamespace(xiaoshi_api_key="configured", xiaoshi_max_qps=30),
    )

    result = ds.set_provider_enabled(db_session, "xiaoshi", False)

    assert result is not None
    assert result["provider_name"] == "xiaoshi"
    assert result["enabled"] is False
    for source_type in ("quote", "kline"):
        row = db_session.get(
            DataSourceConfig,
            {"source": "xiaoshi", "source_type": source_type},
        )
        assert row is not None and row.enabled is False


def test_admin_management_api_uses_unified_source_projection(db_session, monkeypatch):
    from app.api import admin

    expected = [{"provider_name": "xiaoshi"}]
    monkeypatch.setattr(ds, "list_management_sources", lambda _db: expected)

    assert admin.list_data_sources_api(db_session) == {"providers": expected}

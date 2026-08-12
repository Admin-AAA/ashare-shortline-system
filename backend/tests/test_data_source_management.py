"""数据源管理（provider 启停 + 测试探针）单测。"""

from __future__ import annotations

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
    _stub_providers(monkeypatch, ["xiaodefa"])
    monkeypatch.setattr(
        ds, "_provider_health_rows",
        lambda: [{"provider_name": "xiaodefa", "enabled": False, "status": "disabled"}],
    )
    # 避免触碰真实 provider 缓存
    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "invalidate_provider_enabled_override", lambda: None)

    out = ds.set_provider_enabled(db_session, "xiaodefa", False)
    assert out is not None and out["provider_name"] == "xiaodefa"

    row = db_session.get(DataSourceConfig, {"source": "xiaodefa", "source_type": "tushare_provider"})
    assert row is not None and row.enabled is False


def test_set_provider_enabled_unknown_returns_none(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["fastapic"])
    out = ds.set_provider_enabled(db_session, "nonexist", True)
    assert out is None
    # 未接入不应落库
    assert db_session.get(DataSourceConfig, {"source": "nonexist", "source_type": "tushare_provider"}) is None


def test_set_provider_enabled_toggles_existing_row(db_session, monkeypatch):
    _stub_providers(monkeypatch, ["fastapic"])
    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "invalidate_provider_enabled_override", lambda: None)
    monkeypatch.setattr(ds, "_provider_health_rows", lambda: [{"provider_name": "fastapic", "enabled": True}])

    db_session.add(DataSourceConfig(source="fastapic", source_type="tushare_provider", enabled=True, priority=0))
    db_session.commit()
    ds.set_provider_enabled(db_session, "fastapic", False)
    row = db_session.get(DataSourceConfig, {"source": "fastapic", "source_type": "tushare_provider"})
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
    _stub_providers(monkeypatch, ["fastapic"])

    class _Router:
        _providers = {}

    class _Pro:
        _router = _Router()

    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "get_pro", lambda: _Pro())

    with pytest.raises(KeyError):
        ds.test_provider(db_session, "nonexist")


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

    # 预置：一个 provider 覆盖行（模拟 set_provider_enabled 禁用 fastapic）+ 一个孤儿 config
    db_session.add(DataSourceConfig(source="fastapic", source_type="tushare_provider", enabled=False, priority=100))
    db_session.add(DataSourceConfig(source="ghost", source_type="quote", enabled=True, priority=99))
    db_session.commit()

    ds.sync_data_source_configs(db_session)

    # provider 覆盖行保留（豁免）；非 provider 孤儿行被删
    provider_row = db_session.get(DataSourceConfig, {"source": "fastapic", "source_type": "tushare_provider"})
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
        tushare_fastapic_token = "fake-token"  # token 在 → configured=True，但运行时禁用
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
        tushare_ai_tool_key1 = "key-1"
        tushare_ai_tool_key2 = "key-2"
        tushare_ai_tool_api_key = ""
        tushare_ai_tool_proxy = ""
        tushare_ai_tool_key1_rate_limit_per_minute = 90
        tushare_ai_tool_key2_rate_limit_per_minute = 90
        tushare_ai_tool_key1_rt_min_rate_limit_per_minute = 72
        tushare_ai_tool_key2_rt_min_rate_limit_per_minute = 72
        tushare_ai_tool_key1_stk_mins_rate_limit_per_minute = 48
        tushare_ai_tool_key2_stk_mins_rate_limit_per_minute = 48
        tushare_ai_tool_rate_limit_per_minute = 90
        tushare_ai_tool_rt_min_rate_limit_per_minute = 72
        tushare_ai_tool_stk_mins_rate_limit_per_minute = 48
        tushare_fastapic_token = ""
        tushare_fastapic_rate_limit_per_minute = 30
        tushare_fastapic_max_concurrency = 1
        tushare_xiaodefa_token = ""
        tushare_xiaodefa_rate_limit_per_minute = 60
        tushare_official_token = "official"
        tushare_official_rate_limit_per_minute = 120
        tushare_quantdata_token = "quantdata"
        tushare_quantdata_rate_limit_per_minute = 120

    class _Pro:
        def provider_health_snapshot(self):
            return []

    settings = _Settings()
    monkeypatch.setattr(tc, "get_settings", lambda: settings)
    monkeypatch.setattr(tc, "_provider_enabled_override", lambda: {"ai_tool_key2": False})
    monkeypatch.setattr(tc, "_provider_priority_override", lambda: {})
    monkeypatch.setattr(tc, "get_pro", lambda: _Pro())
    monkeypatch.setattr(rq, "get_settings", lambda: settings)
    monkeypatch.setattr(rq, "_enabled_override", lambda: True)

    rows = tc.get_tushare_provider_health()
    by_name = {row["provider_name"]: row for row in rows}

    assert set(by_name) == {"ai_tool_key1", "ai_tool_key2", "tushare_official", "quantdata"}
    assert by_name["ai_tool_key2"]["configured"] is True
    assert by_name["ai_tool_key2"]["enabled"] is False


def test_list_data_sources_excludes_provider_history(db_session, monkeypatch):
    """历史 tushare_provider 配置/健康行不应污染逻辑数据源列表。"""
    _empty_sources(monkeypatch)
    db_session.add(DataSourceConfig(source="fastapic", source_type="tushare_provider", enabled=False, priority=0))
    db_session.add(DataSourceHealth(source="fastapic", source_type="tushare_provider"))
    db_session.commit()

    import app.services.tushare_client as tc
    monkeypatch.setattr(tc, "get_tushare_provider_health", lambda: [])

    rows = ds.list_data_sources(db_session)
    assert all(row["source_type"] != "tushare_provider" for row in rows)
    assert all(row["source"] != "fastapic" for row in rows)

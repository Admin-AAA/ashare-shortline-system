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

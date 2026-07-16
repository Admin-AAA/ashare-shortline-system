"""S2.3 生产因子注册表装配：显式 bootstrap 与启动顺序。"""
from __future__ import annotations

import pytest

from app.services.factors import (
    FactorError,
    FactorRegistry,
    bootstrap_default_factor_registry,
    default_registry,
)
from app.services.factors import driver


def test_unfrozen_default_registry_is_rejected_without_lazy_bootstrap(monkeypatch):
    """生产默认 Registry 未冻结时，驱动必须明确失败而非暗中装配。"""
    monkeypatch.setattr(driver, "default_registry", FactorRegistry())

    with pytest.raises(FactorError, match="bootstrap"):
        driver.run_factors([])


def test_explicit_bootstrap_makes_default_registry_runnable():
    """显式 bootstrap 后，默认 Registry 可作为生产执行来源。"""
    plan = bootstrap_default_factor_registry()

    assert default_registry.is_frozen is True
    assert plan is default_registry.execution_plan
    assert driver.run_factors([]) == []


def test_startup_bootstraps_factors_after_logging_before_database(monkeypatch):
    """启动顺序受控验证：在替身 init_db 入口处终止，绝不访问数据库。"""
    from app import main

    events: list[str] = []

    class StopBeforeDatabase(BaseException):
        pass

    monkeypatch.setattr(main, "setup_logging", lambda: events.append("setup_logging"))
    monkeypatch.setattr(
        main,
        "bootstrap_default_factor_registry",
        lambda: events.append("bootstrap_factors"),
    )

    def stop_before_database() -> None:
        events.append("init_db")
        raise StopBeforeDatabase()

    monkeypatch.setattr(main, "init_db", stop_before_database)

    with pytest.raises(StopBeforeDatabase):
        main.on_startup()

    assert events == ["setup_logging", "bootstrap_factors", "init_db"]


def test_startup_propagates_factor_bootstrap_failure_before_database(monkeypatch):
    """因子装配异常位于数据库降级边界之外，必须直接向上传播。"""
    from app import main

    error = FactorError("factor bootstrap failed")
    database_called = False

    monkeypatch.setattr(main, "setup_logging", lambda: None)

    def fail_factor_bootstrap() -> None:
        raise error

    def should_not_connect_database() -> None:
        nonlocal database_called
        database_called = True

    monkeypatch.setattr(main, "bootstrap_default_factor_registry", fail_factor_bootstrap)
    monkeypatch.setattr(main, "init_db", should_not_connect_database)

    with pytest.raises(FactorError, match="factor bootstrap failed"):
        main.on_startup()

    assert database_called is False

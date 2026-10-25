import logging

import pytest

from app.agents.circuit_breaker import CircuitBreaker
from app.core import alerts


@pytest.fixture()
def caplog(caplog):
    # 其它测试可能把 ashare.alerts 的 propagate/disabled 改掉；这里直接把 caplog handler 挂到该 logger。
    lg = logging.getLogger("ashare.alerts")
    old = (lg.disabled, lg.propagate)
    lg.disabled = False
    lg.propagate = False
    lg.addHandler(caplog.handler)
    yield caplog
    lg.removeHandler(caplog.handler)
    lg.disabled, lg.propagate = old


def test_emit_alert_dedup(caplog):
    alerts.reset_alerts()
    with caplog.at_level(logging.ERROR, logger="ashare.alerts"):
        assert alerts.emit_alert("k", "x", "msg")
        assert not alerts.emit_alert("k", "x", "msg")
        assert alerts.emit_alert("k", "y", "msg")
    assert sum("[ALERT:k]" in r.getMessage() for r in caplog.records) == 2


def test_llm_permanent_error_emits_alert(caplog):
    alerts.reset_alerts()
    cb = CircuitBreaker()
    with caplog.at_level(logging.ERROR, logger="ashare.alerts"):
        cb.record_result("deepseek-chat", False, error_type="insufficient_balance")
    assert any("[ALERT:llm_circuit_open]" in r.getMessage() for r in caplog.records)


def test_llm_transient_below_threshold_no_alert(caplog):
    alerts.reset_alerts()
    cb = CircuitBreaker(failure_threshold=3)
    with caplog.at_level(logging.ERROR, logger="ashare.alerts"):
        cb.record_result("m", False, error_type="timeout")
    assert not caplog.records


def test_alert_registry_emit_resolve_and_active_records():
    from app.core import alerts

    alerts.reset_alerts()
    assert alerts.active_alert_records() == []
    alerts.emit_alert("data_freshness", "capital_flow", "滞后 1 个交易日")
    alerts.emit_alert("data_freshness", "capital_flow", "滞后 1 个交易日")  # 冷却内重复：不新增也不丢
    alerts.emit_alert("task_status", "t1", "partial_failed")
    recs = alerts.active_alert_records("data_freshness")
    assert [(r["kind"], r["key"]) for r in recs] == [("data_freshness", "capital_flow")]
    assert alerts.resolve_alert("data_freshness", "capital_flow", "ok") is True
    assert alerts.resolve_alert("data_freshness", "capital_flow", "ok") is False  # 已解除：不重复记
    assert alerts.active_alert_records("data_freshness") == []
    assert [r["key"] for r in alerts.active_alert_records()] == ["t1"]
    # 解除后同 key 再次告警不受旧冷却影响
    assert alerts.emit_alert("data_freshness", "capital_flow", "再次滞后") is True
    alerts.reset_alerts()

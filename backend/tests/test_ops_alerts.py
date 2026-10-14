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

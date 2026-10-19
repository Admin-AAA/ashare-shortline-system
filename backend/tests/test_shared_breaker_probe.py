"""Unified shared breaker: 5-min cooldown, single cross-process half-open probe, per-namespace."""
import time

import app.core.shared_breaker as sb


def _store(monkeypatch, tmp_path, ns="live"):
    import app.core.metrics as m

    monkeypatch.setattr(m, "_process_role", lambda: "backend")
    monkeypatch.setattr(m, "_store_path", lambda: tmp_path / "rt.sqlite")
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, ns)
    sb._cache.clear()


def _expire(key):
    conn = sb._conn()
    with conn:
        conn.execute("UPDATE breaker_state SET open_until=? WHERE key=?", (time.time() - 1, key))
    conn.close()
    sb._cache.clear()


def test_cooldown_is_at_least_five_minutes(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    a = sb.SharedCircuitBreaker("gw:p:d", 1, 60.0)
    a.record_failure()
    left = sb.shared_open_until(a.key) - time.time()
    assert 290 < left <= 300.5 and a.allow_request() is False


def test_single_probe_across_processes_then_close(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    a = sb.SharedCircuitBreaker("gw:p:d", 1, 300.0)
    b = sb.SharedCircuitBreaker("gw:p:d", 1, 300.0)  # another process
    a.record_failure()
    _expire(a.key)
    first, second = a.allow_request(), (sb._cache.clear() or b.allow_request())
    assert (first, second) == (True, False)
    a.record_success()
    sb._cache.clear()
    assert b.allow_request() is True and sb.shared_breaker_states() == []


def test_probe_failure_reopens_five_minutes(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    a = sb.SharedCircuitBreaker("gw:p:d", 3, 300.0)
    a.record_failure(force_open=True)
    _expire(a.key)
    assert a.allow_request() is True
    a.record_failure()  # probe failed (below threshold but still re-opens)
    sb._cache.clear()
    assert sb.shared_open_until(a.key) - time.time() > 290
    assert a.allow_request() is False


def test_namespaces_independent(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path, "live")
    live = sb.SharedCircuitBreaker("gw:p:d", 1, 300.0)
    live.record_failure()
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "research")
    sb._cache.clear()
    research = sb.SharedCircuitBreaker("gw:p:d", 1, 300.0)
    assert research.key == "research:gw:p:d" and research.allow_request() is True
    assert live.allow_request() is False


def test_gateway_uses_shared_breaker(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    from app.services.datasource.gateway import DatasetGateway

    class P:
        name = "x"
        config_keys = ()

        def supports(self, d, q):
            return True

        def fetch(self, d, q):
            raise RuntimeError("t")

    gw = DatasetGateway([P()], enabled=lambda p: True, failure_threshold=1)
    gw.fetch_first("ds", {})
    sb._cache.clear()
    other = DatasetGateway([P()], enabled=lambda p: True, failure_threshold=1)  # another process
    res = other.fetch_first("ds", {})
    assert res.attempts == [{"provider": "x", "outcome": "circuit_open"}]

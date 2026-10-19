import time

import app.core.shared_breaker as sb


def _enable_store(monkeypatch, tmp_path):
    import app.core.metrics as m

    monkeypatch.setattr(m, "_process_role", lambda: "backend")
    monkeypatch.setattr(m, "_store_path", lambda: tmp_path / "rt.sqlite")
    sb._cache.clear()


def test_open_in_one_process_blocks_another(monkeypatch, tmp_path):
    _enable_store(monkeypatch, tmp_path)
    a = sb.SharedCircuitBreaker("gw:x:y", 2, 60.0)
    b = sb.SharedCircuitBreaker("gw:x:y", 2, 60.0)  # stands in for another process
    a.record_failure()
    a.record_failure()
    assert a.state == "open"
    sb._cache.clear()  # b has its own cache in a real process
    assert b.allow_request() is False and b.state == "open"
    assert sb.shared_breaker_states()[0]["key"] == "gw:x:y"
    a._local.record_success()
    a.record_success()
    sb._cache.clear()
    assert b.allow_request() is True


def test_degrades_to_local_under_pytest(monkeypatch):
    sb._cache.clear()
    br = sb.SharedCircuitBreaker("gw:p:q", 1, 60.0)
    br.record_failure()
    assert br.allow_request() is False
    other = sb.SharedCircuitBreaker("gw:p:q", 1, 60.0)
    assert other.allow_request() is True  # no shared store in pytest role


def test_expired_shared_open_allows(monkeypatch, tmp_path):
    _enable_store(monkeypatch, tmp_path)
    sb._publish_open("k", time.time() - 1)
    sb._cache.clear()
    assert sb.SharedCircuitBreaker("k", 1, 1.0).allow_request() is True

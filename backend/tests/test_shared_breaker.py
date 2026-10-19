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
    assert sb.shared_breaker_states()[0]["key"].endswith(":gw:x:y")
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


def test_research_process_never_blocks_live(monkeypatch, tmp_path):
    _enable_store(monkeypatch, tmp_path)
    monkeypatch.delenv(sb.BREAKER_NAMESPACE_ENV, raising=False)
    research = sb.SharedCircuitBreaker("gw:promax:daily", 1, 60.0)  # default = research
    assert research.key == "research:gw:promax:daily"
    research.record_failure()
    assert research.state == "open"
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "live")
    sb._cache.clear()
    live = sb.SharedCircuitBreaker("gw:promax:daily", 1, 60.0)
    assert live.key == "live:gw:promax:daily" and live.allow_request() is True


def test_live_processes_share_live_namespace(monkeypatch, tmp_path):
    _enable_store(monkeypatch, tmp_path)
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "")  # registers restore on teardown
    sb.mark_live_process()
    backend = sb.SharedCircuitBreaker("tushare:promax:daily", 1, 60.0)
    backend.record_failure()
    sb._cache.clear()
    assert sb.SharedCircuitBreaker("tushare:promax:daily", 1, 60.0).allow_request() is False
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "anything-else")
    assert sb.breaker_namespace() == "research"


def test_entrypoints_mark_live():
    import inspect

    import app.main as m

    assert "mark_live_process()" in inspect.getsource(m.on_startup)
    from pathlib import Path

    assert "mark_live_process()" in (Path(__file__).parents[1] / "scripts" / "run_prefect_worker.py").read_text()

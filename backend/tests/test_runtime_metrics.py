from fastapi.testclient import TestClient

from app.core import metrics


def test_counters_and_timings():
    metrics.reset()
    metrics.inc("provider_requests", provider="a", outcome="success")
    metrics.inc("provider_requests", provider="a", outcome="success")
    with metrics.timed("llm_latency", client="x"):
        pass
    snap = metrics.snapshot()
    assert snap["counters"][0]["value"] == 2.0
    assert snap["timings"][0]["count"] == 1


def test_metrics_never_raise():
    metrics.inc("x", value="bad")  # type error swallowed
    metrics.observe("y", "bad")


def test_runtime_endpoint():
    from app.main import app
    metrics.reset(); metrics.inc("provider_fallback", api="daily", provider="b", fallback_from="a")
    r = TestClient(app).get("/api/metrics/runtime")
    assert r.status_code == 200
    assert r.json()["counters"][0]["name"] == "provider_fallback"

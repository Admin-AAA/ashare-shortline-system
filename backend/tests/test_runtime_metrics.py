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


def test_metrics_never_raise_and_ignore_bad_values():
    metrics.reset()
    metrics.inc("x", value="bad")
    metrics.observe("y", "bad")
    snap = metrics.snapshot()
    # 坏值既不抛出，也不能留下半条记录（observe 旧实现先加 count 再加 sum，会留 count=1/sum=0）
    assert snap["counters"] == []
    assert snap["timings"] == []


def test_runtime_endpoint():
    from app.main import app
    metrics.reset(); metrics.inc("provider_fallback", api="daily", provider="b", fallback_from="a")
    r = TestClient(app).get("/api/metrics/runtime")
    assert r.status_code == 200
    assert r.json()["counters"][0]["name"] == "provider_fallback"

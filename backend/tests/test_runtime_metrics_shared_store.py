from app.core import metrics


def test_publish_and_read_back_across_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("ASHARE_METRICS_DB", str(tmp_path / "m.sqlite"))
    monkeypatch.setenv("ASHARE_PROCESS_ROLE", "backend-test")
    monkeypatch.setattr(metrics, "_publisher_started", True)  # no background thread in tests
    metrics.reset()
    metrics.inc("demo_total", 2, source="x")
    assert metrics.publish() is True
    snap = metrics.snapshot()
    assert snap["process"]["role"] == "backend-test" and snap["process"]["started_at"]
    assert snap["counters"][0]["value"] == 2.0
    [proc] = snap["processes"]
    assert proc["current"] and proc["counters"][0]["name"] == "demo_total"
    metrics.reset()


def test_disabled_store_never_raises(monkeypatch):
    monkeypatch.setenv("ASHARE_METRICS_DB", "off")
    assert metrics.publish() is False
    assert metrics.snapshot()["processes"] == []


def test_pytest_role_does_not_publish_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("ASHARE_METRICS_DB", str(tmp_path / "m.sqlite"))
    monkeypatch.delenv("ASHARE_PROCESS_ROLE", raising=False)
    assert metrics.publish() is False

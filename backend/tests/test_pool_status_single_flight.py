import threading
import time

from app.api import data_sources as ds


def test_concurrent_cache_miss_computes_once(monkeypatch):
    calls = []

    def fake_status(db, **kwargs):
        calls.append(1)
        time.sleep(0.2)
        return {"unified_quotes": [], "daily_bars": [], "n": len(calls)}

    monkeypatch.setattr(ds, "get_local_pool_status", fake_status)
    monkeypatch.setattr(ds, "_load_paginated", lambda *a, **k: ([], []))
    ds._pool_status_cache.clear()
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(ds.get_cached_local_pool_status(None, include_paginated=False)))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ds._pool_status_cache.clear()
    assert len(calls) == 1
    assert len(results) == 5
    assert all(r["n"] == 1 for r in results)


def test_stale_cache_served_immediately_and_refreshed_in_background(monkeypatch):
    calls = []

    def fake_status(db, **kwargs):
        calls.append(1)
        time.sleep(0.3)
        return {"unified_quotes": [], "daily_bars": [], "n": len(calls)}

    monkeypatch.setattr(ds, "get_local_pool_status", fake_status)
    monkeypatch.setattr(ds, "_load_paginated", lambda *a, **k: ([], []))
    ds._pool_status_cache.clear()
    key = ds._pool_cache_key(True)
    ds._pool_status_cache[key] = {"ts": time.monotonic() - 1000, "data": {"n": 0}}
    started = time.monotonic()
    result = ds.get_cached_local_pool_status(None, include_paginated=False, cache_ttl=300)
    assert time.monotonic() - started < 0.2  # 不等重算
    assert result["n"] == 0 and result["cache_stale"] is True
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and ds._pool_status_cache[key]["data"].get("n") != 1:
        time.sleep(0.05)
    assert ds._pool_status_cache[key]["data"]["n"] == 1
    assert len(calls) == 1
    ds._pool_status_cache.clear()

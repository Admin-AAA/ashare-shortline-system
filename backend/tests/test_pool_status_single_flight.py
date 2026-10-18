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

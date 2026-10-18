import threading
import time

from app.api import admin


def test_single_flight_and_stale_while_revalidate(monkeypatch):
    calls = []

    def fake(db):
        calls.append(1)
        time.sleep(0.2)
        return {"n": len(calls)}

    monkeypatch.setattr("app.services.temporal_consistency.check_run_temporal_consistency", fake)
    admin.invalidate_temporal_consistency_cache()
    out = []
    ts = [threading.Thread(target=lambda: out.append(admin._temporal_consistency_cached(None))) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and all(o == {"n": 1} for o in out)
    # 过期后先返回旧值
    admin._tc_cache["v"] = (time.time() - 60, {"n": 1})
    monkeypatch.setattr(admin, "_tc_background_refresh", lambda: None)
    assert admin._temporal_consistency_cached(None) == {"n": 1}
    admin.invalidate_temporal_consistency_cache()
    assert admin._tc_cache == {}

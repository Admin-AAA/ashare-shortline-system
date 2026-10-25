import threading
import time

import pytest

from app.services.datasource import gateway as gw


def test_timed_out_calls_are_tracked_and_released():
    release = threading.Event()
    with pytest.raises(gw.DataSourceError) as ei:
        gw._call_with_timeout(lambda: release.wait(5), 0.05)
    assert ei.value.kind == gw.ErrorKind.TIMEOUT
    assert gw.abandoned_call_count() >= 1
    release.set()
    deadline = time.monotonic() + 2
    while gw.abandoned_call_count() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert gw.abandoned_call_count() == 0


def test_fast_fail_when_abandoned_limit_reached(monkeypatch):
    monkeypatch.setattr(gw, "ABANDONED_LIMIT", 1)
    release = threading.Event()
    with pytest.raises(gw.DataSourceError):
        gw._call_with_timeout(lambda: release.wait(5), 0.05)
    called = []
    with pytest.raises(gw.DataSourceError) as ei:
        gw._call_with_timeout(lambda: called.append(1), 1.0)
    assert "saturated" in str(ei.value) and not called
    release.set()
    deadline = time.monotonic() + 2
    while gw.abandoned_call_count() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert gw._call_with_timeout(lambda: 7, 1.0) == 7

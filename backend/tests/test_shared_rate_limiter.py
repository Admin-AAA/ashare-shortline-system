import sqlite3
import time

from app.services import tushare_transport as tt
from app.services.shared_rate_limiter import SharedSlidingWindowLimiter


_WORKER = """
import sys, time
sys.path.insert(0, {root!r})
from app.services.shared_rate_limiter import SharedSlidingWindowLimiter
lim = SharedSlidingWindowLimiter("p", 5, {db!r}, window_seconds=2.0, pad_seconds=0.0)
for _ in range(4):
    lim.acquire(); print(repr(time.time()), flush=True)
"""


def test_quota_shared_across_processes(tmp_path):
    import os, subprocess, sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = str(tmp_path / "rl.sqlite")
    code = _WORKER.format(root=root, db=db)
    ps = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True) for _ in range(2)]
    stamps = []
    for p in ps:
        out, _ = p.communicate(timeout=60)
        assert p.returncode == 0
        stamps += [float(x) for x in out.split()]
    stamps.sort()
    assert len(stamps) == 8
    # 8 hits at 5/2s across two processes: the 6th must wait a full window
    for i in range(len(stamps) - 5):
        assert stamps[i + 5] - stamps[i] >= 2.0 - 0.05


def test_same_wait_semantics_as_in_process(tmp_path, monkeypatch):
    clock = [1000.0]; sleeps = []
    monkeypatch.setattr(time, "time", lambda: clock[0])
    def fake_sleep(s):
        sleeps.append(round(s, 6)); clock[0] += s
    monkeypatch.setattr(time, "sleep", fake_sleep)
    lim = SharedSlidingWindowLimiter("k", 1, str(tmp_path / "a.sqlite"))
    lim.acquire(); lim.acquire()
    assert sleeps == [60.01]


def test_fallback_to_in_process_on_sqlite_error(tmp_path, monkeypatch):
    calls = []
    class Fb:
        def acquire(self): calls.append(1)
    lim = SharedSlidingWindowLimiter("k", 1, str(tmp_path / "a.sqlite"), fallback=Fb())
    def boom(): raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(lim, "_try_acquire", boom)
    lim.acquire(); lim.acquire()
    assert calls == [1, 1]


def test_make_limiter_env_switch(tmp_path, monkeypatch):
    monkeypatch.delenv("TUSHARE_SHARED_LIMITER_DB", raising=False)
    assert isinstance(tt._make_limiter("x", 10), tt.SlidingWindowLimiter)
    monkeypatch.setenv("TUSHARE_SHARED_LIMITER_DB", str(tmp_path / "s.sqlite"))
    assert isinstance(tt._make_limiter("x", 10), SharedSlidingWindowLimiter)

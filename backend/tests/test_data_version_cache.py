"""版本化读缓存：数据版本指纹必须随库里任何增删改变化（跨进程可靠），缓存有上限、有兜底过期。"""
from __future__ import annotations

import threading
import time

import pytest

from app.models.market import DailyKbar
from app.models.strategy import Preset
from app.services.data_version import VersionedCache, data_version


def test_cache_recomputes_when_version_changes():
    cache = VersionedCache("t", maxsize=4, max_age=60)
    calls = []
    f = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert cache.get_or_compute("k", "v1", f) == 1
    assert cache.get_or_compute("k", "v1", f) == 1
    assert cache.get_or_compute("k", "v2", f) == 2
    assert len(calls) == 2


def test_cache_max_age_and_lru_bound():
    cache = VersionedCache("t", maxsize=2, max_age=0.05)
    cache.get_or_compute("a", 1, lambda: "a")
    time.sleep(0.06)
    assert cache.get_or_compute("a", 1, lambda: "a2") == "a2"  # 兜底过期
    cache.get_or_compute("b", 1, lambda: "b")
    cache.get_or_compute("c", 1, lambda: "c")
    assert len(cache) == 2  # 超上限按 LRU 淘汰


def test_cache_single_flight_per_key():
    cache = VersionedCache("t", maxsize=4, max_age=60)
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.1)
        return "x"

    ths = [threading.Thread(target=cache.get_or_compute, args=("k", 1, slow)) for _ in range(5)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert len(calls) == 1


@pytest.mark.integration
def test_fingerprint_sees_insert_update_and_null(db):
    v0 = data_version(db, ["presets"])
    db.add(Preset(preset_id="zz_perf_probe", name="probe"))
    db.flush()
    v1 = data_version(db, ["presets"])
    assert v1 != v0  # 新增
    p = db.get(Preset, "zz_perf_probe")
    p.description = ""
    db.flush()
    v2 = data_version(db, ["presets"])
    assert v2 != v1  # NULL -> 空串 也要能发现
    p.name = "probe2"
    db.flush()
    assert data_version(db, ["presets"]) != v2  # 原地改写


@pytest.mark.integration
def test_fingerprint_sees_new_daily_bar(db):
    v0 = data_version(db, ["daily_kbars"])
    db.add(DailyKbar(code="999998", trade_date="2099-01-01", open=1, high=1, low=1, close=1))
    db.flush()
    assert data_version(db, ["daily_kbars"]) != v0

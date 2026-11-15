"""回放激励公告的本地离线读取器回归测试。

背景：R12 曾因 midterm_incentive.fetch_range 每天打在线网关查历史区间，
271/271 个交易日全记 no_data，两个现役期权策略的近 12 个月指标实际是空的。
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.services import midterm_incentive as mi
from scripts import rebuild_replay_picks as rp


def _row(code: str, day: str, title: str = "某激励计划（草案）", ann_id: str | None = None) -> dict:
    return {"code": code, "name": "测试", "ann_ms": 0, "ann_date": day,
            "title": title, "ann_id": ann_id or f"{code}-{day}-{title}"}


def _write(directory, filename: str, rows: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=mi.COLS).to_parquet(directory / filename, index=False)


@pytest.fixture
def incentive_dirs(tmp_path, monkeypatch):
    hist, live = tmp_path / "hist", tmp_path / "live"
    monkeypatch.setattr(mi, "HIST_DIR", hist)
    monkeypatch.setattr(mi, "LIVE_DIR", live)
    return hist, live


def test_serves_covered_range_without_calling_api(incentive_dirs):
    hist, live = incentive_dirs
    _write(hist, "202501.parquet", [_row("000001", "20250110"), _row("000002", "20250120")])
    _write(live, "incentive_live_2026.parquet", [_row("000003", "20260210")])
    called = []

    def api(start, end, max_pages=60):
        called.append((start, end))
        return None

    fetch = rp._local_incentive_reader(api)
    assert [r["code"] for r in fetch("2025-01-01", "2025-01-31")] == ["000001", "000002"]
    # end 必须落在本地覆盖内（covered_through=20260210），与回放 as_of 的语义一致
    assert [r["code"] for r in fetch("2026-01-01", "2026-02-10")] == ["000003"]
    assert called == []


def test_pit_does_not_leak_future_announcements(incentive_dirs):
    hist, live = incentive_dirs
    _write(hist, "202501.parquet", [_row("000001", "20250110"), _row("000002", "20250120")])
    fetch = rp._local_incentive_reader(lambda *a, **k: pytest.fail("should not call api"))
    assert [r["code"] for r in fetch("2025-01-01", "2025-01-15")] == ["000001"]


def test_falls_back_to_api_when_end_exceeds_local_coverage(incentive_dirs):
    hist, _ = incentive_dirs
    _write(hist, "202501.parquet", [_row("000001", "20250110")])
    seen = []

    def api(start, end, max_pages=60):
        seen.append((start, end, max_pages))
        return [{"code": "FROM_API"}]

    fetch = rp._local_incentive_reader(api)
    assert fetch("2025-01-01", "2026-12-31", 30) == [{"code": "FROM_API"}]
    assert seen == [("2025-01-01", "2026-12-31", 30)]


def test_no_local_cache_keeps_original_fetch(incentive_dirs):
    api = lambda start, end, max_pages=60: []  # noqa: E731
    assert rp._local_incentive_reader(api) is api


def test_deduplicates_hist_and_live_overlap(incentive_dirs):
    hist, live = incentive_dirs
    _write(hist, "202601.parquet", [_row("000001", "20260110", ann_id="X")])
    _write(live, "incentive_live_2026.parquet", [_row("000001", "20260110", ann_id="X")])
    fetch = rp._local_incentive_reader(lambda *a, **k: pytest.fail("should not call api"))
    assert len(fetch("2026-01-01", "2026-01-10")) == 1


def test_install_offline_incentive_restores_globals_and_disables_live_write(incentive_dirs):
    hist, live = incentive_dirs
    _write(hist, "202501.parquet", [_row("000001", "20250110")])
    orig_fetch, orig_hist, orig_store = mi.fetch_range, mi._history_dates, mi._store_live
    restore = rp.install_offline_incentive()
    try:
        assert mi.fetch_range is not orig_fetch
        assert mi._history_dates is not orig_hist
        assert mi.fetch_range("2025-01-01", "2025-01-10")[0]["code"] == "000001"
        mi._store_live([_row("000009", "20250110")])  # 回放不得污染实盘事件库
        assert list(live.glob("*.parquet")) == []
    finally:
        restore()
    assert mi.fetch_range is orig_fetch
    assert mi._history_dates is orig_hist
    assert mi._store_live is orig_store


def test_history_from_store_matches_file_scanning_original(incentive_dirs):
    """内存二分必须与原 _history_dates 的 groupby max 逐日等价（含 BAD 标题过滤）。"""
    hist, live = incentive_dirs
    _write(hist, "202501.parquet", [
        _row("000001", "20250110", "股权激励计划（草案）"),
        _row("000001", "20250315", "股权激励计划（草案）"),
    ])
    _write(live, "incentive_live_2025.parquet", [
        _row("000001", "20250401", "股权激励计划（草案）修订"),  # 修订不算草案
        _row("000002", "20250220", "股权激励计划（草案）"),
    ])
    store = rp._load_incentive_store()
    local = rp._incentive_history_from_store(store)
    codes = ["000001", "000002", "000003"]
    for before in ("20250110", "20250220", "20250401", "20260101"):
        assert local(codes, before) == mi._history_dates(codes, before), before
    assert local(codes, "20260101")["000001"] == "20250315"

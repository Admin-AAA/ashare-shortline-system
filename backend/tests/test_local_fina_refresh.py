"""本地逐股财务指标库增量刷新 + rolling_r12 覆盖日判断 + 巡检项（2026-11-05：10-17 后没人更新，三季报只 47 只）。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from app.services import data_quality_monitor as m
from app.services import local_fina_refresh as lfr
from app.services import rolling_r12 as rr


def _write(d, code, rows):
    pd.DataFrame(rows).to_parquet(d / f"{code}.parquet", index=False)


@pytest.fixture
def fdir(tmp_path):
    d = tmp_path / "fina"
    d.mkdir()
    _write(d, "000001", [
        {"ts_code": "000001.SZ", "ann_date": "20260815", "end_date": "20260630", "profit_dedt": "100.0", "eps": 1.0},
        {"ts_code": "000001.SZ", "ann_date": "20260820", "end_date": "20260630", "profit_dedt": "101.0", "eps": 1.1},
    ])
    _write(d, "600000", [{"ts_code": "600000.SH", "ann_date": "20260828", "end_date": "20260630",
                          "profit_dedt": "5.0", "eps": 0.2}])
    return d


def _fetcher(frames, fail=()):
    calls = []

    def fetch(p, cols):
        calls.append((p, tuple(cols)))
        if p in fail:
            raise RuntimeError("gateway down")
        return frames.get(p, pd.DataFrame(columns=["ts_code", "ann_date", "end_date"]))
    fetch.calls = calls
    return fetch


Q3 = pd.DataFrame([
    {"ts_code": "000001.SZ", "ann_date": "20261024", "end_date": "20260930", "profit_dedt": 150.0, "eps": 1.5},
    {"ts_code": "600000.SH", "ann_date": "20261030", "end_date": "20260930", "profit_dedt": 8.0, "eps": 0.3},
    {"ts_code": "300001.SZ", "ann_date": "20261028", "end_date": "20260930", "profit_dedt": 3.0, "eps": 0.1},
    {"ts_code": "830001.BJ", "ann_date": "20261028", "end_date": "20260930", "profit_dedt": 1.0, "eps": 0.1},
    {"ts_code": "600000.SH", "ann_date": None, "end_date": "20260930", "profit_dedt": 9.0, "eps": 0.3},
])
H1 = pd.DataFrame([  # 网关给的半年报：000001 同一公告版本数值略不同 + 少了一版更正；600000 多了一版更正
    {"ts_code": "000001.SZ", "ann_date": "20260815", "end_date": "20260630", "profit_dedt": 100.4, "eps": 1.0},
    {"ts_code": "600000.SH", "ann_date": "20260828", "end_date": "20260630", "profit_dedt": 5.0, "eps": 0.2},
    {"ts_code": "600000.SH", "ann_date": "20261010", "end_date": "20260630", "profit_dedt": 5.5, "eps": 0.2},
])


def test_append_only_keeps_history_and_adds_new_versions(fdir):
    out = lfr.refresh(periods=["20260930", "20260630"], fina_dir=fdir, today="20261105", fetch=_fetcher({"20260930": Q3, "20260630": H1}))
    assert out["status"] == "completed" and out["covered_through"] == "20261105"
    a = pd.read_parquet(fdir / "000001.parquet")
    h1 = a[a.end_date == "20260630"].set_index("ann_date")
    assert sorted(h1.index) == ["20260815", "20260820"]  # 旧更正版没被删
    assert h1.at["20260815", "profit_dedt"] == 100.0  # 旧版本数值不改写
    assert (a.end_date == "20260930").sum() == 1
    b = pd.read_parquet(fdir / "600000.parquet")
    assert sorted(b[b.end_date == "20260630"].ann_date) == ["20260828", "20261010"]
    assert sorted(b[b.end_date == "20260930"].ann_date) == ["20261030"]  # 空公告日的行丢掉
    assert (fdir / "300001.parquet").exists() and not (fdir / "830001.parquet").exists()  # 新股建文件，北交所不建
    assert out["files_created"] == 1 and out["rows_dropped_bad_ann_date"] == 1
    assert json.loads((fdir / "_manifest.json").read_text())["covered_through"] == "20261105"
    # 再跑一次：没有新版本，一个文件都不写
    again = lfr.refresh(periods=["20260930", "20260630"], fina_dir=fdir, today="20261105",
                        fetch=_fetcher({"20260930": Q3, "20260630": H1}))
    assert again["files_written"] == 0


def test_failed_period_does_not_advance_manifest(fdir):
    lfr._atomic_write_json(fdir / "_manifest.json", {"covered_through": "20261030"})
    out = lfr.refresh(periods=["20260930", "20260630"], fina_dir=fdir, today="20261105",
                      fetch=_fetcher({"20260930": Q3}, fail={"20260630"}))
    assert out["status"] == "partial_failed" and out["failed_periods"] == ["20260630"]
    assert lfr.load_manifest(fdir)["covered_through"] == "20261030"


def test_shrinking_period_is_refused(fdir):
    tiny = H1[H1.ts_code == "600000.SH"].iloc[:1]  # 本地该期 2 只，网关只给 1 只
    out = lfr.refresh(periods=["20260630"], fina_dir=fdir, today="20261105", fetch=_fetcher({"20260630": tiny}))
    assert out["periods"]["20260630"]["status"] == "refused_shrink" and out["status"] == "partial_failed"
    assert not (fdir / "_manifest.json").exists()


def test_quarter_ends():
    assert lfr.quarter_ends("20261105") == ["20260930", "20260630", "20260331", "20251231"]
    assert lfr.quarter_ends("20260331", 2) == ["20260331", "20251231"]


def test_covered_through_ignores_null_ann_and_prefers_manifest(tmp_path):
    df = pd.DataFrame({"ann_date": ["20261017", "None", "nan", "20261001"]})
    assert rr._covered_through("fina", df, tmp_path) == "20261017"  # 旧实现会得到 "nan"
    (tmp_path / "fina").mkdir()
    lfr._atomic_write_json(tmp_path / "fina" / "_manifest.json", {"covered_through": "20261105"})
    assert rr._covered_through("fina", df, tmp_path) == "20261105"
    assert rr._covered_through("dividend", df, tmp_path) == "20261017"


def test_local_fina_item_severity():
    db = {"20260930": {f"{i:06d}" for i in range(5000)}}
    stale = m.local_fina_item(["20260930"], db, {"20260930": {"000001"}}, None, None)
    assert stale["severity"] == m.RED and "比库少 4999 只" in stale["detail"]
    ok = m.local_fina_item(["20260930"], db, {"20260930": set(db["20260930"])}, "20261105", 0)
    assert ok["severity"] == m.GREEN
    lagged = m.local_fina_item(["20260930"], db, {"20260930": set(db["20260930"])}, "20261103", 2)
    assert lagged["severity"] == m.RED


def test_scheduler_hook_skips_in_tests():
    from app.services import scheduler_service as ss

    assert ss._refresh_local_fina("postclose_late_data_repair") == {"status": "skipped", "reason": "pytest"}

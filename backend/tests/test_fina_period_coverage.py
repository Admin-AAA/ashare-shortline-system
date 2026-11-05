"""按报告期财报覆盖率：快照记录 + 数据质量巡检项。"""
from __future__ import annotations

import pandas as pd

from app.services import data_quality_monitor as m
from app.services.datasource import fina_coverage as fc


def test_due_report_periods():
    assert m.due_report_periods("20261105") == ["20260930", "20260630", "20260331"]
    assert m.due_report_periods("20261031") == ["20260630", "20260331", "20251231"]
    assert m.due_report_periods("20260501") == ["20260331", "20251231", "20250930"]
    assert m.report_deadline("20251231") == "20260430"


def test_record_keeps_tradable_codes_and_cap_flag(tmp_path):
    p = tmp_path / "cov.json"
    df = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(9998)] + ["830001.BJ", "920001.BJ"]})
    fc.record("20260630", df, source="gateway", path=p)
    e = fc.load(p)["20260630"]
    assert e["rows"] == 10000 and e["row_cap_multiple"] is True
    assert e["tradable_codes"] == 9998 and "830001" not in e["tradable"]
    for i in range(10):
        fc.record(f"2020{i:02d}31", df.head(3), source="gateway", path=p)
    assert len(fc.load(p)) == fc.KEEP_PERIODS


def test_record_never_raises(tmp_path):
    fc.record("20260630", None, source="gateway", path=tmp_path / "a" / "b.json")  # 空表也不抛


def _db_codes(n, start=600000):
    return {f"{start + i:06d}" for i in range(n)}


def test_item_green_when_gateway_matches_db():
    codes = _db_codes(5000)
    snap = {"20260630": {"tradable": sorted(codes), "rows": 9000, "row_cap_multiple": False}}
    it = m.fina_coverage_item(["20260630"], {"20260630": 5000}, {"20260630": codes}, snap)
    assert it["severity"] == m.GREEN and "比库少 0 只" in it["detail"]


def test_item_red_when_tail_truncated_even_if_ratio_looks_fine():
    codes = _db_codes(5000)
    kept = sorted(codes)[:-119]  # 尾部 119 只被截，覆盖率仍 97.6%
    snap = {"20260630": {"tradable": kept, "rows": 10000, "row_cap_multiple": True, "fetched_at": "x", "source": "gateway"}}
    it = m.fina_coverage_item(["20260630"], {"20260630": 5000}, {"20260630": codes}, snap)
    assert it["severity"] == m.RED and "比库少 119 只" in it["detail"] and "疑似被截断" in it["detail"]


def test_item_yellow_on_moderate_gateway_gap_and_red_on_db_gap():
    codes = _db_codes(5000)
    snap = {"20260630": {"tradable": sorted(codes)[:-20], "rows": 9000, "row_cap_multiple": False}}
    assert m.fina_coverage_item(["20260630"], {"20260630": 5000}, {"20260630": codes}, snap)["severity"] == m.YELLOW
    assert m.fina_coverage_item(["20260630"], {"20260630": 5000}, {"20260630": _db_codes(4000)}, {})["severity"] == m.RED

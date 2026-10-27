"""st_hist 重建：以 DB is_st 为准；校验逻辑（纯 polars，不连库）。"""
import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.midterm.export_data import StHistMismatch, build_st_hist, st_intervals, validate_st_hist  # noqa: E402


def _meta():
    # 000584：退市股，现名无 ST，但 2023-05-05 起 is_st=1（旧逻辑漏标）
    # 000016：现名 *ST，历史 2015 起 is_st=0、2026-04-30 起 is_st=1（旧逻辑整段误标）
    return pl.DataFrame({
        "code": ["000584", "000584", "000016", "000016", "600000"],
        "effective_from": ["2015-01-01", "2023-05-05", "2015-01-01", "2026-04-30", "2015-01-01"],
        "is_st": [0, 1, 0, 1, 0],
    })


def test_build_uses_is_st_not_name():
    s = build_st_hist(_meta())
    d = {(r[0], r[1]): r[2] for r in s.rows()}
    assert d[("000584", "2023-05-05")] is True
    assert d[("000016", "2015-01-01")] is False
    assert d[("000016", "2026-04-30")] is True
    assert s.columns == ["code", "effective_from", "st"]


def test_validate_ok_and_intervals():
    m = _meta()
    stats = validate_st_hist(build_st_hist(m), m)
    assert stats["st_codes_db"] == 2 and stats["only_in_file"] == 0
    iv = st_intervals(build_st_hist(m).rename({"st": "f"}), "f")
    assert iv.filter(pl.col("code") == "000584").rows() == [("000584", "2023-05-05", None)]


def test_validate_raises_on_missing_st():
    m = _meta()
    bad = build_st_hist(m).with_columns(pl.when(pl.col("code") == "000584").then(False).otherwise(pl.col("st")).alias("st"))
    with pytest.raises(StHistMismatch):
        validate_st_hist(bad, m)


def test_validate_raises_on_extra_or_missing_rows():
    m = _meta()
    f = build_st_hist(m)
    with pytest.raises(StHistMismatch):
        validate_st_hist(f.filter(pl.col("code") != "600000"), m)
    extra = pl.concat([f, pl.DataFrame({"code": ["967711"], "effective_from": ["2015-01-01"], "st": [False]})])
    with pytest.raises(StHistMismatch):
        validate_st_hist(extra, m)


def test_adjacent_st_rows_merge():
    m = pl.DataFrame({"code": ["a"] * 4, "effective_from": ["2020-01-01", "2020-02-01", "2020-03-01", "2020-04-01"], "is_st": [0, 1, 1, 0]})
    assert st_intervals(build_st_hist(m).rename({"st": "f"}), "f").rows() == [("a", "2020-02-01", "2020-04-01")]

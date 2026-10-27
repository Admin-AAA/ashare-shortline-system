"""成交量单位护栏：中长线 long-history panel（scripts/midterm/lt_data.prices）拼接两侧 volume 必须同为"手"。

背景（2026-10-19）：daily_hist_*.parquet（tushare daily，vol=手）在拼接时被 ×100 成"股"，而
daily_kbar cache（2022-01-04 起）是"手"，跨 2022 用 volume 的计算差 100 倍。
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

MID = Path(__file__).resolve().parents[1] / "scripts" / "midterm"
sys.path.insert(0, str(MID))
import lt_data  # noqa: E402


def _frame(rows):
    return pl.DataFrame(rows, schema=["code", "trade_date", "close", "amount", "volume"], orient="row")


def test_normalize_converts_share_rows_and_is_idempotent():
    df = _frame([
        ("000001", "2021-12-31", 16.48, 2_899_620_000.0, 175_076_000.0),  # 股
        ("000001", "2022-01-04", 16.66, 1_918_890_000.0, 1_169_260.0),    # 手
        ("600000", "2021-12-31", 10.0, 0.0, 0.0),                         # 停牌：不可判别，原样
    ])
    once = lt_data.normalize_volume_to_lots(df)
    assert once["volume"].to_list() == [1_750_760.0, 1_169_260.0, 0.0]
    twice = lt_data.normalize_volume_to_lots(once)
    assert twice["volume"].to_list() == once["volume"].to_list()
    lt_data.assert_volume_in_lots(once)


def test_assert_rejects_mixed_units():
    df = _frame([("000001", "2021-12-31", 16.48, 2_899_620_000.0, 175_076_000.0),
                 ("000001", "2022-01-04", 16.66, 1_918_890_000.0, 1_169_260.0)])
    with pytest.raises(AssertionError):
        lt_data.assert_volume_in_lots(df)


@pytest.mark.skipif(not (lt_data.D / "daily_hist_2021.parquet").exists() or not lt_data.CACHE.exists(),
                    reason="本地中长线 parquet 不在")
def test_real_panel_volume_unit_consistent_across_2022_stitch():
    df = lt_data.prices("2022-01-01", "2022-01-14").filter(pl.col("trade_date").is_between(pl.lit("2021-12-20"), pl.lit("2022-01-14")))
    r = (df.filter((pl.col("volume") > 0) & (pl.col("close") > 0))
           .with_columns((pl.col("amount") / (pl.col("close") * pl.col("volume"))).alias("r"),
                         (pl.col("trade_date") >= "2022-01-04").alias("new")))
    med = dict(r.group_by("new").agg(pl.col("r").median()).rows())
    assert set(med) == {False, True}
    for side, m in med.items():
        assert 90 < m < 110, (side, m)

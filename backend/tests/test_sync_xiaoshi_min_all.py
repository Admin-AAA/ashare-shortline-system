"""小石全市场分钟线归档脚本的纯函数：路径映射、增量判定、旧版本留存、列统一。"""
import sys
from pathlib import Path

B = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(B)); sys.path.insert(0, str(B / "scripts"))
import sync_xiaoshi_min_all as S  # noqa: E402


def test_key_to_relpath():
    assert S.key_to_relpath("market=CN/symbol=000005") == "symbol/000005.parquet"
    assert S.key_to_relpath("market=CN/year=2026/month=2026-09/bucket=7") == "bucket/2026-09/bucket=7.parquet"


def test_plan_downloads_incremental():
    cat = [{"key": "a", "sha256": "1"}, {"key": "b", "sha256": "2"}, {"key": "c", "sha256": "3"}]
    state = {"a": {"sha256": "1"}, "b": {"sha256": "OLD"}}
    assert [x["key"] for x in S.plan_downloads(cat, state)] == ["b", "c"]
    assert S.plan_downloads(cat, {k["key"]: {"sha256": k["sha256"]} for k in cat}) == []


def test_should_archive_old_only_when_rolling_trim():
    assert S.should_archive_old({"first_date": "2026-07-13"}, "2026-07-14") is True
    assert S.should_archive_old({"first_date": "2026-07-13"}, "2026-07-13") is False
    assert S.should_archive_old(None, "2026-07-13") is False


def test_unify_columns_and_units():
    import polars as pl
    df = pl.DataFrame({"symbol": ["600000", "000001"], "exchange": ["SH", None], "trade_time": ["2026-09-01 09:31:00", "2026-09-01 09:31:00"],
                       "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [100, 200], "amount": [1000.0, 2000.0]}
                      ).with_columns(pl.col("trade_time").str.to_datetime())
    u = S.unify(df)
    assert u["ts_code"].to_list() == ["600000.SH", "000001.SZ"]
    assert u["vol"].to_list() == [100.0, 200.0]
    assert set(S.UNIFIED_COLS) <= set(u.columns)

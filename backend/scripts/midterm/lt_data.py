"""[中长线] Long-history panel 2018+: Tushare daily (backfill parquet) + daily_kbar cache (2021-05-26+),
full daily_basic (pe_ttm/pb/dv_ttm/total_mv), PIT ST flag, limit flags; fina/forecast PIT helpers.

Units of prices(): volume = 手 (lots, 100 shares), amount = 元 — on both sides of the 2022-01-04 stitch."""
from __future__ import annotations
from pathlib import Path
import os, datetime as _dt
import polars as pl
from harness import CACHE

def _range(start=None, end=None, warmup_days=420):
    """Date window (YYYY-MM-DD). Defaults from env LT_START/LT_END; start is pulled back by warmup_days
    so 250-day rolling features are valid at `start`. None -> unbounded (old behaviour)."""
    start = start or os.environ.get("LT_START"); end = end or os.environ.get("LT_END")
    if start:
        start = (_dt.date.fromisoformat(start) - _dt.timedelta(days=warmup_days)).isoformat()
    return start, end

def _win(col, start, end, dash=True):
    e = pl.lit(True)
    f = (lambda x: x) if dash else (lambda x: x.replace("-", ""))
    if start: e = e & (pl.col(col) >= f(start))
    if end: e = e & (pl.col(col) <= f(end))
    return e

D = Path(__file__).resolve().parents[2] / "data/midterm"

def _dash(col):  # YYYYMMDD -> YYYY-MM-DD
    return pl.concat_str([pl.col(col).str.slice(0, 4), pl.col(col).str.slice(4, 2), pl.col(col).str.slice(6, 2)], separator="-")

def prices(start=None, end=None) -> pl.DataFrame:
    start, end = _range(start, end)
    cols = ["code", "trade_date", "open", "high", "low", "close", "amount", "pct_chg", "volume"]
    new = pl.scan_parquet(CACHE).select(cols).with_columns(pl.col("trade_date").cast(pl.Utf8)).filter(_win("trade_date", start, end)).collect()
    parts = [new]
    hist = [f for f in sorted(D.glob("daily_hist_*.parquet")) if not start or f.stem[-4:] >= start[:4]]
    if hist:
        h = pl.concat([pl.read_parquet(f) for f in hist], how="diagonal_relaxed")
        h = h.select(pl.col("ts_code").str.slice(0, 6).alias("code"), _dash("trade_date").alias("trade_date"),
                     "open", "high", "low", "close", (pl.col("amount") * 1000).alias("amount"), "pct_chg",
                     pl.col("vol").alias("volume"))  # tushare daily vol 已是"手"，与 cache 一致，不再 ×100
        # cache is sparse (~5 codes/day) until 2022-01-04 -> switch to the cache only once it is broad
        cnt = new.group_by("trade_date").len()
        cut = cnt.filter(pl.col("len") >= 2000)["trade_date"].min() or "2022-01-04"
        new = new.filter(pl.col("trade_date") >= cut)
        parts = [new]
        h = h.filter((pl.col("trade_date") < cut) & _win("trade_date", start, end))
        parts.insert(0, h.cast({c: new.schema[c] for c in cols}))
    df = pl.concat(parts).filter((pl.col("close") > 0) & (pl.col("open") > 0)).unique(["code", "trade_date"]).sort("code", "trade_date")
    df = normalize_volume_to_lots(df)
    assert_volume_in_lots(df)
    return df


# ── 成交量单位护栏（2026-10-19）──────────────────────────────────────────────────
# panel 的 volume 统一为"手"(100 股)、amount 为"元"（与 tushare daily / daily_kbars 一致）。
# 按行判别：amount/(close*volume) ≈ 100 → 手；≈ 1 → 股（÷100）。阈值取几何中点 10，
# 盘中均价/收盘偏差（±20% 涨跌停内）远到不了 10 倍，判别无歧义；已是"手"的行不动，幂等。
VOLUME_UNIT = "lots"
_LOTS_RATIO = 100.0
_SHARE_LOT_SPLIT = 10.0


def _vol_ratio() -> pl.Expr:
    return pl.col("amount") / (pl.col("close") * pl.col("volume"))


def normalize_volume_to_lots(df: pl.DataFrame) -> pl.DataFrame:
    """把按"股"记录的行 ÷100 换成"手"；无法判别（缺 amount/close/volume 或为 0）的行原样保留。"""
    if df.is_empty() or not {"amount", "close", "volume"} <= set(df.columns):
        return df
    is_shares = (pl.col("volume") > 0) & (pl.col("close") > 0) & (pl.col("amount") > 0) & (_vol_ratio() < _SHARE_LOT_SPLIT)
    return df.with_columns(pl.when(is_shares).then(pl.col("volume") / 100.0).otherwise(pl.col("volume")).alias("volume"))


def assert_volume_in_lots(df: pl.DataFrame, lo: float = 50.0, hi: float = 200.0) -> None:
    """逐年中位 amount/(close*volume) 必须落在 [lo, hi]（≈100 即"手"），否则抛错，防止单位再次混用。"""
    if df.is_empty() or not {"amount", "close", "volume", "trade_date"} <= set(df.columns):
        return
    chk = (df.filter((pl.col("volume") > 0) & (pl.col("close") > 0) & (pl.col("amount") > 0))
             .group_by(pl.col("trade_date").cast(pl.Utf8).str.slice(0, 4).alias("y"))
             .agg(_vol_ratio().median().alias("r")))
    bad = chk.filter((pl.col("r") < lo) | (pl.col("r") > hi))
    if bad.height:
        raise AssertionError(f"volume 单位不一致（应为手，amount/(close*volume)≈100）: {bad.sort('y').rows()}")

def basic(start=None, end=None) -> pl.DataFrame:
    start, end = _range(start, end)
    fs = [f for f in sorted(D.glob("basic_[0-9]*.parquet")) if not start or f.stem[-4:] >= start[:4]]
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "pe_ttm", "pb", "dv_ttm", "total_mv", "circ_mv"]) for f in fs], how="diagonal_relaxed")
    b = b.filter(_win("trade_date", start, end, dash=False))
    return b.select(pl.col("ts_code").str.slice(0, 6).alias("code"), _dash("trade_date").alias("trade_date"),
                    "pe_ttm", "pb", "dv_ttm", "total_mv", "circ_mv").unique(["code", "trade_date"])

def fina_pit() -> pl.DataFrame:
    """One row per (code, ann_date) with latest-reported metrics -> join_asof on trade_date (backward)."""
    fs = list((D / "fina").glob("*.parquet"))
    keep = ["ts_code", "ann_date", "end_date", "roe", "roe_dt", "grossprofit_margin", "netprofit_yoy", "or_yoy", "debt_to_assets", "ocfps", "eps"]
    f = pl.concat([pl.read_parquet(x).select([c for c in keep if c in pl.read_parquet_schema(x)]) for x in fs], how="diagonal_relaxed")
    f = f.drop_nulls("ann_date").with_columns(pl.col("ts_code").str.slice(0, 6).alias("code"), _dash("ann_date").alias("trade_date"))
    # annualised ROE: quarterly roe is YTD -> scale by 12/months
    m = pl.col("end_date").str.slice(4, 2).cast(pl.Int32)
    f = f.with_columns((pl.col("roe").cast(pl.Float64, strict=False) * 12 / m).alias("roe_ann"))
    return f.sort("end_date").unique(["code", "trade_date"], keep="last").sort("code", "trade_date")

def forecast_events() -> pl.DataFrame:
    fs = list((D / "forecast").glob("*.parquet"))
    f = pl.concat([pl.read_parquet(x) for x in fs], how="diagonal_relaxed")
    return f.drop_nulls("ann_date").select(pl.col("ts_code").str.slice(0, 6).alias("code"), _dash("ann_date").alias("ann"),
                                           "end_date", "type", "p_change_min", "p_change_max")

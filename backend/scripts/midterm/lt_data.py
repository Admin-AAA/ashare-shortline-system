"""[中长线] Long-history panel 2018+: Tushare daily (backfill parquet) + daily_kbar cache (2021-05-26+),
full daily_basic (pe_ttm/pb/dv_ttm/total_mv), PIT ST flag, limit flags; fina/forecast PIT helpers."""
from __future__ import annotations
from pathlib import Path
import polars as pl
from harness import CACHE

D = Path(__file__).resolve().parents[2] / "data/midterm"

def _dash(col):  # YYYYMMDD -> YYYY-MM-DD
    return pl.concat_str([pl.col(col).str.slice(0, 4), pl.col(col).str.slice(4, 2), pl.col(col).str.slice(6, 2)], separator="-")

def prices() -> pl.DataFrame:
    cols = ["code", "trade_date", "open", "high", "low", "close", "amount", "pct_chg", "volume"]
    new = pl.scan_parquet(CACHE).select(cols).with_columns(pl.col("trade_date").cast(pl.Utf8)).collect()
    parts = [new]
    hist = sorted(D.glob("daily_hist_*.parquet"))
    if hist:
        h = pl.concat([pl.read_parquet(f) for f in hist], how="diagonal_relaxed")
        h = h.select(pl.col("ts_code").str.slice(0, 6).alias("code"), _dash("trade_date").alias("trade_date"),
                     "open", "high", "low", "close", (pl.col("amount") * 1000).alias("amount"), "pct_chg",
                     (pl.col("vol") * 100).alias("volume"))
        h = h.filter(pl.col("trade_date") < new["trade_date"].min())
        parts.insert(0, h.cast({c: new.schema[c] for c in cols}))
    df = pl.concat(parts).filter((pl.col("close") > 0) & (pl.col("open") > 0)).unique(["code", "trade_date"]).sort("code", "trade_date")
    return df

def basic() -> pl.DataFrame:
    fs = sorted(D.glob("basic_*.parquet"))
    b = pl.concat([pl.read_parquet(f, columns=["ts_code", "trade_date", "pe_ttm", "pb", "dv_ttm", "total_mv", "circ_mv"]) for f in fs], how="diagonal_relaxed")
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
    f = f.with_columns((pl.col("roe") * 12 / m).alias("roe_ann"))
    return f.sort("end_date").unique(["code", "trade_date"], keep="last").sort("code", "trade_date")

def forecast_events() -> pl.DataFrame:
    fs = list((D / "forecast").glob("*.parquet"))
    f = pl.concat([pl.read_parquet(x) for x in fs], how="diagonal_relaxed")
    return f.drop_nulls("ann_date").select(pl.col("ts_code").str.slice(0, 6).alias("code"), _dash("ann_date").alias("ann"),
                                           "end_date", "type", "p_change_min", "p_change_max")

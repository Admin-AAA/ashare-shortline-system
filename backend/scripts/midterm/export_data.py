"""[中长线] Export MySQL tables the mid-term research needs to backend/data/midterm/ (gitignored)."""
from pathlib import Path
import polars as pl
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "midterm"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    url = [l.split("=", 1)[1].strip() for l in open(ROOT / ".env") if l.startswith("ASHARE_DATABASE_URL")][0]
    with create_engine(url).connect() as c:
        pl.read_database("select code,trade_date,pe_ttm,total_mv,circ_mv from daily_basic", c).write_parquet(OUT / "daily_basic.parquet")
        h = pl.read_database("select code,name,effective_from from stock_metadata_history", c)
        (h.with_columns(pl.col("effective_from").cast(pl.Utf8), pl.col("name").str.contains("ST").alias("st"))
          .sort("code", "effective_from").select("code", "effective_from", "st").write_parquet(OUT / "st_hist.parquet"))
        pl.read_database("select ts_code,trade_date,close from index_daily", c).write_parquet(OUT / "index_daily.parquet")
        pl.read_database("select code,trade_date,up_limit,down_limit from stk_limits", c).with_columns(pl.col("trade_date").cast(pl.Utf8)).write_parquet(OUT / "stk_limits.parquet")
        pl.read_database("select code,trade_date,ratio from hk_holdings", c).with_columns(pl.col("trade_date").cast(pl.Utf8)).write_parquet(OUT / "hk_holdings.parquet")
    print("exported to", OUT)

if __name__ == "__main__":
    main()

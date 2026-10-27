"""[短线研究 §280] 导出本地 MySQL capital_flow（只读 SELECT，不联网）→ output/research/s280/capital_flow.parquet。
用法：cd backend && PYTHONPATH=. .venv/bin/python scripts/shortline_research/rso_export_s280.py"""
import os
import polars as pl
from sqlalchemy import text
from app.db.session import SessionLocal

OUT = os.path.join(os.path.dirname(__file__), "../../output/research/s280/capital_flow.parquet")
db = SessionLocal()
parts = []
for y in range(2022, 2027):
    rows = db.execute(text("select code, trade_date, small_net_inflow_yi, main_net_inflow_yi from capital_flow "
                           "where trade_date between :a and :b"), {"a": f"{y}-01-01", "b": f"{y}-12-31"}).fetchall()
    parts.append(pl.DataFrame(rows, schema=["code", "trade_date", "small_net", "main_net"], orient="row",
                              schema_overrides={"small_net": pl.Float64, "main_net": pl.Float64}))
    print(y, len(rows), flush=True)
df = pl.concat(parts).unique(["code", "trade_date"]).sort("code", "trade_date")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
df.write_parquet(OUT)
print("rows", df.height, "dates", df["trade_date"].n_unique(), df["trade_date"].min(), df["trade_date"].max())

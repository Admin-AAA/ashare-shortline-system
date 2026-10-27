"""§275 解禁事件导出：share_floats（口径同 app/services/unlock_filter.py，PIT：ann_date ≤ 解禁日），按 (code, float_date) 合计解禁股数。只读库。"""
import polars as pl
from sqlalchemy import text
from app.db.session import SessionLocal
SQL = """
SELECT code, float_date AS fd, MAX(ann) AS ann_max, SUM(sh) AS sh, SUM(rt) AS rt, COUNT(*) AS nh FROM (
  SELECT code, float_date, MIN(ann_date) AS ann, MAX(float_share) AS sh, MAX(float_ratio) AS rt
  FROM share_floats
  WHERE ann_date IS NOT NULL AND ann_date <> '' AND (holder_name IS NULL OR holder_name NOT LIKE 'xiaoshi:%')
    AND float_date >= '20190101' AND float_date <= '20261016' AND ann_date <= float_date
  GROUP BY code, float_date, holder_key, float_share_key
) x GROUP BY code, float_date
"""
if __name__ == "__main__":
    db = SessionLocal()
    rows = db.execute(text(SQL)).fetchall()
    d = pl.DataFrame([tuple(r) for r in rows], schema=["code", "fd", "ann_max", "sh", "rt", "nh"], orient="row")
    print(d.height, d["sh"].median(), d["rt"].median(), d["rt"].quantile(0.9))
    d.write_parquet("output/research/s275/unlock_events.parquet")

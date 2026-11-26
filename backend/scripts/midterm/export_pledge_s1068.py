"""[§1068 数据] 只读导出 MySQL pledge_stats（中登周度股权质押统计）到研究缓存 parquet。只 SELECT，不写库。
用法：cd <主工作树>/backend && PYTHONPATH=. .venv/bin/python <本文件>"""
import warnings
from pathlib import Path

import pandas as pd
from sqlalchemy import text

warnings.filterwarnings("ignore")
OUT = Path(__file__).resolve().parents[2] / "data/research_cache/pledge_stats/pledge_stats.parquet"


def main() -> None:
    from app.db.session import engine

    with engine.connect() as c:
        df = pd.read_sql(text("SELECT code, end_date, pledge_count, unrest_pledge, rest_pledge, pledge_ratio, created_at "
                              "FROM pledge_stats"), c)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(len(df), df["end_date"].min(), df["end_date"].max())


if __name__ == "__main__":
    main()

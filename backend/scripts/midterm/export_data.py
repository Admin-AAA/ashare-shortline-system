"""[中长线] Export MySQL tables the mid-term research needs to backend/data/midterm/ (gitignored).

st_hist.parquet 以 DB `stock_metadata_history.is_st` 为唯一权威（按 effective_from 区间）。
旧实现用 `name.contains("ST")` —— 但 name 列在该股所有历史行里都是"最新名称"，
导致：①现名不含 ST 的股（摘帽改名 / 退市 "xx退(退)"）整段 ST 期被漏标；
②现名含 ST 的股在历史非 ST 期被误标。勿再用 name 推断 ST。

用法：
  python -m scripts.midterm.export_data                      # 导出全部表
  python -m scripts.midterm.export_data --only st_hist       # 仅重建 st_hist.parquet（含与 DB 的一致性校验）
  python -m scripts.midterm.export_data --only st_hist --out /path/to/midterm
"""
import argparse
import os
import tempfile
from pathlib import Path

import polars as pl
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "midterm"

ST_META_SQL = (
    "select code, cast(effective_from as char) as effective_from, "
    "cast(is_st as signed) as is_st from stock_metadata_history"
)


class StHistMismatch(RuntimeError):
    """st_hist.parquet 与 DB is_st 区间不一致。"""


def build_st_hist(meta: pl.DataFrame) -> pl.DataFrame:
    """meta: code, effective_from, is_st → code, effective_from(Utf8), st(Boolean)，按 code,effective_from 排序。"""
    return (
        meta.select(
            pl.col("code").cast(pl.Utf8),
            pl.col("effective_from").cast(pl.Utf8).str.slice(0, 10).alias("effective_from"),
            (pl.col("is_st").cast(pl.Int64) == 1).alias("st"),
        )
        .unique(subset=["code", "effective_from"], keep="last")
        .sort("code", "effective_from")
    )


def st_intervals(df: pl.DataFrame, flag: str) -> pl.DataFrame:
    """把逐行(code, effective_from, flag)折成 ST 区间 (code, start, end)；end=下一行 effective_from，末行为 None(开放)。
    相邻连续 ST 行合并。与 lt_research 的 asof backward 语义一致。"""
    d = (
        df.select(pl.col("code").cast(pl.Utf8), pl.col("effective_from").cast(pl.Utf8).str.slice(0, 10).alias("effective_from"),
                  (pl.col(flag).cast(pl.Int64) == 1).alias("f"))
        .sort("code", "effective_from")
        .with_columns(pl.col("effective_from").shift(-1).over("code").alias("nxt"))
    )
    d = d.with_columns(
        (pl.col("f") != pl.col("f").shift(1).over("code")).fill_null(True).cum_sum().over("code").alias("grp")
    )
    return (
        d.filter(pl.col("f"))
        .group_by("code", "grp", maintain_order=True)
        .agg(pl.col("effective_from").first().alias("start"), pl.col("nxt").last().alias("end"))
        .select("code", "start", "end")
        .sort("code", "start")
    )


def validate_st_hist(file_df: pl.DataFrame, meta: pl.DataFrame, *, max_show: int = 10) -> dict:
    """比较 st_hist 与 DB is_st：行键集合、逐行 st、ST 区间三项，任一不一致抛 StHistMismatch。"""
    f = file_df.select("code", "effective_from", "st").with_columns(pl.col("effective_from").cast(pl.Utf8))
    m = build_st_hist(meta).rename({"st": "is_st"})
    j = f.join(m, on=["code", "effective_from"], how="full", coalesce=True)
    only_file = j.filter(pl.col("is_st").is_null())
    only_db = j.filter(pl.col("st").is_null())
    flip = j.filter(pl.col("st").is_not_null() & pl.col("is_st").is_not_null() & (pl.col("st") != pl.col("is_st")))
    iv_f = st_intervals(f, "st")
    iv_m = st_intervals(m, "is_st")
    iv_diff = iv_f.join(iv_m, on=["code", "start", "end"], how="full", coalesce=True, nulls_equal=True)
    iv_bad = iv_f.height != iv_m.height or iv_diff.height != iv_f.height
    stats = {
        "file_rows": f.height, "db_rows": m.height,
        "only_in_file": only_file.height, "only_in_db": only_db.height, "st_flag_diff_rows": flip.height,
        "st_intervals_file": iv_f.height, "st_intervals_db": iv_m.height,
        "st_codes_file": iv_f["code"].n_unique(), "st_codes_db": iv_m["code"].n_unique(),
    }
    if only_file.height or only_db.height or flip.height or iv_bad:
        sample = pl.concat([only_file, only_db, flip]).head(max_show).rows()
        raise StHistMismatch(f"st_hist 与 DB is_st 不一致: {stats}; 样例(code,effective_from,file_st,db_is_st)={sample}")
    return stats


def write_st_hist(c, out_dir: Path) -> dict:
    meta = pl.read_database(ST_META_SQL, c, infer_schema_length=None)
    st = build_st_hist(meta)
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".st_hist.", suffix=".parquet.tmp", dir=out_dir)
    os.close(fd)
    try:
        st.write_parquet(tmp)
        stats = validate_st_hist(pl.read_parquet(tmp), meta)  # 先校验临时文件，通过才替换
        os.replace(tmp, out_dir / "st_hist.parquet")
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["st_hist"], default=None)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    url = os.environ.get("ASHARE_DATABASE_URL") or [l.split("=", 1)[1].strip() for l in open(ROOT / ".env") if l.startswith("ASHARE_DATABASE_URL")][0]
    with create_engine(url).connect() as c:
        if a.only == "st_hist":
            print("st_hist ok:", write_st_hist(c, out))
            return
        pl.read_database("select code,trade_date,pe_ttm,total_mv,circ_mv from daily_basic", c).write_parquet(out / "daily_basic.parquet")
        print("st_hist ok:", write_st_hist(c, out))
        pl.read_database("select ts_code,trade_date,close from index_daily", c).write_parquet(out / "index_daily.parquet")
        pl.read_database("select code,trade_date,up_limit,down_limit from stk_limits", c).with_columns(pl.col("trade_date").cast(pl.Utf8)).write_parquet(out / "stk_limits.parquet")
        pl.read_database("select code,trade_date,ratio from hk_holdings", c).with_columns(pl.col("trade_date").cast(pl.Utf8)).write_parquet(out / "hk_holdings.parquet")
    print("exported to", out)

if __name__ == "__main__":
    main()

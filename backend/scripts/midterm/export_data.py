"""[中长线] Export MySQL tables the mid-term research needs to backend/data/midterm/ (gitignored).

st_hist.parquet 以 DB `stock_metadata_history.is_st` 为唯一权威（按 effective_from 区间）。
旧实现用 `name.contains("ST")` —— 但 name 列在该股所有历史行里都是"最新名称"，
导致：①现名不含 ST 的股（摘帽改名 / 退市 "xx退(退)"）整段 ST 期被漏标；
②现名含 ST 的股在历史非 ST 期被误标。勿再用 name 推断 ST。

用法：
  python -m scripts.midterm.export_data                      # 导出全部表
  python -m scripts.midterm.export_data --only st_hist       # 仅重建 st_hist.parquet（含与 DB 的一致性校验）
  python -m scripts.midterm.export_data --only st_hist --out /path/to/midterm
  python -m scripts.midterm.export_data --only daily_basic,index_daily   # 增量：只重取「已有最大日期-7天」之后的行
  python -m scripts.midterm.export_data --full                           # 强制全量重导（默认对有日期列的表走增量）

增量导出（2026-10-25）：daily_basic(600万行)/stk_limits(480万行) 全量导出耗时耗内存（16GB Mac），且 parquet 长期停在
10-09/10-16/06-30。现在默认读已有 parquet 的最大日期，回看 OVERLAP_DAYS 天（覆盖补数/修正）后只从 DB 取新行拼接，
临时文件写完再 os.replace 原子替换（owner 的研究进程可能正读该文件）。
"""
import argparse
import datetime as dt
import os
import tempfile
from pathlib import Path

import polars as pl
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "midterm"

OVERLAP_DAYS = 7

# name -> (表, 列, 日期列, 日期列是否需 cast 成 Utf8)
TABLES = {
    "daily_basic": ("daily_basic", "code,trade_date,pe_ttm,total_mv,circ_mv", "trade_date", False),
    "index_daily": ("index_daily", "ts_code,trade_date,close", "trade_date", False),
    "stk_limits": ("stk_limits", "code,trade_date,up_limit,down_limit", "trade_date", True),
    "hk_holdings": ("hk_holdings", "code,trade_date,ratio", "trade_date", True),
}

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


def _atomic_write_parquet(df: pl.DataFrame, target: Path) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".parquet.tmp", dir=target.parent)
    os.close(fd)
    try:
        df.write_parquet(tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def export_table(c, name: str, out_dir: Path, *, full: bool = False, overlap_days: int = OVERLAP_DAYS) -> dict:
    """导出一张表；已有 parquet 且非 full 时增量：保留 < cutoff 的旧行，用 DB 里 >= cutoff 的行替换其后部分。"""
    table, cols, date_col, _ = TABLES[name]
    target = out_dir / f"{name}.parquet"

    def read(where: str = "") -> pl.DataFrame:
        df = pl.read_database(f"select {cols} from {table}{where}", c)
        return df.with_columns(pl.col(date_col).cast(pl.Utf8))  # 日期统一为字符串，与历史 parquet 一致

    if full or not target.exists():
        df = read()
        _atomic_write_parquet(df, target)
        return {"table": name, "mode": "full", "rows": df.height, "max_date": df[date_col].max()}
    old = pl.read_parquet(target)
    max_old = old[date_col].max()
    cutoff = (dt.date.fromisoformat(str(max_old)[:10]) - dt.timedelta(days=overlap_days)).isoformat()  # 自算 ISO 日期，无注入面
    new = read(f" where {date_col} >= '{cutoff}'").select(old.columns).cast(old.schema)
    keep = old.filter(pl.col(date_col) < cutoff)
    merged = pl.concat([keep, new])
    _atomic_write_parquet(merged, target)
    return {"table": name, "mode": "incremental", "cutoff": cutoff, "old_rows": old.height, "new_rows": new.height,
            "rows": merged.height, "old_max": max_old, "max_date": merged[date_col].max()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="逗号分隔：st_hist,daily_basic,index_daily,stk_limits,hk_holdings")
    ap.add_argument("--full", action="store_true", help="强制全量重导（默认增量）")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    url = os.environ.get("ASHARE_DATABASE_URL") or [l.split("=", 1)[1].strip() for l in open(ROOT / ".env") if l.startswith("ASHARE_DATABASE_URL")][0]
    with create_engine(url).connect() as c:
        wanted = [x for x in (a.only or "st_hist," + ",".join(TABLES)).split(",") if x]
        bad = [x for x in wanted if x != "st_hist" and x not in TABLES]
        if bad:
            raise SystemExit(f"unknown table(s): {bad}")
        for name in wanted:
            if name == "st_hist":
                print("st_hist ok:", write_st_hist(c, out))
            else:
                print(export_table(c, name, out, full=a.full), flush=True)
    print("exported to", out)

if __name__ == "__main__":
    main()

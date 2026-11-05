"""§1001 起第四批开跑前的可检验性清点：只数「事件 & liq & r20≤−15% & 信号日未封板」的信号个数，不算任何收益。
用法：cd backend/scripts/midterm && LT_START=2024-01-01 LT_END=2026-10-30 PYTHONPATH=<backend>:.:../shortline_research python ../shortline_research/evdip_s1001_count.py"""
import glob
import polars as pl
import lt_data as L
from lt_research import build
from lt_research6 import holder_events
from lt_research12 import _pit
import lt_research47 as R47
c = pl.col
WIN = {"R12": ("2025-10-01", "2026-10-16"), "EXT": ("2024-01-01", "2025-09-30")}


def de_events(td):
    """股东减持（holdertrade_de）按 (code, 公告日) 合计 change_ratio，≥1% 总股本（§986 门槛）。"""
    h = pl.concat([pl.read_parquet(f, columns=["ts_code", "ann_date", "change_ratio"])
                   for f in sorted(glob.glob(str(L.D / "events" / "holdertrade_de_*.parquet")))], how="diagonal_relaxed")
    h = h.with_columns(c("change_ratio").cast(pl.Float64, strict=False).fill_null(0)).unique()
    h = h.group_by("ts_code", "ann_date").agg(c("change_ratio").sum().alias("de_cr")).filter(c("de_cr") >= 1.0)
    return _pit(h, td).group_by("code", "sd").agg(c("de_cr").max()).rename({"sd": "trade_date"})


if __name__ == "__main__":
    df = build().sort("code", "trade_date")
    td = df.select("trade_date").unique().sort("trade_date")
    df = (df.join(holder_events(td), on=["code", "trade_date"], how="left")
            .join(R47.frev_events(td).rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
            .join(R47.early_events(td).rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
            .join(de_events(td), on=["code", "trade_date"], how="left")).sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    nl = ((c("close") > pl.coalesce(c("down_limit"), pc * (1 - band)) + 0.005)
          & (c("close") < pl.coalesce(c("up_limit"), pc * (1 + band)) - 0.005)).fill_null(False)
    ev = {"HN 股东户数环比降≥10%": c("hn_chg") <= -0.10, "FREV 预告上修≥20%": c("rev").is_not_null(),
          "EARLY 财报提前≥15天": c("early").is_not_null(), "DE 减持≥1%": c("de_cr").is_not_null(),
          "STR ST摘帽": (c("st").shift(1).over("code") & ~c("st"))}
    base = c("liq") & (c("r20") <= -0.15) & nl
    for name, e in ev.items():
        row = {}
        for w, (s, t) in WIN.items():
            x = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(t)))
            m = x.filter((e & base).fill_null(False))
            row[w] = (m.height, m["trade_date"].n_unique(), int(x.filter((e & c("liq")).fill_null(False)).height))
        print(name, "| 信号/天/事件全量(liq):", row, flush=True)

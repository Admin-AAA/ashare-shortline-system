"""涨停事件面板构建 + 数据审计（预注册前，只读本地 parquet，不调外部接口）。

口径：
- 涨停判定用原始收盘价 vs 涨停价：stk_limits.parquet 的 up_limit 优先；缺失时按板块幅度兜底。
  兜底 band 日期感知：创业板 3* 在 2020-08-24 起 20cm，之前 10cm；科创板 688* 自始 20cm；主板 10cm。
  （ST/北交所已被 liq 排除，不考虑 5%/30%。）
- luc = close >= up_limit - 0.005（收盘封板）；touch = high >= up_limit - 0.005（盘中触板）；zha = touch & ~luc。
- nb = 截至当日收盘的连续封板天数（当日未封板则 0）。
- 收益一律用复权 OHLC（ac/ao），与生产 simulate 同构。

事件（全部 T 日收盘可见）：
- FB   首板：luc & nb==1
- DUAN 断板：昨日 luc（nb_1>=1）& 今日 ~luc
- FBC  反包板：今日 luc & 昨日 ~luc & 前日 luc（断板 1 日后回封），且前日 nb>=2（连板龙头断板反包）
- DBF1 首阴反包确认（次日）：L 连板>=2 → D=L+1 断板收阴(pct>=-7) → R=D+1 收盘>D最高 & ~luc
- DBF2 首阴反包确认（3 日内）：同 DBF1，但 R 为 D+1..D+3 中首个收盘>D最高 & ~luc 之日
- NMB  N天M板歇脚：近 5 日(含T)封板>=3 次 & T 未封板 & |pct_T|<=3

用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python ../shortline_research/lu_event_panel.py
输出：backend/output/research/lu_events/panel.parquet + audit.json
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import numpy as np
import polars as pl

from lt_research import build, PH

c = pl.col
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "lu_events")


def add_lu_flags(df: pl.DataFrame) -> pl.DataFrame:
    """日期感知的涨停/触板/炸板标记 + 连板高度。"""
    band = (
        pl.when(c("code").str.starts_with("688")).then(0.198)
        .when(c("code").str.starts_with("3") & (c("trade_date") >= "2020-08-24")).then(0.198)
        .otherwise(0.098)
    )
    pc = c("close").shift(1).over("code")
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    df = df.with_columns(
        ul.alias("ul_px"), dl.alias("dl_px"),
        (c("close") >= ul - 0.005).fill_null(False).alias("luc"),
        (c("high") >= ul - 0.005).fill_null(False).alias("touch"),
        (c("close") <= dl + 0.005).fill_null(False).alias("ldc"),
        (c("open") >= ul - 0.005).fill_null(False).alias("yizi"),
    )
    df = df.with_columns((c("touch") & ~c("luc")).alias("zha"))
    # 连板高度：逐 code 用 numpy 递推（polars 无原生 streak）
    nb = np.zeros(df.height, dtype=np.int32)
    codes = df["code"].to_numpy()
    luc = df["luc"].to_numpy()
    k = 0
    prev_code = None
    for i in range(df.height):
        if codes[i] != prev_code:
            k = 0
            prev_code = codes[i]
        k = k + 1 if luc[i] else 0
        nb[i] = k
    return df.with_columns(pl.Series("nb", nb))


def add_events(df: pl.DataFrame) -> pl.DataFrame:
    df = df.sort("code", "trade_date")
    # 复权最高（与 ac 同基），用于跨日「收盘 > D 最高」比较，避免除权失真
    df = df.with_columns((c("high") * c("ac") / c("close")).alias("ah"))
    g = lambda n: c("luc").shift(n).over("code").fill_null(False)
    hi1 = c("ah").shift(1).over("code")
    pct1 = c("pct_chg").shift(1).over("code")
    # 断板日 D 的属性（相对信号日 R 的 shift）
    df = df.with_columns(
        (c("luc") & (c("nb") == 1)).alias("FB"),
        (g(1) & ~c("luc")).alias("DUAN"),
        # FBC：今日反包板，昨日断板，前日连板>=2
        (c("luc") & ~g(1) & g(2) & (c("nb").shift(2).over("code").fill_null(0) >= 2) & ~c("yizi")).alias("FBC"),
    )
    # DBF：信号日 R，D=R-1 为首阴断板日，L=R-2 连板>=2
    d_is_shouyin = (
        ~g(1) & g(2)
        & (c("nb").shift(2).over("code").fill_null(0) >= 2)
        & (c("close").shift(1).over("code") < c("open").shift(1).over("code"))  # D 收阴（原始收盘<开盘）
        & (pct1 >= -7.0)
        & ~c("ldc").shift(1).over("code").fill_null(False)  # D 非跌停
    )
    df = df.with_columns(d_is_shouyin.alias("_d_ok"))
    # R 日收盘 > D 日最高（复权同基）且 R 未封板
    fanbao_ok = (c("ac") > hi1) & ~c("luc")
    df = df.with_columns(
        (c("_d_ok") & fanbao_ok).alias("DBF1"),
    )
    # DBF2：D+1..D+3 首个反包确认日。用「D 在 1..3 日前且其间无更早确认」实现：
    # 对 k=1..3，D=R-k 满足首阴断板，R 收盘>D最高，且 R-1..R-k+1 均未收盘>D最高（保证首个）
    cond2 = pl.lit(False)
    for k in (1, 2, 3):
        dk = (
            ~c("luc").shift(k).over("code").fill_null(False)
            & c("luc").shift(k + 1).over("code").fill_null(False)
            & (c("nb").shift(k + 1).over("code").fill_null(0) >= 2)
            & (c("close").shift(k).over("code") < c("open").shift(k).over("code"))
            & (c("pct_chg").shift(k).over("code") >= -7.0)
            & ~c("ldc").shift(k).over("code").fill_null(False)
        )
        above = c("ac") > c("ah").shift(k).over("code")
        # 其间各日 j=1..k-1 均未收盘 > D 最高
        no_earlier = pl.lit(True)
        for j in range(1, k):
            no_earlier = no_earlier & ~(c("ac").shift(j).over("code") > c("ah").shift(k).over("code"))
        cond2 = cond2 | (dk & above & no_earlier & ~c("luc"))
    df = df.with_columns(cond2.fill_null(False).alias("DBF2"))
    # NMB：近5日(含T)封板>=3 & T未封板 & |pct|<=3
    lu5 = sum(c("luc").shift(j).over("code").fill_null(False).cast(pl.Int32) for j in range(5))
    df = df.with_columns(
        ((lu5 >= 3) & ~c("luc") & (c("pct_chg").abs() <= 3.0)).alias("NMB"),
        lu5.alias("lu5"),
    )
    # 排序分：连板高度/板数优先，成交额次之（确定性 tie-break 用 code）
    amt = c("amount")
    df = df.with_columns(
        pl.when(c("FBC")).then(c("nb").shift(2).over("code")).otherwise(0).alias("_h_fbc"),
        pl.when(c("DBF1") | c("DBF2")).then(c("nb").shift(2).over("code")).otherwise(0).alias("_h_dbf"),
    )
    for ev, sc in (("FBC", "sc_fbc"), ("DBF1", "sc_dbf1"), ("DBF2", "sc_dbf2"), ("NMB", "sc_nmb")):
        h = c("_h_fbc") if ev == "FBC" else (c("lu5") if ev == "NMB" else c("_h_dbf"))
        df = df.with_columns(
            pl.when(c(ev)).then(h * 1e15 + amt).otherwise(-1e18).alias(sc)
        )
    return df.drop("_d_ok", "_h_fbc", "_h_dbf")


def audit(df: pl.DataFrame) -> dict:
    a: dict = {}
    a["panel"] = {
        "rows": df.height,
        "date_min": df["trade_date"].min(),
        "date_max": df["trade_date"].max(),
        "codes": df["code"].n_unique(),
    }
    lim = pl.read_parquet(os.path.join(os.path.dirname(__file__), "..", "..", "data", "midterm", "stk_limits.parquet"))
    a["stk_limits"] = {"rows": lim.height, "date_min": lim["trade_date"].min(), "date_max": lim["trade_date"].max(),
                       "codes": lim["code"].n_unique()}
    a["ul_missing_by_year"] = (
        df.with_columns(c("trade_date").str.slice(0, 4).alias("y"))
        .group_by("y").agg((c("up_limit").is_null().mean() * 100).round(2).alias("miss_pct"))
        .sort("y").to_dicts()
    )
    # kpl_list 交叉验证（2019+，开盘啦涨停榜收盘封板成员）
    kpl_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "research_cache", "kpl_list")
    parts = []
    for f in sorted(os.listdir(kpl_dir)):
        if f.endswith(".parquet"):
            x = pl.read_parquet(os.path.join(kpl_dir, f))
            parts.append(x)
    kpl = pl.concat(parts, how="diagonal_relaxed")
    a["kpl_cols"] = kpl.columns
    kpl = kpl.with_columns(
        c("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).dt.strftime("%Y-%m-%d").alias("trade_date")
    )
    # 统一 code 列名
    code_col = "code" if "code" in kpl.columns else ("ts_code" if "ts_code" in kpl.columns else None)
    if code_col:
        kpl = kpl.with_columns(c(code_col).str.slice(0, 6).alias("code6"))
        k = kpl.select("code6", "trade_date").unique()
        j = df.select("code", "trade_date", "luc").join(
            k.with_columns(pl.lit(True).alias("in_kpl")),
            left_on=["code", "trade_date"], right_on=["code6", "trade_date"], how="left",
        ).with_columns(c("in_kpl").fill_null(False))
        both = j.filter(c("luc") & c("in_kpl")).height
        a["kpl_xval"] = {
            "kpl_rows": k.height,
            "recon_luc": j.filter(c("luc")).height,
            "overlap": both,
            "recall_of_kpl": round(both / max(k.height, 1), 4),
            "precision_vs_kpl": round(both / max(j.filter(c("luc")).height, 1), 4),
        }
    # 事件频率（按阶段，liq & amount>=1e8 股池内）
    pool = c("liq") & (c("amount") >= 1e8)
    evs = ["FB", "DUAN", "FBC", "DBF1", "DBF2", "NMB"]
    freq = {}
    for ph, (s, e) in list(PH.items()) + [("N3 2024-01~2026-10", ("2024-01-01", "2026-10-16"))]:
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)) & pool)
        freq[ph] = {ev: int(w.filter(c(ev)).height) for ev in evs}
        freq[ph]["sigdays_FBC"] = int(w.filter(c("FBC"))["trade_date"].n_unique())
        freq[ph]["sigdays_DBF2"] = int(w.filter(c("DBF2"))["trade_date"].n_unique())
    a["event_freq_in_pool"] = freq
    return a


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    df = build()
    df = add_lu_flags(df)
    df = add_events(df)
    a = audit(df)
    df.write_parquet(os.path.join(OUT, "panel.parquet"))
    json.dump(a, open(os.path.join(OUT, "audit.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(a, indent=1, ensure_ascii=False))
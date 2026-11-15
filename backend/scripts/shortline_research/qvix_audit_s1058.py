"""[§1058 预注册前数据审计] QVIX 恐慌门 × 流动性篮子。

只读：MySQL index_daily 的 QVIX50.SH/QVIX300.SH（SELECT）+ 本地 lu_events 面板。
清洗（按 MEMORY 上游脏数据口径）：close<=0 丢弃；同日重复按日期去重留最后有效行。
QVIX50 2015-02-09 起（覆盖 P19+）；QVIX300 2019-12-23 起（P19 缺）。
因果：T 收盘 QVIX 可得 → T+1 开盘入场。

信号（审计阶段只数频率，不看收益）：
  mkt_lu_ld = 当日非北交所活跃股 涨停家数/max(跌停家数,1)（与 mrsi_s1013 同口径，用面板 luc/ldc）
  QV1: QVIX50 > 过去 250 个交易日（不含当日）90 分位 & liq & 收盘未封涨停
  QV2: QVIX50 / 5 日前 QVIX50 − 1 > 0.25 & liq & 收盘未封涨停
  QV3: QV1 & mkt_lu_ld <= 0.34
排序：当日成交额降序前 10（§1054 验证实盘近似口径）；K=20、max_new=10（仅回测）。

用法：cd backend/scripts/midterm
  set -a && . ../../.env && set +a
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/qvix_audit_s1058.py
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import polars as pl
import pymysql

c = pl.col
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "s1058")
PANEL = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "lu_events", "panel.parquet")


def load_qvix() -> pl.DataFrame:
    u = urlparse(os.environ["ASHARE_DATABASE_URL"].replace("mysql+pymysql://", "mysql://"))
    conn = pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                           password=u.password, database=u.path.lstrip("/"))
    cur = conn.cursor()
    cur.execute("SELECT ts_code, trade_date, close FROM index_daily WHERE ts_code IN ('QVIX50.SH','QVIX300.SH')")
    rows = cur.fetchall()
    conn.close()
    q = pl.DataFrame(rows, schema=["ts_code", "trade_date", "close"], orient="row")
    q = q.with_columns(c("trade_date").cast(pl.Utf8), c("close").cast(pl.Float64, strict=False))
    q = q.filter(c("close") > 0)
    # 同日重复留最后有效行（按 ts_code+date 去重）
    q = q.unique(["ts_code", "trade_date"], keep="last").sort("ts_code", "trade_date")
    return q


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    q = load_qvix()
    q50 = q.filter(c("ts_code") == "QVIX50.SH").select("trade_date", c("close").alias("qvix50"))
    q300 = q.filter(c("ts_code") == "QVIX300.SH").select("trade_date", c("close").alias("qvix300"))
    audit = {"qvix50": {"n": q50.height, "min": q50["trade_date"].min(), "max": q50["trade_date"].max()},
             "qvix300": {"n": q300.height, "min": q300["trade_date"].min(), "max": q300["trade_date"].max()}}

    df = pl.read_parquet(PANEL)
    # 市场涨跌停家数（非北交所活跃股）
    act = (c("volume") > 0) & (c("close") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
    ms = (df.with_columns((act & c("luc")).fill_null(False).alias("_lu"),
                          (act & c("ldc")).fill_null(False).alias("_ld"))
          .group_by("trade_date").agg(c("_lu").sum().alias("lu_n"), c("_ld").sum().alias("ld_n"))
          .with_columns((c("lu_n") / c("ld_n").clip(1)).alias("mkt_lu_ld")))
    df = df.join(ms.select("trade_date", "mkt_lu_ld"), on="trade_date", how="left")
    df = df.join(q50, on="trade_date", how="left").join(q300, on="trade_date", how="left")
    df = df.sort("trade_date")
    # 分位在日级表计算（过去 250 日不含当日），再 join 回长面板
    day = (df.select("trade_date", "qvix50", "qvix300", "mkt_lu_ld").unique().sort("trade_date")
           .with_columns(
               c("qvix50").shift(1).rolling_quantile(0.9, window_size=250, min_samples=200).alias("qv_p90_250"),
               (c("qvix50") / c("qvix50").shift(5) - 1).alias("qv_chg5")))
    df = df.join(day.select("trade_date", "qv_p90_250", "qv_chg5"), on="trade_date", how="left")

    not_lu = ~c("luc")
    base = c("liq") & not_lu
    qv1 = base & (c("qvix50") > c("qv_p90_250"))
    qv2 = base & (c("qv_chg5") > 0.25)
    qv3 = qv1 & (c("mkt_lu_ld") <= 0.34)
    df = df.with_columns(qv1.fill_null(False).alias("S_QV1"),
                         qv2.fill_null(False).alias("S_QV2"),
                         qv3.fill_null(False).alias("S_QV3"),
                         c("amount").alias("sc_amt_today"))
    # 信号日频率（市场级）与股池内候选数
    from lt_research import PH
    PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16"); PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
    freq = {}
    for ph, (s, e) in PH.items():
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        d = w.select("trade_date", "S_QV1", "S_QV2", "S_QV3").unique()
        freq[ph] = {
            "qv1_days": int(d.filter(c("S_QV1"))["trade_date"].n_unique()),
            "qv2_days": int(d.filter(c("S_QV2"))["trade_date"].n_unique()),
            "qv3_days": int(d.filter(c("S_QV3"))["trade_date"].n_unique()),
            "qv1_cands": int(w.filter(c("S_QV1")).height),
            "qv3_cands": int(w.filter(c("S_QV3")).height),
        }
    audit["freq"] = freq
    # QVIX 与 rsi6 恐慌日重叠（诊断，需要 rsi6 —— 用 mrsi_s1013.market_rsi 在日级等权收益上算）
    df.write_parquet(os.path.join(OUT, "panel_qvix.parquet"))
    json.dump(audit, open(os.path.join(OUT, "audit.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps(audit, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
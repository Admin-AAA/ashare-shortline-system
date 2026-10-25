"""[中线] §275 限售解禁日后买入 UNL_1/UNL_2（预注册 docs/strategy-research.md §275，结果出来前写定，不调参）。
事件表由 xbx_unl_export_s275.py 导出（output/research/s275/unlock_events.parquet）。
用法：cd backend/scripts/midterm && PYTHONPATH=../..:. ../../.venv/bin/python xbx_unl_s275.py ../../output/research/s275/res.json"""
import sys, json, os
import numpy as np
import polars as pl
from lt_research import build, fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
OUT = sys.argv[1]
EVF = os.path.join(os.path.dirname(OUT), "unlock_events.parquet")
df = build()
px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
df = df.join(px, on=["code", "bd"], how="left").with_columns(
    (c("circ_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("fmv"))
df = df.sort("code", "trade_date").with_columns(c("fmv").shift(1).over("code").alias("fmv_prev"), c("close").shift(1).over("code").alias("close_prev"))
# 事件：解禁日顺延到下一个交易日 T
td = df.select("trade_date").unique().sort("trade_date")
ev = pl.read_parquet(EVF).with_columns(
    (c("fd").str.slice(0, 4) + "-" + c("fd").str.slice(4, 2) + "-" + c("fd").str.slice(6, 2)).alias("fdd")).sort("fdd")
ev = ev.join_asof(td.with_columns(c("trade_date").alias("tdd")), left_on="fdd", right_on="tdd", strategy="forward").drop_nulls("trade_date")
ev = ev.group_by("code", "trade_date").agg(c("sh").sum().alias("sh"))
df = df.join(ev, on=["code", "trade_date"], how="left")
ratio = (c("sh") * c("close_prev") / (c("fmv_prev") * 1e4))
tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
df = df.with_columns(ratio.alias("ratio")).with_columns(
    (c("liq") & c("sh").is_not_null()).fill_null(False).alias("U1"),
    (c("liq") & c("sh").is_not_null() & (c("ratio") >= 0.5)).fill_null(False).alias("U2"),
    (c("ratio").fill_null(0) - tie).alias("sc_u"))
df = df.select("code", "trade_date", "ao", "ac", "lim_up_open", "lock_down", "liq", "total_mv", "U1", "U2", "sc_u", "ratio").sort("code", "trade_date")
print("事件(liq内)", int(df["U1"].sum()), "UNL_2", int(df["U2"].sum()), "ratio 分位", df.filter(c("U1"))["ratio"].quantile(0.5), df.filter(c("U1"))["ratio"].quantile(0.9), flush=True)
res = {}
for n, sig in (("UNL_1", "U1"), ("UNL_2", "U2")):
    for ph, (s, e) in PH.items():
        t, cur = run_real(df, sig, "sc_u", fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
        m = metrics_real(t, cur)
        if t is not None and t.height:
            b = cohort_bench(df, t)
            m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
            m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
            streak = mx = 0
            for x in t.sort("entry")["ret"].to_list():
                streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
            m["streak"] = mx
        res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
        print(n, ph, m, flush=True)
        json.dump(res, open(OUT, "w"), indent=1)
print("DONE", flush=True)

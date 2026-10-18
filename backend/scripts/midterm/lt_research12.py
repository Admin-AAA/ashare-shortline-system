"""[中线/长线] §91 事件型信号：重要股东增持 + 股票回购预案 — 预注册 6 版 + 同市值五分位对照（lt_research6_ctrl 口径）。
数据：backfill_longterm.py events 阶段 -> data/midterm/events/{holdertrade_in,repurchase}_<year>.parquet（2017-07 起，公告日 PIT）。
PIT：公告日 ann_date 映射到 >= ann_date 的第一个交易日 sd 作为信号日，sd 收盘后出信号，次日开盘买（run_real 规则：一字涨停不买、
跌停锁死顺延卖出、0.3% 成本）。若公告在 sd 盘前发布，这只会让买入晚一天（保守）。
增持（holdertrade trade_type=IN，change_ratio 为占总股本 %）：按 (code, sd) 汇总 net = sum(IN 比例)（只回补增持，同日减持不抵扣；结果出来前改定）
  H_any_h20 : net > 0，持 20 日
  H_big_h20 : net >= 0.5%，持 20 日
  H_big_h60 : net >= 0.5%，持 60 日
回购（repurchase）：只用"预案"阶段（proc 含"预案"），同一股票 365 天内只取第一次预案
  R_plan_h20 : 预案，持 20 日
  R_plan_h60 : 预案，持 60 日
  R_big_h60  : 预案且 回购金额上限/总市值 >= 1%，持 60 日
通用：liq（成交额20日>5000万、上市>250日、非ST、非北交所），k_slots=20, max_new=5，排序 H 按 net、R 按 金额/市值。
对照：同一入场/出场日、信号日同市值五分位流动性合格全体股票等权（开盘入→开盘出，同扣成本），每笔超额 = 策略 − 基准。
采纳标准（预先写定，不调参）：8 阶段中每笔超额 > 0 至少 6 个，且 R12 与 W00 超额 > 0，且每笔均值 > 0 至少 5/8；
各阶段 n 中位数 >= 15。h20 达标归 /mid，h60 达标归 /long。"""
import sys, json, glob
import polars as pl
from lt_research import build, fixed, PH
from lt_research2 import regime
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
import lt_data as L
c = pl.col
EV = L.D / "events"

def _pit(e, td):
    e = e.with_columns(c("ann_date").cast(pl.Utf8).str.strip_chars()).filter(c("ann_date").str.len_chars() == 8)
    e = e.with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("ann_date").alias("ann")).sort("ann")
    return e.join_asof(td.rename({"trade_date": "sd"}).with_columns(c("sd").alias("ann2")), left_on="ann", right_on="ann2",
                       strategy="forward").drop_nulls("sd")

def holder_sig(td):
    h = pl.concat([pl.read_parquet(f) for f in sorted(glob.glob(str(EV / "holdertrade_in_*.parquet")))], how="diagonal_relaxed")
    h = h.unique(["ts_code", "ann_date", "holder_name", "in_de", "change_vol"])
    h = _pit(h, td).with_columns(c("change_ratio").cast(pl.Float64, strict=False).fill_null(0))
    return (h.group_by("code", "sd").agg(
                (pl.when(c("in_de") == "IN").then(c("change_ratio")).otherwise(0).sum()
                 - pl.when(c("in_de") == "DE").then(c("change_ratio")).otherwise(0).sum()).alias("net"))
             .rename({"sd": "trade_date"}))

def repo_sig(td):
    r = pl.concat([pl.read_parquet(f) for f in sorted(glob.glob(str(EV / "repurchase_*.parquet")))], how="diagonal_relaxed")
    r = r.filter(c("proc").cast(pl.Utf8).str.contains("预案")).unique(["ts_code", "ann_date"])
    r = _pit(r, td).sort("code", "sd")
    r = r.with_columns(c("sd").str.to_date().alias("d"))
    r = r.with_columns(c("d").shift(1).over("code").alias("pd"))
    r = r.filter(c("pd").is_null() | ((c("d") - c("pd")).dt.total_days() > 365))
    return (r.group_by("code", "sd").agg(c("amount").cast(pl.Float64, strict=False).max().alias("rp_amt"))
             .with_columns(pl.lit(True).alias("rp")).rename({"sd": "trade_date"}))

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lt12.json"
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = regime(df.join(holder_sig(td), on=["code", "trade_date"], how="left").join(repo_sig(td), on=["code", "trade_date"], how="left"))
    rp_ratio = c("rp_amt") / (c("total_mv").cast(pl.Float64, strict=False) * 1e4)
    df = df.with_columns(
        (c("liq") & (c("net") > 0)).fill_null(False).alias("HA"),
        (c("liq") & (c("net") >= 0.5)).fill_null(False).alias("HB"),
        (c("liq") & c("rp").fill_null(False)).fill_null(False).alias("RP"),
        (c("liq") & c("rp").fill_null(False) & (rp_ratio >= 0.01)).fill_null(False).alias("RB"),
        c("net").fill_null(0).alias("sc_h"), rp_ratio.fill_null(0).alias("sc_r"))
    print("signal days per year:", df.filter(c("HA") | c("RP")).group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(c("HA").sum(), c("HB").sum(), c("RP").sum(), c("RB").sum()).sort("y").to_dicts(), flush=True)
    C = {"H_any_h20": ("HA", "sc_h", 20), "H_big_h20": ("HB", "sc_h", 20), "H_big_h60": ("HB", "sc_h", 60),
         "R_plan_h20": ("RP", "sc_r", 20), "R_plan_h60": ("RP", "sc_r", 60), "R_big_h60": ("RB", "sc_r", 60)}
    res = {}
    for n, (sig, sc, h) in C.items():
        print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            m = metrics_real(t, cur)
            if t is not None and t.height:
                b = cohort_bench(df, t)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
            res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(" ", ph, m, flush=True)
        v = res[n].values()
        print("  pos", sum(x.get("avg", -1) > 0 for x in v), "/8  excess+", sum(x.get("excess", -1) > 0 for x in v), "/8", flush=True)
        json.dump(res, open(out, "w"), indent=1)
    print("all done", flush=True)

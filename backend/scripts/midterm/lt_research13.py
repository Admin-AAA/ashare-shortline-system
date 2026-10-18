"""[中线/长线] §92 事件型信号：现金分红预案（高单次股息率）— 预注册 3 版 + 同市值五分位对照（§91 lt_research12 口径）。
数据：data/midterm/dividend/<code>.parquet（tushare dividend，2018+ 覆盖完整，2018 偏少）。只用 div_proc == "预案" 且 cash_div_tax > 0；
同一 (code, end_date) 只取最早一次预案公告。PIT：ann_date -> >= ann_date 的第一个交易日 sd，sd 收盘出信号，次日开盘买（run_real）。
单次股息率 y = cash_div_tax(元/股, 税前) / sd 收盘价(不复权，与每股分红同口径)。收益用 pct_chg 复利（含除息还原）。
  Y2_h20 : y >= 2%，持 20 日 -> /mid
  Y2_h60 : y >= 2%，持 60 日 -> /long
  Y4_h60 : y >= 4%，持 60 日 -> /long
通用：liq（成交额20日>5000万、上市>250日、非ST、非北交所），k_slots=20, max_new=5，按 y 降序。
对照：同入场/出场日、信号日同市值五分位流动性合格全体等权（cohort_bench），每笔超额 = 策略 − 基准。
采纳标准（结果出来前写定，同 §91，不调参）：8 阶段每笔超额 > 0 至少 6 个，且 R12 与 W00 超额 > 0，每笔均值 > 0 至少 5/8，
各阶段 n 中位数 >= 15。送转预案 2024 后每年 < 20 例，无法实盘，不检验。"""
import sys, json, glob
import polars as pl
from lt_research import build, fixed, PH
from lt_research2 import regime
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
import lt_data as L
from lt_research12 import _pit
c = pl.col

def div_sig(td):
    fs = sorted(glob.glob(str(L.D / "dividend" / "*.parquet")))
    d = pl.concat([pl.read_parquet(f, columns=["ts_code", "end_date", "ann_date", "div_proc", "cash_div_tax"]) for f in fs], how="diagonal_relaxed")
    d = d.filter((c("div_proc") == "预案") & (c("cash_div_tax").cast(pl.Float64, strict=False) > 0))
    d = d.with_columns(c("cash_div_tax").cast(pl.Float64, strict=False).alias("cd")).sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    d = _pit(d, td)
    return d.group_by("code", "sd").agg(c("cd").sum()).rename({"sd": "trade_date"})

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lt13.json"
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = regime(df.join(div_sig(td), on=["code", "trade_date"], how="left"))
    y = c("cd") / c("close")
    df = df.with_columns((c("liq") & (y >= 0.02)).fill_null(False).alias("Y2"), (c("liq") & (y >= 0.04)).fill_null(False).alias("Y4"),
                         y.fill_null(0).alias("sc_y"))
    print("signals per year:", df.filter(c("Y2")).group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(c("Y2").sum(), c("Y4").sum()).sort("y").to_dicts(), flush=True)
    C = {"Y2_h20": ("Y2", 20), "Y2_h60": ("Y2", 60), "Y4_h60": ("Y4", 60)}
    res = {}
    for n, (sig, h) in C.items():
        print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, sig, "sc_y", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
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

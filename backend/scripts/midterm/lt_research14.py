"""[中线/长线] §93 事件型信号：定期报告业绩超预期漂移（PEAD，实际财报而非预告）— 预注册 3 版 + 同市值五分位对照（§91 口径）。
数据：data/midterm/fina/<code>.parquet（tushare fina_indicator，按公告日 PIT）。同一 (code, end_date) 只取最早一次公告。
PIT：ann_date -> >= ann_date 的第一个交易日 sd，sd 收盘出信号，次日开盘买（run_real）。
变量（均为累计口径同比，%）：dtp = dt_netprofit_yoy（扣非净利同比），orv = or_yoy（营收同比），profit_dedt（累计扣非净利，元）；
prev_dtp = 同一公司上一期报告（按 end_date 排序）的 dt_netprofit_yoy。
  G_h20 : dtp >= 50 且 orv >= 20 且 profit_dedt > 0 且 dtp > prev_dtp（加速），持 20 日 -> /mid
  G_h60 : 同 G 条件，持 60 日 -> /long
  B_h20 : dtp >= 30 且 orv >= 15 且 profit_dedt > 0（不要求加速，宽版），持 20 日 -> /mid
排序分：min(dtp, 300) 降序。通用：liq（成交额20日>5000万、上市>250日、非ST、非北交所），k_slots=20, max_new=5。
对照：同入场/出场日、信号日同市值五分位流动性合格全体等权（cohort_bench），每笔超额 = 策略 − 基准。
采纳标准（结果出来前写定，同 §91/§92，不调参）：8 阶段每笔超额 > 0 至少 6 个，且 R12 与 W00 超额 > 0，每笔均值 > 0 至少 5/8，
各阶段 n 中位数 >= 15。已否决的"业绩预告漂移"是预告事件，本检验是正式财报事件，口径不同。"""
import sys, json, glob
import polars as pl
from lt_research import build, fixed, PH
from lt_research2 import regime
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
import lt_data as L
from lt_research12 import _pit
c = pl.col
COLS = ["ts_code", "ann_date", "end_date", "dt_netprofit_yoy", "or_yoy", "profit_dedt"]

def fina_sig(td):
    fs = sorted(glob.glob(str(L.D / "fina" / "*.parquet")))
    parts = []
    for f in fs:
        try:
            x = pl.read_parquet(f)
        except Exception:
            continue
        if not set(COLS) <= set(x.columns): continue
        parts.append(x.select([c(k).cast(pl.Utf8) if k in ("ts_code", "ann_date", "end_date") else c(k).cast(pl.Float64, strict=False) for k in COLS]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = d.with_columns(c("dt_netprofit_yoy").shift(1).over("ts_code").alias("prev_dtp"))
    d = _pit(d, td)
    return (d.sort("end_date").group_by("code", "sd").agg(c("dt_netprofit_yoy").last().alias("dtp"), c("or_yoy").last().alias("orv"),
             c("profit_dedt").last().alias("pdd"), c("prev_dtp").last()).rename({"sd": "trade_date"}))

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lt14.json"
    df = build()
    td = df.select("trade_date").unique().sort("trade_date")
    df = regime(df.join(fina_sig(td), on=["code", "trade_date"], how="left"))
    base = c("liq") & (c("pdd") > 0)
    G = base & (c("dtp") >= 50) & (c("orv") >= 20) & (c("dtp") > c("prev_dtp"))
    B = base & (c("dtp") >= 30) & (c("orv") >= 15)
    df = df.with_columns(G.fill_null(False).alias("G"), B.fill_null(False).alias("B"), c("dtp").clip(-1e9, 300).fill_null(0).alias("sc_g"))
    print("signals per year:", df.filter(c("B")).group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(c("G").sum(), c("B").sum()).sort("y").to_dicts(), flush=True)
    C = {"G_h20": ("G", 20), "G_h60": ("G", 60), "B_h20": ("B", 20)}
    res = {}
    for n, (sig, h) in C.items():
        print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, sig, "sc_g", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
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

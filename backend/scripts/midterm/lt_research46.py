"""[中线/长线] §154 三个未测过的新方向 — 预注册（docs/strategy-research.md §154，结果出来前写定，不调参）。
口径同 lt_research25：realistic 引擎（开盘涨停不买、跌停锁死顺延、0.3% 成本）、liq、K=20、max_new=5（DMP 为 10）、
cohort_bench 同日同市值五分位对照。只用本地 parquet。
  A. EAR：正式财报 ann_date -> 首个交易日 sd；EAR = sd-1 收盘到 sd+1 收盘复权涨幅 - 同窗口全市场等权涨幅；
     sd+1 收盘出信号、次日开盘买，按 EAR 降序。EAR_h20（EAR>=5%，持20）/ EARq_h20（再要求净利同比>0）/ EAR_h60（持60）
  B. DIVX15 / DIVX30：分红实施公告 imp_ann_date -> 首个交易日 sd 收盘出信号，每股税前派现/收盘 >= 1.5% / 3%，
     次日开盘买，股权登记日开盘卖（不拿分红），最长 20 日；要求登记日晚于买入日。
     DMP_h20：每月首个交易日，去年同月有现金分红除息、近 60 天没有除息、dv_ttm>=2，按 dv_ttm 取前 10，持 20。
  C. 减持回避：信号日前 40 个交易日内已公告的重要股东减持合计 >= 总股本 1% 不买；用于 5 个现役中线策略。
用法：python lt_research46.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
from lt_research12 import _pit
import lt_research25 as R25
import lt_data as L
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
EV = L.D / "events"
EAR_TH, RED_TH, RED_WIN = 0.05, 0.01, 40
BASE = {"EA_h20": ("G", "sc_g"), "F_any_h20": ("FA", "sc_a"), "RP_r05": ("RP_r05", "sc_r"),
        "INC_opt_h20": ("IO", "sc_i"), "BT_prem_h20": ("BTP", "sc_bt")}


def ear_events(td):
    """(code, sd, npy) 每个 (ts_code, end_date) 最早一次正式财报公告。"""
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date", "netprofit_yoy"])
        except Exception:
            continue
        parts.append(x.with_columns(c("netprofit_yoy").cast(pl.Float64, strict=False),
                                    *[c(k).cast(pl.Utf8) for k in ("ts_code", "ann_date", "end_date")]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    d = _pit(d, td)
    return d.sort("end_date").group_by("code", "sd").agg(c("netprofit_yoy").last().alias("npy")).with_columns(pl.lit(True).alias("ev_sd"))


def dividend_rows():
    cols = ["ts_code", "div_proc", "cash_div_tax", "record_date", "ex_date", "imp_ann_date"]
    d = pl.concat([pl.read_parquet(f, columns=cols) for f in sorted(glob.glob(str(L.D / "dividend" / "*.parquet")))],
                  how="diagonal_relaxed")
    d = d.with_columns(c("cash_div_tax").cast(pl.Float64, strict=False).fill_null(0),
                       *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("record_date", "ex_date", "imp_ann_date")])
    return d.filter((c("div_proc") == "实施") & (c("cash_div_tax") > 0)).unique(["ts_code", "imp_ann_date", "record_date", "cash_div_tax"])


def divx_events(d, td):
    """(code, sd, dps, drd) 实施公告事件；drd = 股权登记日 YYYY-MM-DD。"""
    e = d.filter(c("imp_ann_date").str.len_chars() == 8).filter(c("record_date").str.len_chars() == 8)
    e = _pit(e.rename({"imp_ann_date": "ann_date"}), td).with_columns(L._dash("record_date").alias("drd"))
    return e.group_by("code", "sd").agg(c("cash_div_tax").sum().alias("dps"), c("drd").min())


def dmp_flags(df, d):
    ex = d.filter(c("ex_date").str.len_chars() == 8).select(c("ts_code").str.slice(0, 6).alias("code"),
                                                             L._dash("ex_date").alias("exd")).unique()
    ly = ex.select("code", c("exd").str.slice(0, 7).alias("ly_ym")).unique().with_columns(pl.lit(True).alias("ly_ex"))
    df = df.with_columns(((c("ym").str.slice(0, 4).cast(pl.Int32) - 1).cast(pl.Utf8) + c("ym").str.slice(4, 3)).alias("ly_ym"))
    df = df.join(ly, on=["code", "ly_ym"], how="left")
    df = df.sort("trade_date").join_asof(ex.sort("exd").with_columns(c("exd").alias("trade_date")), on="trade_date",
                                         by="code", strategy="backward")
    gap = (c("trade_date").str.to_date() - c("exd").str.to_date()).dt.total_days()
    return df.with_columns((gap.is_null() | (gap > 60)).alias("no_ex60")).drop("ly_ym", "exd").sort("code", "trade_date")


def reduction(df, td):
    """red40 = 前 40 个交易日（含信号日）已公告减持合计 / 总股本 >= 1%。"""
    h = pl.concat([pl.read_parquet(f) for f in sorted(glob.glob(str(EV / "holdertrade_de_*.parquet")))], how="diagonal_relaxed")
    h = h.unique(["ts_code", "ann_date", "holder_name", "in_de", "change_vol"]).filter(c("in_de") == "DE")
    h = _pit(h, td).with_columns(c("change_vol").cast(pl.Float64, strict=False).fill_null(0))
    h = h.group_by("code", "sd").agg(c("change_vol").sum().alias("de_vol")).rename({"sd": "trade_date"})
    px = df.select("code", c("trade_date").alias("bd"), c("close").alias("close_bd"))
    df = df.join(px, on=["code", "bd"], how="left").join(h, on=["code", "trade_date"], how="left")
    shares = c("total_mv").cast(pl.Float64, strict=False) * 1e4 / c("close_bd")  # 万元 -> 元 / 元每股 = 股
    df = df.with_columns((c("de_vol").fill_null(0) / shares).fill_null(0).alias("de_r")).sort("code", "trade_date")
    return df.with_columns((c("de_r").rolling_sum(RED_WIN, min_samples=1).over("code") >= RED_TH).alias("red40")).drop("close_bd")


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    # A. EAR
    ew = (df.group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
            .with_columns((1 + c("s").fill_null(0) / 100).cum_prod().alias("ewi")).select("trade_date", "ewi"))
    df = df.join(ew, on="trade_date", how="left").join(ear_events(td).rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
    df = df.sort("code", "trade_date").with_columns(
        c("ev_sd").shift(1).over("code").fill_null(False).alias("ev_prev"), c("npy").shift(1).over("code").alias("npy_prev"),
        (c("ac") / c("ac").shift(2).over("code") - c("ewi") / c("ewi").shift(2).over("code")).alias("ear"))
    ea = c("liq") & c("ev_prev") & (c("ear") >= EAR_TH)
    # B. dividend
    d = dividend_rows()
    df = df.join(divx_events(d, td).rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
    nxt = td.with_columns(c("trade_date").shift(-1).alias("nxt"))
    df = df.join(nxt, on="trade_date", how="left")
    dy = c("dps") / c("close")
    dx = c("liq") & c("dps").is_not_null() & (c("drd") > c("nxt"))
    df = dmp_flags(df, d)
    dmp = c("liq") & c("mon") & c("ly_ex").fill_null(False) & c("no_ex60") & (c("dv_ttm") >= 2)
    # C. reduction filter
    df = reduction(df, td)
    cols = [(c(sig) & ~c("red40")).fill_null(False).alias(f"{sig}__red") for sig, _ in BASE.values()]
    return df.with_columns(
        ea.fill_null(False).alias("EAR"), (ea & (c("npy_prev") > 0)).fill_null(False).alias("EARq"),
        (c("ear").fill_null(0) - tie).alias("sc_ear"),
        (dx & (dy >= 0.015)).fill_null(False).alias("DX15"), (dx & (dy >= 0.03)).fill_null(False).alias("DX30"),
        (dy.fill_null(0) - tie).alias("sc_dy"),
        dmp.fill_null(False).alias("DMP"), (c("dv_ttm").fill_null(0) - tie).alias("sc_dv2"), *cols).sort("code", "trade_date")


def divx_exit(a, e, i):
    return i + 1 < len(a["trade_date"]) and a["trade_date"][i + 1] >= a["drd"][e - 1]


NEW = {"EAR_h20": ("EAR", "sc_ear", fixed(20), 20, 5), "EARq_h20": ("EARq", "sc_ear", fixed(20), 20, 5),
       "EAR_h60": ("EAR", "sc_ear", fixed(60), 60, 5), "DIVX15": ("DX15", "sc_dy", divx_exit, 20, 5),
       "DIVX30": ("DX30", "sc_dy", divx_exit, 20, 5), "DMP_h20": ("DMP", "sc_dv2", fixed(20), 20, 10)}


def full_metrics(df, t, cur):
    m = metrics_real(t, cur)
    if t is not None and t.height:
        b = cohort_bench(df, t)
        m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
        m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
    return {a: float(x) for a, x in m.items()}


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(*[c(x).sum() for x in ("EAR", "EARq", "DX15", "DX30", "DMP", "red40")])
          .sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, sc, fn, h, mn) in NEW.items():
            t, cur = run_real(df, sig, sc, fn, s, e, k_slots=20, max_new=mn, max_hold=h)
            res.setdefault(n, {})[ph] = full_metrics(df, t, cur)
            print(n, ph, res[n][ph], flush=True)
        for n, (sig, sc) in BASE.items():
            for v, col in (("base", sig), ("red", f"{sig}__red")):
                t, cur = run_real(df, col, sc, fixed(20), s, e, k_slots=20, max_new=5, max_hold=20)
                res.setdefault(f"C_{n}", {}).setdefault(v, {})[ph] = full_metrics(df, t, cur)
                print("C", n, v, ph, res[f"C_{n}"][v][ph], flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

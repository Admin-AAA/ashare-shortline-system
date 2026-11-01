"""[中线/长线] §111 送转预案 / 除权填权漂移 — 预注册 3 版（/workspace/strategy-research.md §111，结果出来前写定，不调参）。
（§111 原题"研报评级上调/首次覆盖"：网关 report_rc 只有 promax、必须带 ts_code、单次 ≤1 年，全历史需 ~4 万次调用，data-unavailable。）
数据：data/midterm/dividend/<code>.parquet（tushare dividend）。stk_div = 每股送转（0.3 = 10送转3）。
  预案事件：div_proc ∈ {预案, 预披露, 股东提议} 且 stk_div>=0.3，同一 (ts_code, end_date) 取最早 ann_date；
    PIT：ann_date -> >= ann_date 的首个交易日 sd，sd 收盘出信号，次日开盘买。
  除权事件：div_proc == 实施 且 stk_div>=0.3；sd = ex_date 前一个交易日；要求 imp_ann_date < sd；ex_date 开盘买。
  SZ_h20    : 预案，持 20 -> /mid
  SZ_EX_h20 : 除权，持 20 -> /mid
  SZ_h60    : 预案，持 60 -> /long
排序分 = stk_div 降序，其次 r20 升序，再代码升序。liq，k_slots=20, max_new=5（run_real：开盘涨停不买、跌停锁死顺延、0.3% 成本）。
cohort_bench 同日同市值五分位对照。参照（算重叠）：EA_h20、F_any_h20、RP_r05、INC_opt_h20、SCW（/mid），RP_big_h60（/long long-repurchase-big）。
用法：LT_START=.. LT_END=.. python lt_research24.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
from lt_research12 import _pit
import lt_research20 as R20
import lt_research23 as R23
import lt_data as L
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
TH = 0.3
PRE_PROC = ["预案", "预披露", "股东提议"]


def dividend_rows():
    fs = sorted(glob.glob(str(L.D / "dividend" / "*.parquet")))
    cols = ["ts_code", "end_date", "ann_date", "div_proc", "stk_div", "imp_ann_date", "ex_date"]
    d = pl.concat([pl.read_parquet(f, columns=cols) for f in fs], how="diagonal_relaxed")
    return d.with_columns(c("stk_div").cast(pl.Float64, strict=False).fill_null(0),
                          *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ann_date", "imp_ann_date", "ex_date", "end_date")])


def pre_events(d=None):
    d = dividend_rows() if d is None else d
    p = d.filter(c("div_proc").is_in(PRE_PROC) & (c("stk_div") >= TH)).filter(c("ann_date").str.len_chars() == 8)
    return p.sort("ann_date").unique(["ts_code", "end_date"], keep="first", maintain_order=True)


def ex_events(d=None):
    d = dividend_rows() if d is None else d
    x = d.filter((c("div_proc") == "实施") & (c("stk_div") >= TH)
                 & (c("ex_date").str.len_chars() == 8) & (c("imp_ann_date").str.len_chars() == 8))
    return x.unique(["ts_code", "end_date", "ex_date"])


def sz_sig(td):
    d = dividend_rows()
    p = _pit(pre_events(d), td)
    pre = (p.group_by("code", "sd").agg(c("stk_div").max().alias("sz_pre")).rename({"sd": "trade_date"}))
    x = ex_events(d).with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("ex_date").alias("exd"),
                                  L._dash("imp_ann_date").alias("imp"))
    tdl = td.rename({"trade_date": "sd"}).with_columns(c("sd").alias("k")).sort("k")
    # sd = 严格早于 ex_date 的最后一个交易日：用 ex_date-1 天 backward asof
    x = x.with_columns((c("exd").str.to_date() - pl.duration(days=1)).dt.strftime("%Y-%m-%d").alias("k")).sort("k")
    x = x.join_asof(tdl, on="k", strategy="backward").drop_nulls("sd").filter(c("imp") < c("sd"))
    ex = (x.group_by("code", "sd").agg(c("stk_div").max().alias("sz_ex")).rename({"sd": "trade_date"}))
    return pre, ex


def prep():
    df = R23.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    pre, ex = sz_sig(td)
    df = df.join(pre, on=["code", "trade_date"], how="left").join(ex, on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    r20 = c("r20").fill_null(0).clip(-5, 5)
    return df.with_columns(
        (c("liq") & c("sz_pre").is_not_null()).fill_null(False).alias("SZP"),
        (c("liq") & c("sz_ex").is_not_null()).fill_null(False).alias("SZX"),
        (c("sz_pre").fill_null(0) * 1e4 - r20 - tie).alias("sc_zp"),
        (c("sz_ex").fill_null(0) * 1e4 - r20 - tie).alias("sc_zx"),
        (c("liq") & c("rp").fill_null(False) & (c("sc_r") >= 0.01)).fill_null(False).alias("RP_big"),
    ).sort("code", "trade_date")


C = {"SZ_h20": ("SZP", "sc_zp", 20, 5), "SZ_EX_h20": ("SZX", "sc_zx", 20, 5), "SZ_h60": ("SZP", "sc_zp", 60, 5),
     "EA_h20": ("G", "sc_g", 20, 5), "F_any_h20": ("FA", "sc_a", 20, 5), "RP_r05": ("RP_r05", "sc_r", 20, 5),
     "INC_opt_h20": ("IO", "sc_i", 20, 5), "SCW": ("SCW", "sc_small", 20, 10), "RP_big_h60": ("RP_big", "sc_r", 60, 5)}
REFS = ("EA_h20", "F_any_h20", "RP_r05", "INC_opt_h20", "SCW", "RP_big_h60")
VARS = ("SZ_h20", "SZ_EX_h20", "SZ_h60")

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("signal days per year:", df.filter(c("SZP") | c("SZX")).group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(c("SZP").sum(), c("SZX").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        trades = {}
        for n, (sig, sc, h, mn) in C.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=mn, max_hold=h)
            m = metrics_real(t, cur)
            if t is not None and t.height:
                b = cohort_bench(df, t)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
                r = t["ret"].to_numpy()
                m["avg_win"] = round(100 * float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0
                m["avg_loss"] = round(100 * float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0
                streak = mx = 0
                for x in t.sort("entry")["ret"].to_list():
                    streak = streak + 1 if x <= 0 else 0; mx = max(mx, streak)
                m["max_loss_streak"] = mx
            trades[n] = t
            res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(n, ph, m, flush=True)
        for n in VARS:
            for rname in REFS:
                res[n][ph][f"overlap_{rname}"] = R20.overlap(trades[n], trades[rname])
            print("  overlap", n, {r: res[n][ph][f"overlap_{r}"]["frac_hold_overlap"] for r in REFS}, flush=True)
        if ph in ("R12 2025-10~2026-10", "N3 2024-01~2026-10"):
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            for n, t in trades.items():
                if n in VARS and t is not None and t.height:
                    t.write_csv(out.replace(".json", f"_{n}_{ph.split()[0]}_trades.csv"))
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

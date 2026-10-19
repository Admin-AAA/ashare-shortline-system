"""[中线/长线] §116 业绩快报（tushare express）首次快报正向漂移 — 预注册 3 版（/workspace/strategy-research.md §116，结果出来前写定，不调参）。
数据：output/research/sprint4/express/express_YYYYMM.parquet + express/income/<code>.parquet
  （scripts/midterm/fetch_express_cache.py，统一数据网关 tushare express / income，只读，不写库）。
事件：同一 (code, end_date) 只取最早 ann_date 的快报（同日多行取 update_flag 最小）；np = n_income，np0 = yoy_net_profit（去年同期净利润）；
  g = np/np0 − 1（np0>0）；np0<=0 且 np>0 = 扭亏（排序分 300）；
  rg = revenue / rev0 − 1，rev0 = income 去年同期 report_type=1、ann_date < 快报 ann_date 的最早一行 revenue（空则 total_revenue）。
  去重：同 (code, end_date) 已有业绩预告（data/midterm/forecast，ann<=快报 ann）或正式报告（data/midterm/fina 或 income 同期，ann<=快报 ann）→ 不算信号。
PIT：ann_date -> >= ann_date 的首个交易日 sd，sd 收盘出信号，次日开盘买（run_real：开盘涨停不买、跌停锁死顺延、0.3% 成本）。
  E_any_h20 : np>0 且 (g>0 或扭亏)，持 20 -> /mid
  E_big_h20 : np>0 且 (g>=50% 或扭亏) 且 rg>0，持 20 -> /mid
  E_big_h60 : 同 E_big，持 60 -> /long
排序分 = min(g*100, 300) 降序（扭亏 300），平手按代码升序；同 (code, sd) 多期取任一合格、分数取最大。liq，k_slots=20, max_new=5。
参照（同进程重算、算重叠）：EA_h20（mid-earnings-accel）、F_any_h20（mid-forecast-beat）。cohort_bench 同市值五分位对照。
用法：LT_START=.. LT_END=.. python lt_research26.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
from pathlib import Path
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real
from lt_research6_ctrl import cohort_bench
from lt_research12 import _pit
import lt_research20 as R20
import lt_data as L
c = pl.col
PH = dict(PH); PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
EX = Path(__file__).resolve().parents[2] / "output/research/sprint4/express"
CAP = 300.0


def _ok_code(col="ts_code"):
    return (c(col).str.slice(0, 6).str.contains(r"^\d{6}$") & ~c(col).str.slice(0, 1).is_in(["4", "8", "9"])
            & ~c(col).str.ends_with(".BJ"))


def load_express() -> pl.DataFrame:
    d = pl.concat([pl.read_parquet(f) for f in sorted(glob.glob(str(EX / "express_*.parquet")))], how="diagonal_relaxed")
    d = d.select(c("ts_code").cast(pl.Utf8), c("ann_date").cast(pl.Utf8).str.strip_chars(), c("end_date").cast(pl.Utf8).str.strip_chars(),
                 *[c(k).cast(pl.Float64, strict=False) for k in ("revenue", "n_income", "yoy_net_profit", "update_flag")])
    d = d.filter(_ok_code() & (c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    return d.sort("ann_date", c("update_flag").fill_null(0)).unique(["ts_code", "end_date"], keep="first", maintain_order=True)


def _first_ann(files, cols=("ts_code", "ann_date", "end_date"), extra=None):
    parts = []
    for f in files:
        try:
            x = pl.read_parquet(f, columns=list(cols) + (extra or []))
        except Exception:
            continue
        if x.height: parts.append(x.select([c(k).cast(pl.Utf8) for k in cols] + [c(k) for k in (extra or [])]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    return d.with_columns(c("ann_date").str.strip_chars(), c("end_date").str.strip_chars()).filter(c("ann_date").str.len_chars() == 8)


def express_events() -> pl.DataFrame:
    e = load_express()
    fc = _first_ann(sorted(glob.glob(str(L.D / "forecast" / "*.parquet")))).group_by("ts_code", "end_date").agg(c("ann_date").min().alias("fc_ann"))
    fi = _first_ann(sorted(glob.glob(str(L.D / "fina" / "*.parquet")))).group_by("ts_code", "end_date").agg(c("ann_date").min().alias("fi_ann"))
    inc = _first_ann(sorted(glob.glob(str(EX / "income" / "*.parquet"))), extra=["report_type", "revenue", "total_revenue"])
    inc = inc.filter(c("report_type").cast(pl.Utf8) == "1").with_columns(
        c("revenue").cast(pl.Float64, strict=False).fill_null(c("total_revenue").cast(pl.Float64, strict=False)).alias("rev"))
    in_ann = inc.group_by("ts_code", "end_date").agg(c("ann_date").min().alias("in_ann"))
    e = (e.join(fc, on=["ts_code", "end_date"], how="left").join(fi, on=["ts_code", "end_date"], how="left")
          .join(in_ann, on=["ts_code", "end_date"], how="left"))
    prior = ((c("fc_ann").is_not_null() & (c("fc_ann") <= c("ann_date"))) | (c("fi_ann").is_not_null() & (c("fi_ann") <= c("ann_date")))
             | (c("in_ann").is_not_null() & (c("in_ann") <= c("ann_date"))))
    e = e.with_columns(prior.fill_null(False).alias("prior"), c("fc_ann").is_not_null().alias("has_fc"),
                       ((c("end_date").str.slice(0, 4).cast(pl.Int32) - 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_prev"))
    # 去年同期营收：income report_type=1、end_date=end_prev、ann_date < 快报 ann 的最早一行
    r0 = (e.select("ts_code", "end_date", "ann_date", "end_prev")
           .join(inc.select("ts_code", c("end_date").alias("end_prev"), c("ann_date").alias("a0"), "rev"), on=["ts_code", "end_prev"])
           .filter((c("a0") < c("ann_date")) & c("rev").is_not_null()).sort("a0")
           .group_by("ts_code", "end_date").agg(c("rev").first().alias("rev0")))
    e = e.join(r0, on=["ts_code", "end_date"], how="left")
    np_, np0 = c("n_income"), c("yoy_net_profit")
    turn = (np0 <= 0) & (np_ > 0)
    g = pl.when(np0 > 0).then(np_ / np0 - 1)
    rg = pl.when(c("rev0") > 0).then(c("revenue") / c("rev0") - 1)
    e = e.with_columns(turn.fill_null(False).alias("turn"), g.alias("g"), rg.alias("rg"))
    anyp = (np_ > 0) & ((c("g") > 0) | c("turn"))
    big = (np_ > 0) & ((c("g") >= 0.5) | c("turn")) & (c("rg") > 0)
    sc = pl.when(c("turn")).then(CAP).otherwise((c("g") * 100).clip(-1e9, CAP))
    return e.with_columns(anyp.fill_null(False).alias("xa_raw"), big.fill_null(False).alias("xb_raw"), sc.alias("xsc"),
                          (anyp.fill_null(False) & ~c("prior")).alias("xa"), (big.fill_null(False) & ~c("prior")).alias("xb"))


def ex_sig(td):
    e = express_events().filter(c("xa"))  # xb ⊂ xa
    e = _pit(e, td)
    return (e.group_by("code", "sd").agg(c("xa").any().alias("xa"), c("xb").any().alias("xb"),
                                         c("xsc").max().alias("sc_xa"), pl.when(c("xb")).then(c("xsc")).max().alias("sc_xb"))
             .rename({"sd": "trade_date"}))


def prep():
    df = R20.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    df = df.join(ex_sig(td), on=["code", "trade_date"], how="left")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    return df.with_columns(
        (c("liq") & c("xa").fill_null(False)).fill_null(False).alias("XA"),
        (c("liq") & c("xb").fill_null(False)).fill_null(False).alias("XB"),
        (c("sc_xa").fill_null(0) - tie).alias("sc_xa"), (c("sc_xb").fill_null(0) - tie).alias("sc_xb")).sort("code", "trade_date")


C = {"E_any_h20": ("XA", "sc_xa", 20, 5), "E_big_h20": ("XB", "sc_xb", 20, 5), "E_big_h60": ("XB", "sc_xb", 60, 5),
     "EA_h20": ("G", "sc_g", 20, 5), "F_any_h20": ("FA", "sc_a", 20, 5)}
REFS = ("EA_h20", "F_any_h20")
VARS = ("E_any_h20", "E_big_h20", "E_big_h60")


def event_stats():
    e = express_events()
    y = c("ann_date").str.slice(0, 4).alias("y")
    return (e.group_by(y).agg(pl.len().alias("first_express"), c("prior").sum().alias("prior_fc_or_report"), c("has_fc").sum().alias("has_fc_any"),
                              c("rev0").is_not_null().sum().alias("rev0_ok"), c("xa_raw").sum(), c("xb_raw").sum(), c("xa").sum(), c("xb").sum())
             .sort("y").to_dicts())


if __name__ == "__main__":
    if sys.argv[1] == "stats":
        for r in event_stats(): print(r)
        sys.exit(0)
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    print("signal days per year (liq):", df.filter(c("XA") | c("FA")).group_by(c("trade_date").str.slice(0, 4).alias("y"))
          .agg(c("XA").sum(), c("XB").sum(), c("FA").sum(), (c("XA") & c("FA")).sum().alias("XA_and_FA")).sort("y").to_dicts(), flush=True)
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

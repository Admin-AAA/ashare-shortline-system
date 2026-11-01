"""[中线/长线] §160 财报公告前溢价 EAP — 预注册（docs/strategy-research.md §160，结果出来前写定，不调参）。
口径同 lt_research48：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。只用本地 fina parquet。
  预期披露日 E = 去年同一报告期最早公告日 + 1 年；sd = E-14 自然日当天或之后首个交易日；sd 时本期尚未公告且 sd > 期末日。
  排序 hash(代码, 日期) 随机。EAP_h20 / EAPg_h20（sd 时最近一次已公告财报净利同比>0）/ EAPg_h60。
用法：LT_START=<phase start> LT_END=<phase end+120d> PYTHONPATH=../.. python lt_research49.py "<phase>" out.json"""
import sys, json, os, glob, datetime as dt
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
LEAD_DAYS = 14


def fina_first():
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date", "netprofit_yoy"])
        except Exception:
            continue
        parts.append(x.with_columns(c("netprofit_yoy").cast(pl.Float64, strict=False),
                                    *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ts_code", "ann_date", "end_date")]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    return d.with_columns(c("ts_code").str.slice(0, 6).alias("code"), L._dash("ann_date").alias("ann"), L._dash("end_date").alias("pend"))


def eap_events(td):
    f = fina_first()
    prev = f.select("code", ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
                    c("ann").alias("prev_ann"))
    # 目标期 = 去年同期 +1 年；E = prev_ann + 1 年（2/29 -> 2/28）
    ev = prev.with_columns(L._dash("end_date").alias("pend"),
                           ((c("prev_ann").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + "-" + c("prev_ann").str.slice(5, 5).str.replace("02-29", "02-28")).alias("E"))
    ev = ev.with_columns(c("E").str.to_date().dt.offset_by(f"-{LEAD_DAYS}d").dt.strftime("%Y-%m-%d").alias("s0")).sort("s0")
    ev = ev.join_asof(td.rename({"trade_date": "sd"}).with_columns(c("sd").alias("sd2")), left_on="s0", right_on="sd2",
                      strategy="forward").drop_nulls("sd")
    cur = f.select("code", "end_date", c("ann").alias("cur_ann"))
    ev = ev.join(cur, on=["code", "end_date"], how="left")
    ev = ev.filter((c("cur_ann").is_null() | (c("cur_ann") > c("sd"))) & (c("sd") > c("pend")))
    # 最近一次已公告（ann <= sd）财报的净利同比
    last = f.select("code", c("ann").alias("la"), c("netprofit_yoy").alias("npy"), "pend").sort("la", "pend") \
            .group_by("code", "la").agg(c("npy").last()).sort("la")
    ev = ev.sort("sd").join_asof(last.rename({"la": "la_"}).with_columns(c("la_").alias("lak")), left_on="sd", right_on="lak",
                                 by="code", strategy="backward")
    return ev.group_by("code", "sd").agg(c("npy").max().alias("npy"), pl.len().alias("nper"))


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    ev = eap_events(td).rename({"sd": "trade_date"}).with_columns(pl.lit(True).alias("ev"))
    df = df.join(ev, on=["code", "trade_date"], how="left")
    rnd = (pl.concat_str([c("code"), c("trade_date")]).hash(160) % 1000003).cast(pl.Float64)
    sig = (c("liq") & c("ev").fill_null(False)).fill_null(False)
    return df.with_columns(sig.alias("EAP"), (sig & (c("npy") > 0)).fill_null(False).alias("EAPg"),
                           rnd.alias("sc_rand")).sort("code", "trade_date")


NEW = {"EAP_h20": ("EAP", 20), "EAPg_h20": ("EAPg", 20), "EAPg_h60": ("EAPg", 60)}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(c("EAP").sum(), c("EAPg").sum(), c("ev").fill_null(False).sum().alias("raw")).sort("y").to_dicts(), flush=True)
    print("signals per month (EAP):", df.filter(c("EAP")).group_by(c("trade_date").str.slice(5, 2).alias("m")).agg(pl.len()).sort("m").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, h) in NEW.items():
            t, cur = run_real(df, sig, "sc_rand", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            res.setdefault(n, {})[ph] = full_metrics(df, t, cur)
            print(n, ph, res[n][ph], flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

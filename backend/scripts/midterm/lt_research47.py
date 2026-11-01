"""[中线/长线] §155 又三个未测过的事件方向 — 预注册（docs/strategy-research.md §155，结果出来前写定，不调参）。
口径同 lt_research46：realistic 引擎、liq、K=20、max_new=5、cohort_bench 同日同市值五分位对照。
  A. EARLY：正式财报披露时滞（公告日-期末日）比去年同报告期提前 >= 15 天，按提前天数降序。持 20 / 60。
  B. FREV：同一报告期第二次及以后的业绩预告，净利中值较上一次上修 >= 20% 且 > 0，早于正式财报，按上修幅度降序。持 20 / 60。
  C. DINIT：分红预案每股派现 > 0、公告日前 3 年没实施过现金分红、上市满 3 年，按股息率降序。持 20 / 60。
用法：PYTHONPATH=backend python lt_research47.py "<phase>,<phase>" out.json"""
import sys, json, os, glob
import polars as pl
from sqlalchemy import text
from lt_research import fixed
from realistic import run_real
from lt_research12 import _pit
from lt_research46 import PH, full_metrics
import lt_research25 as R25
import lt_data as L
c = pl.col
EARLY_TH, REV_TH, GAP_Y = 15, 0.20, 3


def early_events(td):
    parts = [pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date"]) for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet")))]
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.filter((c("ann_date").str.len_chars() == 8) & (c("end_date").str.len_chars() == 8))
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first")
    d = d.with_columns((c("ann_date").str.to_date("%Y%m%d") - c("end_date").str.to_date("%Y%m%d")).dt.total_days().alias("lag"))
    prev = d.select("ts_code", ((c("end_date").str.slice(0, 4).cast(pl.Int32) + 1).cast(pl.Utf8) + c("end_date").str.slice(4, 4)).alias("end_date"),
                    c("lag").alias("lag_prev"))
    d = d.join(prev, on=["ts_code", "end_date"], how="inner").with_columns((c("lag_prev") - c("lag")).alias("early"))
    d = _pit(d.filter(c("early") >= EARLY_TH), td)
    return d.group_by("code", "sd").agg(c("early").max())


def fina_first_ann():
    parts = [pl.read_parquet(f, columns=["ts_code", "ann_date", "end_date"]) for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet")))]
    return pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"]).group_by("ts_code", "end_date").agg(
        c("ann_date").min().alias("rep_ann"))


def frev_events(td):
    f = pl.concat([pl.read_parquet(x, columns=["ts_code", "ann_date", "end_date", "net_profit_min", "net_profit_max"])
                   for x in sorted(glob.glob(str(L.D / "forecast" / "*.parquet")))], how="diagonal_relaxed")
    f = f.drop_nulls(["ann_date", "end_date"]).filter(c("ann_date").str.len_chars() == 8)
    mid = pl.when(c("net_profit_min").is_not_null() & c("net_profit_max").is_not_null()).then((c("net_profit_min") + c("net_profit_max")) / 2) \
        .otherwise(pl.coalesce("net_profit_min", "net_profit_max"))
    f = f.with_columns(mid.alias("mid")).drop_nulls("mid").group_by("ts_code", "end_date", "ann_date").agg(c("mid").mean())
    f = f.sort("ts_code", "end_date", "ann_date").with_columns(c("mid").shift(1).over("ts_code", "end_date").alias("prev"))
    f = f.with_columns(((c("mid") - c("prev")) / c("prev").abs()).alias("rev"))
    f = f.filter(c("prev").is_not_null() & (c("prev") != 0) & (c("mid") > 0) & (c("rev") >= REV_TH))
    f = f.join(fina_first_ann(), on=["ts_code", "end_date"], how="left").filter(c("rep_ann").is_null() | (c("ann_date") < c("rep_ann")))
    return _pit(f, td).group_by("code", "sd").agg(c("rev").max())


def list_dates():
    from app.db.session import engine
    with engine.connect() as cn:
        rows = cn.execute(text("SELECT code, list_date FROM stocks UNION ALL SELECT code, list_date FROM stock_metadata_history")).fetchall()
    d = pl.DataFrame([(r[0], str(r[1] or "").replace("-", "")[:8]) for r in rows], schema=["code", "ld"], orient="row")
    return d.filter(c("ld").str.len_chars() == 8).group_by("code").agg(c("ld").min())


def dinit_events(td):
    cols = ["ts_code", "end_date", "ann_date", "div_proc", "cash_div_tax", "ex_date", "imp_ann_date"]
    d = pl.concat([pl.read_parquet(f, columns=cols) for f in sorted(glob.glob(str(L.D / "dividend" / "*.parquet")))], how="diagonal_relaxed")
    d = d.with_columns(c("cash_div_tax").cast(pl.Float64, strict=False).fill_null(0),
                       *[c(k).cast(pl.Utf8).str.strip_chars() for k in ("ann_date", "ex_date", "imp_ann_date", "end_date")])
    paid = d.filter((c("div_proc") == "实施") & (c("cash_div_tax") > 0)).with_columns(
        pl.when(c("ex_date").str.len_chars() == 8).then(c("ex_date")).otherwise(c("imp_ann_date")).alias("pd"))
    paid = paid.filter(c("pd").str.len_chars() == 8).select("ts_code", c("pd").str.to_date("%Y%m%d")).unique().sort("pd")
    plan = d.filter(c("div_proc").is_in(["预案", "预披露"]) & (c("cash_div_tax") > 0) & (c("ann_date").str.len_chars() == 8))
    plan = plan.sort("ann_date").unique(["ts_code", "end_date"], keep="first").with_columns(c("ann_date").str.to_date("%Y%m%d").alias("ad"))
    # 公告日前（严格早于）最近一次实施派现日
    plan = plan.sort("ad").join_asof(paid.with_columns((c("pd") + pl.duration(days=1)).alias("ad")), on="ad", by="ts_code",
                                     strategy="backward").with_columns(c("ann_date").str.to_date("%Y%m%d").alias("ad"))
    gap = (c("ad") - c("pd")).dt.total_days()
    plan = plan.filter(c("pd").is_null() | (gap > 365 * GAP_Y))
    ld = list_dates()
    plan = plan.with_columns(c("ts_code").str.slice(0, 6).alias("code")).join(ld, on="code", how="inner")
    plan = plan.filter((c("ad") - c("ld").str.to_date("%Y%m%d")).dt.total_days() >= 365 * GAP_Y).drop("code")
    return _pit(plan, td).group_by("code", "sd").agg(c("cash_div_tax").sum().alias("ini_dps"))


def prep():
    df = R25.prep()
    td = df.select("trade_date").unique().sort("trade_date")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    for ev in (early_events(td), frev_events(td), dinit_events(td)):
        df = df.join(ev.rename({"sd": "trade_date"}), on=["code", "trade_date"], how="left")
    return df.with_columns(
        (c("liq") & c("early").is_not_null()).fill_null(False).alias("EARLY"), (c("early").fill_null(0) - tie).alias("sc_early"),
        (c("liq") & c("rev").is_not_null()).fill_null(False).alias("FREV"), (c("rev").fill_null(0) - tie).alias("sc_rev"),
        (c("liq") & c("ini_dps").is_not_null()).fill_null(False).alias("DINIT"),
        ((c("ini_dps") / c("close")).fill_null(0) - tie).alias("sc_ini")).sort("code", "trade_date")


NEW = {f"{k}_h{h}": (k, s, h) for k, s in (("EARLY", "sc_early"), ("FREV", "sc_rev"), ("DINIT", "sc_ini")) for h in (20, 60)}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(*[c(x).sum() for x in ("EARLY", "FREV", "DINIT")]).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, sc, h) in NEW.items():
            t, cur = run_real(df, sig, sc, fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            res.setdefault(n, {})[ph] = full_metrics(df, t, cur)
            print(n, ph, res[n][ph], flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

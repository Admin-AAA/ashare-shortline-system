"""[中线] §277「低价股 × 小市值」组合（LPS_M / LPS_W，邢不行文章，持20日） — 预注册（docs/strategy-research.md §277，结果出来前写定，不调参）。
口径同 lt_research11/23（SCW）与 lt_research103：realistic 引擎、K=20、max_new=10（仅回测）、cohort_bench 同日同市值五分位对照。
  U = amt20>3000万 & age>250 & 非ST & 非北交所 & mvd 非空 & close>2（不复权）；rp = U 内 close 升序名次，rm = U 内 mvd 升序名次。
  LPS_M：月末最后一个交易日（下一交易日跨月），rp<=200 & rm<=200，按 mvd 升序前 10。
  LPS_W：周五 & 小盘等权指数>MA20（同 SCW），rp<=200 & rm<=200，按 mvd 升序前 10。
  SCW：现役 mid-small-cap-weekly 参照（非候选）。
用法：LT_START=<phase start> LT_END=<phase end+buffer> PYTHONPATH=../..:. python lt_research104.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import build, fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col


def st_db():
    """MySQL stock_metadata_history.is_st（stocks+namechange，只读 SELECT）→ (code, trade_date=effective_from, st_db)。"""
    from sqlalchemy import text
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        rows = db.execute(text("select code, effective_from, is_st from stock_metadata_history")).fetchall()
    return (pl.DataFrame({"code": [r[0] for r in rows], "trade_date": [str(r[1])[:10] for r in rows],
                          "st_db": [bool(r[2]) for r in rows]}).sort("trade_date"))


def prep():
    df = build()
    if os.environ.get("LPS_ST_SRC") == "db":
        sd = st_db()
        df = (df.sort("trade_date").join_asof(sd, on="trade_date", by="code", strategy="backward")
                .with_columns(c("st_db").fill_null(False).alias("st")).drop("st_db").sort("code", "trade_date"))
        print("ST source = db; st rows share", round(float(df["st"].mean()), 4), flush=True)
    px = df.select("code", c("trade_date").alias("bd"), c("ac").alias("ac_bd"))
    df = df.join(px, on=["code", "bd"], how="left").with_columns(
        (c("total_mv").cast(pl.Float64, strict=False) * c("ac") / c("ac_bd")).alias("mvd"))
    g = (c("mvd").is_not_null() & (c("pe_ttm") > 0) & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]))
    r = df.filter(g).with_columns(c("mvd").rank("ordinal").over("trade_date").alias("r4"))
    idx = (r.filter(c("r4") <= 400).group_by("trade_date").agg(c("pct_chg").clip(-20, 20).mean().alias("s")).sort("trade_date")
           .with_columns((1 + c("s") / 100).cum_prod().alias("scid")).with_columns(c("scid").rolling_mean(20).alias("scid20"))
           .select("trade_date", "scid", "scid20"))
    del r
    df = df.join(idx, on="trade_date", how="left")
    td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(-1).alias("nx"))
    td = td.with_columns((c("nx").is_not_null() & (c("nx").str.slice(0, 7) != c("trade_date").str.slice(0, 7))).alias("me"))
    df = df.join(td.select("trade_date", "me"), on="trade_date", how="left")
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    base = ((c("amt20") > 3e7) & (c("age") > 250) & ~c("st") & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"])
            & c("mvd").is_not_null()).fill_null(False)
    U = (base & (c("close") > 2)).fill_null(False)
    Us = (base & (c("pe_ttm") > 0)).fill_null(False)
    rp = pl.when(U).then(c("close")).rank("ordinal").over("trade_date")
    rm = pl.when(U).then(c("mvd")).rank("ordinal").over("trade_date")
    df = df.with_columns(rp.alias("rp"), rm.alias("rm"))
    lp = U & (c("rp") <= 200) & (c("rm") <= 200)
    rk_lp = pl.when(lp.fill_null(False)).then(c("mvd")).rank("ordinal").over("trade_date")
    rk_s = pl.when(Us).then(c("mvd")).rank("ordinal").over("trade_date")
    on = c("scid") > c("scid20")
    df = df.with_columns(rk_lp.alias("rk_lp"), rk_s.alias("rk_s"))
    return df.with_columns(
        (c("me") & (c("rk_lp") <= 10)).fill_null(False).alias("LPS_M"),
        (wk & on & (c("rk_lp") <= 10)).fill_null(False).alias("LPS_W"),
        (wk & on & (c("rk_s") <= 10)).fill_null(False).alias("SCW"),
        (wk & on).fill_null(False).alias("wkon"),
        (-c("mvd")).fill_null(0).alias("sc_small"),
    ).drop("bd_right", "ac_bd", strict=False).sort("code", "trade_date")


NEW = {"LPS_M": ("LPS_M", 20), "LPS_W": ("LPS_W", 20), "SCW": ("SCW", 20)}

if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        # 重叠：LPS_W 与 SCW 同周；LPS_M 与「同月最后一个 SCW 周的选股」比较用同日 rk_s<=10（不受闸门影响）
        ow = (w.filter(c("LPS_W") | c("SCW")).group_by("trade_date")
              .agg(c("LPS_W").sum().alias("a"), (c("LPS_W") & c("SCW")).sum().alias("b")).filter(c("a") > 0))
        om = (w.filter(c("LPS_M")).group_by("trade_date")
              .agg(pl.len().alias("a"), (c("rk_s") <= 10).fill_null(False).sum().alias("b")))
        info = {"LPS_W_sigdays": ow.height, "LPS_W_overlap": round(float((ow["b"] / ow["a"]).mean()), 3) if ow.height else None,
                "LPS_W_avg_n": round(float(ow["a"].mean()), 2) if ow.height else None,
                "LPS_M_sigdays": om.height, "LPS_M_overlap": round(float((om["b"] / om["a"]).mean()), 3) if om.height else None,
                "LPS_M_avg_n": round(float(om["a"].mean()), 2) if om.height else None}
        lp = w.filter(c("LPS_M") | c("LPS_W"))
        if lp.height:
            info["sel_close_med"] = round(float(lp["close"].median()), 2)
            info["sel_mvd_med_yi"] = round(float(lp["mvd"].median()) / 1e4, 1)
            info["sel_pe_neg_share"] = round(float((lp["pe_ttm"].is_null() | (lp["pe_ttm"] <= 0)).mean()), 3)
        res.setdefault("_info", {})[ph] = info
        print("INFO", ph, info, flush=True)
        del w
        for n, (sig, h) in NEW.items():
            t, cur = run_real(df, sig, "sc_small", fixed(h), s, e, k_slots=20, max_new=10, max_hold=h)
            res.setdefault(n, {})[ph] = full_metrics(df, t, cur)
            if ph.startswith("R12") and t is not None:
                os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{n}_R12.csv"))
            print(n, ph, res[n][ph], flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

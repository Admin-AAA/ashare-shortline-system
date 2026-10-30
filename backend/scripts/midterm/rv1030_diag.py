"""[中线·复盘诊断 2026-10-30] 业绩加速（EA=G）/ 业绩预告（FA）全量信号逐条特征 + 前瞻 20 日收益（不受 K20 名额限制）。
探索性，不是策略检验。输出 output/research/rv1030/sig_{EA,FA}.parquet，分析见 rv1030_diag_report.py。
收益口径：sd 收盘出信号，e=sd+1 开盘买（开盘涨停剔除），e+20 开盘卖，0.3% 成本（不做跌停顺延，诊断用）；
未满 20 日的近期信号按最后收盘盯市（partial=True）。超额 = 同日 liq 全体等权同口径前瞻收益。
用法：PYTHONPATH=../..:. python rv1030_diag.py"""
import glob, json, os
import polars as pl
import lt_data as L
from lt_research20 import prep as prep20, forecast_first
from lt_research12 import _pit
c = pl.col
OUT = os.path.join(os.path.dirname(__file__), "../../output/research/rv1030")
COST = 0.003


def fina_extra(td):
    cols = ["ts_code", "ann_date", "end_date", "extra_item", "profit_dedt", "ocfps", "eps", "netprofit_yoy", "q_sales_yoy", "dt_netprofit_yoy"]
    parts = []
    for f in sorted(glob.glob(str(L.D / "fina" / "*.parquet"))):
        try:
            x = pl.read_parquet(f)
        except Exception:
            continue
        if not set(cols) <= set(x.columns): continue
        parts.append(x.select([c(k).cast(pl.Utf8) if k in ("ts_code", "ann_date", "end_date") else c(k).cast(pl.Float64, strict=False) for k in cols]))
    d = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
    d = d.sort("ann_date").unique(["ts_code", "end_date"], keep="first").sort("ts_code", "end_date")
    d = _pit(d, td)
    return (d.sort("end_date").group_by("code", "sd").agg(
        c("end_date").last().alias("ea_end"), c("extra_item").last(), c("ocfps").last(), c("eps").last(),
        c("netprofit_yoy").last().alias("npy"), c("q_sales_yoy").last().alias("qsy")).rename({"sd": "trade_date"}))


def fc_extra(td):
    d = forecast_first()
    extra = [k for k in ("p_change_max", "last_parent_net", "net_profit_max") if k in d.columns]
    if len(extra) < 3:
        cols = ["ts_code", "ann_date", "end_date", "p_change_max", "last_parent_net", "net_profit_max"]
        parts = []
        for f in sorted(glob.glob(str(L.D / "forecast" / "*.parquet"))):
            try:
                x = pl.read_parquet(f)
            except Exception:
                continue
            if not set(cols) <= set(x.columns): continue
            parts.append(x.select([c(k).cast(pl.Utf8) if k in ("ts_code", "ann_date", "end_date") else c(k).cast(pl.Float64, strict=False) for k in cols]))
        e = pl.concat(parts, how="vertical_relaxed").drop_nulls(["ann_date", "end_date"])
        e = e.with_columns(c("ann_date").str.strip_chars()).sort("ann_date").unique(["ts_code", "end_date"], keep="first")
        d = d.join(e.select("ts_code", "end_date", "p_change_max", "last_parent_net", "net_profit_max"), on=["ts_code", "end_date"], how="left")
    ok = c("type").is_in(["预增", "扭亏", "略增"]) & (c("p_change_min") > 0) & (c("net_profit_min") > 0)
    d = d.filter(ok.fill_null(False)).with_columns(c("p_change_min").clip(-1e9, 300).alias("fsc"))
    d = _pit(d, td)
    return (d.sort("fsc").group_by("code", "sd").agg(
        c("end_date").last().alias("fa_end"), c("type").last().alias("fa_type"), c("p_change_min").last().alias("pcmin"),
        c("p_change_max").last().alias("pcmax"), c("net_profit_min").last().alias("npmin"),
        c("last_parent_net").last().alias("lpn")).rename({"sd": "trade_date"}))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    df = prep20()
    td = df.select("trade_date").unique().sort("trade_date")
    ind = json.load(open(os.path.join(os.path.dirname(__file__), "../../data/industry_cache.json")))
    df = df.join(pl.DataFrame({"code": list(ind), "ind": list(ind.values())}), on="code", how="left")
    g = lambda e, n=1: e.shift(-n).over("code")
    df = df.with_columns(
        g(c("ao")).alias("ao_e"), g(c("ao"), 21).alias("ao_x"), g(c("lim_up_open")).alias("lu_e"),
        (g(c("open")) / c("close") - 1).alias("gap_e"),
        (c("ac").shift(1).over("code") / c("ac").shift(21).over("code") - 1).alias("pre_r20"),
        (c("ac").shift(1).over("code") / c("ac").shift(6).over("code") - 1).alias("pre_r5"),
        (c("pct_chg") / 100).alias("d0"),
        (c("ac") / c("ac").rolling_max(250).over("code")).alias("pos250"),
        (c("ac") / c("ac").rolling_max(60).over("code")).alias("pos60"),
        (c("ac") / c("ac").rolling_min(250).over("code") - 1).alias("up250"),
        c("ac").last().over("code").alias("ac_last"),
        ((c("close") <= c("down_limit") + 0.005) & c("down_limit").is_not_null()).alias("ld_close"),
        ((c("close") >= c("up_limit") - 0.005) & c("up_limit").is_not_null()).alias("lu_close"),
    )
    df = df.with_columns(
        pl.when(c("ao_x").is_not_null()).then(c("ao_x") / c("ao_e") - 1 - COST).otherwise(c("ac_last") / c("ao_e") - 1 - COST).alias("fwd"),
        c("ao_x").is_null().alias("partial"))
    # 市场日度量
    liq = c("liq") & ~c("lu_e").fill_null(False) & c("ao_e").is_not_null()
    mk = (df.group_by("trade_date").agg(
        pl.when(c("liq")).then(c("pct_chg")).mean().alias("mkt_pct"),
        pl.when(liq).then(c("fwd")).mean().alias("mkt_fwd"),
        pl.when(c("liq")).then(c("r20")).mean().alias("mkt_r20"),
        c("ld_close").sum().alias("ld_cnt"), c("lu_close").sum().alias("lu_cnt"),
        (c("liq") & c("G")).sum().alias("nsig_EA"), (c("liq") & c("FA")).sum().alias("nsig_FA"))
        .sort("trade_date"))
    mk = mk.with_columns((1 + c("mkt_pct").fill_null(0) / 100).cum_prod().alias("mkt_idx"))
    mk = mk.with_columns((c("mkt_idx") > c("mkt_idx").rolling_mean(20)).alias("mkt_ma20"),
                         (c("mkt_idx") > c("mkt_idx").rolling_mean(60)).alias("mkt_ma60"),
                         c("ld_cnt").rolling_mean(5).alias("ld5"))
    indr = df.filter(c("liq")).group_by("trade_date", "ind").agg(c("r20").mean().alias("ind_r20"), c("r60").mean().alias("ind_r60"))
    pe_rk = (pl.when(c("liq") & (c("pe_ttm") > 0)).then(c("pe_ttm")).rank().over("trade_date")
             / pl.when(c("liq") & (c("pe_ttm") > 0)).then(1).sum().over("trade_date"))
    mv_rk = (pl.when(c("liq")).then(c("total_mv")).rank().over("trade_date") / pl.when(c("liq")).then(1).sum().over("trade_date"))
    df = df.with_columns(pe_rk.alias("pe_rank"), mv_rk.alias("mv_rank"))
    keep = ["code", "trade_date", "ind", "fwd", "partial", "lu_e", "gap_e", "pre_r20", "pre_r5", "d0", "r20", "r60", "pos250", "pos60", "up250",
            "pe_ttm", "pb", "total_mv", "pe_rank", "mv_rank", "vol120", "amt20", "mkt_fwd", "mkt_r20", "mkt_ma20", "mkt_ma60", "ld_cnt", "ld5", "lu_cnt",
            "ind_r20", "ind_r60"]
    sigs = df.filter(c("G") | c("FA"))
    sigs = sigs.join(mk.select("trade_date", "mkt_fwd", "mkt_r20", "mkt_ma20", "mkt_ma60", "ld_cnt", "ld5", "lu_cnt", "nsig_EA", "nsig_FA"), on="trade_date", how="left")
    sigs = sigs.join(indr, on=["trade_date", "ind"], how="left")
    sigs = sigs.with_columns((c("ind_r20") - c("mkt_r20")).alias("ind_rel20"), (c("fwd") - c("mkt_fwd")).alias("ex"))
    ea = sigs.filter(c("G")).join(fina_extra(td), on=["code", "trade_date"], how="left")
    ea = ea.with_columns(
        (c("pdd") / (1 + c("dtp") / 100)).alias("base"),
        ((c("pdd") / (1 + c("dtp") / 100)) / (c("total_mv") * 1e4)).alias("base_mv"),
        (c("pdd") / (c("total_mv") * 1e4)).alias("pdd_mv"),
        (c("dtp") - c("prev_dtp")).alias("accel"), (c("dtp") >= 300).alias("cap300"),
        (c("pdd") / (c("pdd") + c("extra_item").fill_null(0))).alias("dedt_share"),
        (c("ocfps") / c("eps")).alias("ocf_eps"), c("ea_end").str.slice(4, 4).alias("rpt"),
        c("sc_g").alias("score"))
    ea.select(keep + ["nsig_EA", "ind_rel20", "ex", "dtp", "orv", "prev_dtp", "pdd", "npy", "qsy", "base", "base_mv", "pdd_mv", "accel", "cap300",
                      "dedt_share", "ocf_eps", "rpt", "score"]).write_parquet(os.path.join(OUT, "sig_EA.parquet"))
    fa = sigs.filter(c("FA")).join(fc_extra(td), on=["code", "trade_date"], how="left")
    fa = fa.with_columns((c("lpn") * 1e4 / (c("total_mv") * 1e4)).alias("lpn_mv"), (c("npmin") * 1e4 / (c("total_mv") * 1e4)).alias("np_mv"),
                         (c("pcmax") - c("pcmin")).alias("pc_width"), c("fa_end").str.slice(4, 4).alias("rpt"), c("sc_a").alias("score"),
                         (c("pcmin") >= 300).alias("cap300"))
    fa.select(keep + ["nsig_FA", "ind_rel20", "ex", "fa_type", "pcmin", "pcmax", "npmin", "lpn", "lpn_mv", "np_mv", "pc_width", "rpt", "score", "cap300"]
              ).write_parquet(os.path.join(OUT, "sig_FA.parquet"))
    mk.write_parquet(os.path.join(OUT, "mkt.parquet"))
    print("EA", ea.height, "FA", fa.height, "dates", mk["trade_date"].min(), mk["trade_date"].max(), flush=True)

"""[中线] §295「大小盘轮动」SLR_W_h20（邢不行《一直涨？无脑执行大小盘轮动策略，轻松跑赢指数5倍》：比较大盘/小盘近 20 日涨幅，
买强的一边，两边都跌就空仓）— 预注册（docs/strategy-research.md §295，结果出来前写定，不调参）。
  信号日：周五（weekday==5）。U = liq & total_mv 非空；q = U 内 total_mv 百分位。
  小盘组 S = U & 0.30 < q ≤ 0.60；大盘组 B = U & q > 0.90。动量 = 组内 r20（20 日复权涨幅）中位数。
  若 max(mS, mB) ≤ 0 → 本周不开新仓；否则买动量更高的一组里 amt20 最大的 10 只；次日开盘买、持 20 日。
  K=20、max_new=10（仅回测，实盘不设仓位上限）；cohort_bench 同市值五分位对照（参考项）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research111.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME = "SLR_W_h20"


def prep():
    df = build().sort("code", "trade_date")
    mv = c("total_mv").cast(pl.Float64, strict=False)
    u0 = (c("liq") & mv.is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(u0).then(mv).rank("average").over("trade_date").alias("_rmv"),
                         pl.when(u0).then(1).sum().over("trade_date").alias("_nu0"))
    q = c("_rmv") / c("_nu0")
    gS = (u0 & (q > 0.30) & (q <= 0.60)).fill_null(False); gB = (u0 & (q > 0.90)).fill_null(False)
    df = df.with_columns(gS.alias("gS"), gB.alias("gB"))
    df = df.with_columns(pl.when(c("gS")).then(c("r20")).median().over("trade_date").alias("mS"),
                         pl.when(c("gB")).then(c("r20")).median().over("trade_date").alias("mB"))
    side = pl.when(pl.max_horizontal("mS", "mB") <= 0).then(pl.lit("cash")).when(c("mS") >= c("mB")).then(pl.lit("S")).otherwise(pl.lit("B"))
    df = df.with_columns(side.alias("side"))
    cand = (((c("side") == "S") & c("gS")) | ((c("side") == "B") & c("gB"))).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("amt20").fill_null(0)).rank("ordinal").over("trade_date").alias("_rk"))
    wk = (c("trade_date").str.to_date().dt.weekday() == 5)
    return df.with_columns(
        (wk & c("cand") & (c("_rk") <= 10)).fill_null(False).alias("SLR_W"),
        (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_slr"),
    ).drop("_rmv", "_nu0", "_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        fr = df.filter((c("trade_date").str.to_date().dt.weekday() == 5) & c("trade_date").is_between(pl.lit(s), pl.lit(e))).group_by("trade_date").agg(c("side").first())
        w = df.filter(c("SLR_W") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"fridays": fr.height, "side_S": int((fr["side"] == "S").sum()), "side_B": int((fr["side"] == "B").sum()), "side_cash": int((fr["side"] == "cash").sum())}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64).median()) / 1e4, 1), "r20_med": round(float(w["r20"].median()), 3)})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "SLR_W", "sc_slr", fixed(20), s, e, k_slots=20, max_new=10, max_hold=20)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

"""[中线] §341「财报公告期市场反应强 → 次日开盘买、持 20 日」EAR_h20（Brandt–Kishore–Santa-Clara–Venkatachalam 2008
earnings announcement return 漂移：用市场对财报的真实反应代替会计意外，反应强的股票此后继续跑赢）。
— 预注册（docs/strategy-research.md §341，结果出来前写定，不调参）。
  与既往区别：§93 PEAD / §319 SUE 用会计意外（净利同比/标准化），§322 CSL 用业绩加速+新高，§103 用业绩预告；本题只看价格反应。
  数据：data/midterm/fina/*.parquet（tushare fina_indicator，ann_date PIT），每个 (code, end_date) 只取最早 ann_date（首次披露，排除更正）。
        沪深300：data/midterm/index_daily.parquet 000300.SH。
  事件：R0 = 第一个 ≥ ann_date 的交易日，R1 = R0 的下一个交易日（盘后/盘前披露都能覆盖）；
        EAR2 = (R0 复权涨跌 − 沪深300 涨跌) + (R1 复权涨跌 − 沪深300 涨跌)（单位 %）；EAR2 ≥ +5 触发，信号日 = R1。
  条件：R1 当日 liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）；不加涨停/估值/涨幅过滤。排序分 = EAR2 降序。
  引擎 realistic：R1+1 开盘买（开盘涨停买不进、不递补）、持 20 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research130.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME, HOLD, K, MAX_NEW, THR = "EAR_h20", 20, 20, 5, 5.0


def ann_events():
    fs = sorted(glob.glob(str(L.D / "fina" / "*.parquet")))
    f = pl.concat([pl.read_parquet(x, columns=["ts_code", "ann_date", "end_date"]) for x in fs], how="diagonal_relaxed")
    f = f.drop_nulls(["ann_date", "end_date"]).filter(c("ann_date").str.len_chars() == 8)
    f = f.with_columns(c("ts_code").str.slice(0, 6).alias("code"), c("ann_date").str.to_date("%Y%m%d").alias("a"))
    return f.group_by("code", "end_date").agg(c("a").min()).select("code", "a")


def prep():
    df = build().sort("code", "trade_date")
    ix = pl.read_parquet(L.D / "index_daily.parquet").filter(c("ts_code") == "000300.SH").sort("trade_date")
    ix = ix.with_columns((100 * (c("close") / c("close").shift(1) - 1)).alias("ix_pct")).select("trade_date", "ix_pct")
    df = df.join(ix, on="trade_date", how="left").sort("code", "trade_date")
    df = df.with_columns((c("pct_chg") - c("ix_pct")).alias("ex1"))
    df = df.with_columns((c("ex1").shift(1).over("code") + c("ex1")).alias("ear2"),
                         c("trade_date").shift(1).over("code").alias("prev_td"))
    ev = ann_events()
    td = df.select("trade_date").unique().with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    ev = ev.sort("a").join_asof(td, left_on="a", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    ev = ev.select("code", c("trade_date").alias("prev_td")).unique().with_columns(pl.lit(True).alias("evr0"))
    df = df.join(ev, on=["code", "prev_td"], how="left").with_columns(c("evr0").fill_null(False))
    sig = (c("evr0") & c("liq") & (c("ear2") >= THR)).fill_null(False)
    return df.with_columns(c("evr0").alias("ev"), sig.alias("EAR"),
                           pl.when(sig).then(c("ear2")).otherwise(-1e9).alias("sc_ear")).sort("code", "trade_date")


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
        w = df.filter(c("EAR") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("liq") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_liq": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "ear2_med": round(float(w["ear2"].median()), 2),
                         "r20_med": round(float(w["r20"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "EAR", "sc_ear", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

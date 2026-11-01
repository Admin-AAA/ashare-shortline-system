"""[中线] §337「实际回购执行强度」RBX_h20（公司真金白银已经买入的回购，比回购预案更可信；Peyer–Vermaelen / 实际回购量文献）
— 预注册（docs/strategy-research.md §337，结果出来前写定，不调参）。
  数据：data/midterm/events/repurchase_*.parquet（tushare repurchase，公告日 PIT），只用 proc=='完成' 且 amount>0 的记录（已实际成交的回购金额，元）。
  因公司常按月披露「累计已回购金额」，同一窗口内用 max 而不是 sum（避免把累计数重复相加；分笔披露时会低估，偏保守）。
  事件：对每只股票、每个公告日 a，X = (a−60 自然日, a] 内「完成」记录 amount 的最大值；信号日 sd = 第一个 ≥ a 的交易日；
        ratio = X / (sd 日总市值×1e4) ≥ 1% 即触发；同股 180 自然日内只认第一次触发。
  条件：sd 当日 liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）& sd 收盘未涨停（收盘 < 涨停价−0.005）。排序分 = ratio 降序。
  引擎 realistic：次日开盘买（开盘涨停买不进、不递补）、持 20 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：LT_START=.. LT_END=.. PYTHONPATH=../..:. python lt_research128.py "<phase>" out.json"""
import sys, json, os, glob
from datetime import timedelta
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
NAME, HOLD, WIN_D, DEDUP_D, R_MIN = "RBX_h20", 20, 60, 180, 0.01


def rbx_raw():
    h = pl.concat([pl.read_parquet(f, columns=["ts_code", "ann_date", "proc", "amount"])
                   for f in sorted(glob.glob(str(L.D / "events" / "repurchase_*.parquet")))], how="diagonal_relaxed")
    h = h.filter((c("proc") == "完成") & (c("amount").cast(pl.Float64, strict=False) > 0) & (c("ann_date").str.len_chars() == 8))
    h = h.with_columns(c("ts_code").str.slice(0, 6).alias("code"), c("ann_date").str.to_date("%Y%m%d").alias("a"),
                       c("amount").cast(pl.Float64, strict=False))
    g = h.group_by("code", "a").agg(c("amount").max()).sort("code", "a")
    rows = []
    for (code,), x in g.partition_by("code", as_dict=True).items():
        recs = list(zip(x["a"].to_list(), x["amount"].to_list()))
        for a, _ in recs:
            rows.append((code, a, max(v for d, v in recs if a - timedelta(days=WIN_D) < d <= a)))
    return pl.DataFrame(rows, schema=["code", "a", "X"], orient="row")


def prep():
    df = build().sort("code", "trade_date")
    ev = rbx_raw()
    td = df.select("trade_date").unique().with_columns(c("trade_date").str.to_date().alias("sdd")).sort("sdd")
    ev = ev.sort("a").join_asof(td, left_on="a", right_on="sdd", strategy="forward").drop_nulls("trade_date")
    ev = ev.group_by("code", "trade_date").agg(c("X").max(), c("a").max())
    mv = df.select("code", "trade_date", c("total_mv").cast(pl.Float64, strict=False))
    ev = ev.join(mv, on=["code", "trade_date"], how="inner").with_columns((c("X") / (c("total_mv") * 1e4)).alias("ratio"))
    ev = ev.filter(c("ratio") >= R_MIN).sort("code", "a")
    keep = []
    for (code,), x in ev.partition_by("code", as_dict=True).items():
        last = None
        for td_, a, r in zip(x["trade_date"].to_list(), x["a"].to_list(), x["ratio"].to_list()):
            if last is None or (a - last).days > DEDUP_D:
                keep.append((code, td_, float(r))); last = a
    ev = pl.DataFrame(keep, schema=["code", "trade_date", "ratio"], orient="row")
    df = df.join(ev, on=["code", "trade_date"], how="left")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    sig = (c("ratio").is_not_null() & c("liq") & (c("close") < ul - 0.005)).fill_null(False)
    return df.with_columns(c("ratio").is_not_null().alias("ev"), sig.alias("RBX"),
                           c("ratio").fill_null(0).alias("sc_rbx")).sort("code", "trade_date")


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
        w = df.filter(c("RBX") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("ev") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_ev_all": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "ratio_med": round(float(w["ratio"].median()), 4),
                         "sigday_pct_med": round(float(w["pct_chg"].median()), 2),
                         "r20_med": round(float(w["r20"].median()), 3), "r60_med": round(float(w["r60"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "RBX", "sc_rbx", fixed(HOLD), s, e, k_slots=20, max_new=5, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

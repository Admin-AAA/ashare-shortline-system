"""[短线研究 §323]「高股息除息日未填息 → 次日开盘买、持 5 日」EXD_h5（除息日抛压/股息捕获卖盘过度 → 填息反弹）。
预注册 docs/strategy-research.md §323，结果出来前写定，不调参。
  事件：dividend 缓存 div_proc=="实施" 且 cash_div_tax>0，同 (code, ex_date) 每股派现(税前)求和 = dps；信号日 T = ex_date 当天（该股当天有交易）。
  条件：liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）& 本次股息率 dps/前一交易日不复权收盘 ≥ 2% & 除息日涨跌幅(复权口径 pct_chg) ≤ 0（未填息）
        & 除息日收盘未跌停。每个信号日按本次股息率降序取前 5。
  引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/exd_s323.py "<phase>" out.json"""
import sys, json, os, glob
import polars as pl
import lt_data as L
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "EXD_h5"


def ex_events():
    cols = ["ts_code", "div_proc", "cash_div_tax", "ex_date"]
    d = pl.concat([pl.read_parquet(f, columns=cols) for f in sorted(glob.glob(str(L.D / "dividend" / "*.parquet")))], how="diagonal_relaxed")
    d = d.with_columns(c("cash_div_tax").cast(pl.Float64, strict=False).fill_null(0), c("ex_date").cast(pl.Utf8).str.strip_chars())
    d = d.filter((c("div_proc") == "实施") & (c("cash_div_tax") > 0) & (c("ex_date").str.len_chars() == 8))
    d = d.unique(["ts_code", "ex_date", "cash_div_tax"])
    return d.group_by(c("ts_code").str.slice(0, 6).alias("code"), L._dash("ex_date").alias("trade_date")).agg(c("cash_div_tax").sum().alias("dps"))


def prep():
    df = build().join(ex_events(), on=["code", "trade_date"], how="left").sort("code", "trade_date")
    df = df.with_columns(c("close").shift(1).over("code").alias("pc_raw"))
    dy = c("dps") / c("pc_raw")
    notld = c("close") > pl.coalesce(c("down_limit"), c("pc_raw") * 0.902) + 0.005
    cand = (c("liq") & c("dps").is_not_null() & (dy >= 0.02) & (c("pct_chg") <= 0) & notld).fill_null(False)
    df = df.with_columns(dy.alias("dy"), cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("dy")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("EXD"),
                           c("dy").fill_null(-1e9).alias("sc_exd")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("EXD") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("liq") & c("dps").is_not_null() & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_exdays_liq": a.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
                         "dy_med": round(float(w["dy"].median()), 4), "exday_pct_med": round(float(w["pct_chg"].median()), 2),
                         "r20_med": round(float(w["r20"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(5, 2)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "EXD", "sc_exd", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

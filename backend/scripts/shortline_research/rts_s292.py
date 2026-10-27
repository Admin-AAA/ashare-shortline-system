"""[短线研究 §292]「低位红三兵 → 次日买、持 5 日」RTS_h5（邢不行视频《最牛反指、短线之王、空头的神？谁是A股最强K线形态？》K线形态检验；
经典蜡烛图「红三兵 / Three White Soldiers」底部反转形态）。预注册 docs/strategy-research.md §292，结果出来前写定，不调参。
  连续 3 日（T-2、T-1、T）每日都满足：收阳（close > open）、复权收盘高于前一日（ac > ac[-1]）、0.3% ≤ pct_chg ≤ 6%、
  复权开盘高于前一日复权开盘且不高于前一日复权收盘（ao[-1] < ao ≤ ac[-1]）、上影线不长（high − close ≤ 0.5×(close − open)）。
  低位：形态前一日 T-3 的复权收盘距前 60 日（T-62..T-3）复权收盘最高点回撤 ≥15%。
  信号日 T 非涨停收盘（close < up_limit − 0.005，缺涨停价按前收 +9.8%/+19.8%）。
  每日按 amt20 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/rts_s292.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "RTS_h5"


def prep():
    df = build().sort("code", "trade_date")
    o, h, cl = c("open").cast(pl.Float64), c("high").cast(pl.Float64), c("close").cast(pl.Float64)
    day = ((cl > o) & (c("ac") > c("ac").shift(1).over("code")) & c("pct_chg").is_between(0.3, 6.0)
           & (c("ao") > c("ao").shift(1).over("code")) & (c("ao") <= c("ac").shift(1).over("code"))
           & ((h - cl) <= 0.5 * (cl - o)))
    df = df.with_columns(day.fill_null(False).alias("_d"))
    df = df.with_columns((c("_d") & c("_d").shift(1).over("code") & c("_d").shift(2).over("code")).fill_null(False).alias("_p3"),
                         (c("ac").shift(3).over("code") / c("ac").shift(3).rolling_max(60).over("code") - 1).alias("dd60"),
                         (c("ac") / c("ac").shift(3).over("code") - 1).alias("r3"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    not_lu = (c("close") < ul - 0.005)
    cand = (c("liq") & c("_p3") & (c("dd60") <= -0.15) & not_lu).fill_null(False)
    key = -c("amt20").fill_null(0)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("RTS"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_rts")).drop("_rk", "_d", "_p3").sort("code", "trade_date")

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
        w = df.filter(c("RTS") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "dd60_med": round(float(w["dd60"].median()), 3),
                    "r3_med": round(float(w["r3"].median()), 3), "sig_pct_med": round(float(w["pct_chg"].median()), 2)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "RTS", "sc_rts", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

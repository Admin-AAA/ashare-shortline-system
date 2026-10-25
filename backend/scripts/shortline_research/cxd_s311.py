"""[短线研究 §311]「小盘股回调后缩量十字星 → 次日买、持 5 日」CXD_h5（邢不行视频《谁是A股最强K线形态》里的十字星 + 小市值框架的小盘趋势闸门）。
预注册 docs/strategy-research.md §311，结果出来前写定，不调参。
  复用 lt_research104.prep()（mvd、scid/scid20、rk_s；ST 口径 LPS_ST_SRC=db）。pre = close/(1+pct_chg/100)；r5 = ac/ac.shift(5) − 1。
  候选：rk_s ≤ 400 & scid > scid20 & r5 < 0 & |close − open| ≤ 0.3%·pre & (high − low) ≥ 2%·pre & amount < 0.7·amt20(昨) & 非跌停收盘。
  每个信号日按 r5 升序取前 5。realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LPS_ST_SRC=db LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/cxd_s311.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed
from realistic import run_real
from lt_research46 import PH, full_metrics
from lt_research104 import prep as prep104
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "CXD_h5"


def prep():
    df = prep104().sort("code", "trade_date")
    pre = c("close") / (1 + c("pct_chg") / 100)
    df = df.with_columns((c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"), pre.alias("pre"),
                         c("amt20").shift(1).over("code").alias("amt20p"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    dl = pl.coalesce(c("down_limit"), c("pre") * (1 - band))
    doji = ((c("close") - c("open")).abs() <= 0.003 * c("pre")) & ((c("high") - c("low")) >= 0.02 * c("pre"))
    cand = ((c("rk_s") <= 400) & (c("scid") > c("scid20")) & (c("r5") < 0) & doji
            & (c("amount") < 0.7 * c("amt20p")) & (c("close") > dl + 0.005)).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(c("r5")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("CXD"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_cxd")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("CXD") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "r5_med": round(float(w["r5"].median()), 3),
                    "sig_pct_med": round(float(w["pct_chg"].median()), 2), "r20_med": round(float(w["r20"].median()), 3),
                    "mvd_med_yi": round(float(w["mvd"].median()) / 1e4, 1)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "CXD", "sc_cxd", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

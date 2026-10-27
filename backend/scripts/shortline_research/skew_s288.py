"""[短线研究 §288]「已实现偏度最低（左偏）→ 次日买、持 5 日」SKN_h5（邢不行研报解读：华泰期货 CTA 偏度因子"做多负偏度"；
Amaya-Christoffersen-Jacobs-Vasquez 2015 JFE：已实现偏度低的股票下一周收益更高）。
预注册 docs/strategy-research.md §288，结果出来前写定，不调参。
  skew20 = 近 20 个交易日（含 T）日收益 pct_chg/100 的样本偏度（rolling_skew，bias=True，窗口不足为空）。
  候选：liq（amt20>5000万、上市>250日、非ST、非北交所）& skew20 非空；每日 skew20 升序（越左偏越优先；同值按 amt20 降序）取前 5。
引擎 realistic：T 收盘信号、T+1 开盘买（开盘涨停买不进、不递补）、持 5 日（fixed(5)）、跌停锁死顺延、0.3% 成本；
K=20、max_new=5（仅回测，实盘不设仓位上限）。cohort_bench 同市值五分位对照只作参考。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/skew_s288.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "SKN_h5"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns((c("pct_chg") / 100).rolling_skew(20).over("code").alias("skew20"))
    cand = (c("liq") & c("skew20").is_not_null() & c("skew20").is_not_nan()).fill_null(False)
    key = c("skew20") - c("amt20").fill_null(0) * 1e-15
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("SKN"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_skn")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("SKN") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "skew_med": round(float(w["skew20"].median()), 3),
                    "sig_pct_med": round(float(w["pct_chg"].median()), 2), "r20_med": round(float(w["r20"].median()), 3)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "SKN", "sc_skn", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

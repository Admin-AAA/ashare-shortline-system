"""[短线研究 §294]「MACD 低位底背离 + 金叉 → 次日买、持 5 日」MDB_h5（邢不行《用Python量化定义MACD，找出A股全部12万次顶底背离，胜率究竟有多少？》）。
预注册 docs/strategy-research.md §294，结果出来前写定，不调参。
  用复权收盘 ac 算 MACD(12,26,9)：EMA 递推 adjust=False；DIF = EMA12 − EMA26，DEA = EMA9(DIF)。
  T 日：①金叉 DIF_T > DEA_T 且 DIF_{T-1} ≤ DEA_{T-1}；②零轴下方 DIF_T < 0；
  ③价格创新低：min(ac, T-9..T) < min(ac, T-39..T-10)；④DIF 不创新低：min(DIF, T-9..T) > min(DIF, T-39..T-10)；
  ⑤liq、age ≥ 300（MACD 预热）、T 非涨停收盘（close < up_limit − 0.005，缺涨停价按前收 +9.8%/+19.8%）。
  每日按 amt20 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/macd_s294.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "MDB_h5"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns((c("ac").ewm_mean(span=12, adjust=False).over("code") - c("ac").ewm_mean(span=26, adjust=False).over("code")).alias("dif"))
    df = df.with_columns(c("dif").ewm_mean(span=9, adjust=False).over("code").alias("dea"))
    df = df.with_columns(c("ac").rolling_min(10).over("code").alias("_pl_r"), c("ac").shift(10).rolling_min(30).over("code").alias("_pl_p"),
                         c("dif").rolling_min(10).over("code").alias("_dl_r"), c("dif").shift(10).rolling_min(30).over("code").alias("_dl_p"))
    cross = (c("dif") > c("dea")) & (c("dif").shift(1).over("code") <= c("dea").shift(1).over("code"))
    div = (c("_pl_r") < c("_pl_p")) & (c("_dl_r") > c("_dl_p"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    not_lu = (c("close") < ul - 0.005)
    cand = (c("liq") & (c("age") >= 300) & cross & (c("dif") < 0) & div & not_lu).fill_null(False)
    df = df.with_columns(cand.alias("cand"), (c("ac") / c("_pl_p") - 1).alias("low_vs_prior"))
    df = df.with_columns(pl.when(c("cand")).then(-c("amt20").fill_null(0)).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("MDB"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_mdb")).drop("_rk", "_pl_r", "_pl_p", "_dl_r", "_dl_p").sort("code", "trade_date")


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
        w = df.filter(c("MDB") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "r20_med": round(float(w["r20"].median()), 3),
                    "low_vs_prior_med": round(float(w["low_vs_prior"].median()), 3), "sig_pct_med": round(float(w["pct_chg"].median()), 2)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "MDB", "sc_mdb", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

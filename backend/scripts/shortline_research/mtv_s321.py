"""[短线研究 §321]「强势趋势模板 + 波动收缩后放量突破 → 次日开盘买、持 5 日」MTV_h5（Mark Minervini《股票魔法师》
Trend Template + VCP 波动收缩突破；欧奈尔/Minervini 的"只买第二阶段强势股的突破"）。预注册 docs/strategy-research.md §321，结果出来前写定，不调参。
  趋势模板（复权收盘 ac）：ac > MA50 > MA150 > MA200 且 MA200 > 20 日前的 MA200（200 日线向上）；ac ≥ 1.3×250日最低；ac ≥ 0.75×250日最高。
  波动收缩：信号日之前 10 日涨跌幅标准差 ≤ 0.7 × 之前 60 日涨跌幅标准差（均不含信号日）。
  突破：ac > 前 20 日最高 ac；当日成交额 ≥ 1.5 × 前一日的 20 日均额；当日涨幅 ≥ 3%。
  候选 = liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）& 以上全部；每个信号日按近 120 日涨幅（RS）降序取前 5。
  引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/mtv_s321.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "MTV_h5"


def prep():
    df = build().sort("code", "trade_date")
    o = "code"
    df = df.with_columns(c("ac").rolling_mean(50).over(o).alias("m50"), c("ac").rolling_mean(150).over(o).alias("m150"),
                         c("ac").rolling_mean(200).over(o).alias("m200"), c("ac").rolling_max(250).over(o).alias("hi250"),
                         c("ac").rolling_min(250).over(o).alias("lo250"), c("ac").shift(1).rolling_max(20).over(o).alias("hi20p"),
                         (c("pct_chg") / 100).shift(1).rolling_std(10).over(o).alias("sd10"),
                         (c("pct_chg") / 100).shift(1).rolling_std(60).over(o).alias("sd60"),
                         c("amt20").shift(1).over(o).alias("amt20p"), (c("ac") / c("ac").shift(120).over(o) - 1).alias("r120"))
    df = df.with_columns(c("m200").shift(20).over(o).alias("m200_20"))
    tt = ((c("ac") > c("m50")) & (c("m50") > c("m150")) & (c("m150") > c("m200")) & (c("m200") > c("m200_20"))
          & (c("ac") >= 1.3 * c("lo250")) & (c("ac") >= 0.75 * c("hi250")))
    vcp = c("sd10") <= 0.7 * c("sd60")
    bo = (c("ac") > c("hi20p")) & (c("amount") >= 1.5 * c("amt20p")) & (c("pct_chg") >= 3)
    cand = (c("liq") & tt & vcp & bo).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("r120")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("MTV"),
                           c("r120").fill_null(-1e9).alias("sc_mtv")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("MTV") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("cand") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "n_cand_all": a.height, "n_codes": w["code"].n_unique(),
                    "sig_pct_med": round(float(w["pct_chg"].median()), 2),
                    "lu_share": (lambda v: None if v is None else round(float(v), 3))((w["close"] >= w["up_limit"] - 0.005).mean()),
                    "r20_med": round(float(w["r20"].median()), 3), "r120_med": round(float(w["r120"].median()), 3),
                    "amt_ratio_med": round(float((w["amount"] / w["amt20p"]).median()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "MTV", "sc_mtv", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

"""[短线研究 §302]「强势股缩量回踩 20 日线 → 次日买、持 5 日」RPB_h5（欧奈尔 RPS 强度 + 回踩均线：近半年最强的票
回调到上升中的 20 日线附近、当天缩量、收盘守住 20 日线 = 洗盘结束、趋势延续）。预注册 docs/strategy-research.md §302，结果出来前写定，不调参。
  r120 = ac / ac.shift(120) − 1；rps = liq 内 r120 百分位排名（0~1）。m20 = ac 的 20 日均线；al = low·ac/close（复权最低价）。
  条件：liq；rps ≥ 0.90；m20_T > m20_{T−5}（20 日线上行）；al_T ≤ 1.01·m20_T 且 ac_T ≥ m20_T（盘中回踩到 20 日线 1% 以内、收盘守住）；
        amount_T < 0.8·amt20_T（缩量）；T 非涨停收盘（close < 涨停价 −0.005，缺涨停价按前收 +9.8%/+19.8%）。
  每个信号日按 rps 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/rpb_s302.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "RPB_h5"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns((c("ac") / c("ac").shift(120).over("code") - 1).alias("r120"),
                         c("ac").rolling_mean(20).over("code").alias("m20"),
                         (c("low") * c("ac") / c("close")).alias("al"))
    df = df.with_columns(c("m20").shift(5).over("code").alias("m20_5"))
    ok = (c("liq") & c("r120").is_not_null()).fill_null(False)
    df = df.with_columns(pl.when(ok).then(c("r120")).rank("average").over("trade_date").alias("_r"),
                         pl.when(ok).then(1).sum().over("trade_date").alias("_n"))
    df = df.with_columns((c("_r") / c("_n")).alias("rps"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    cand = (ok & (c("rps") >= 0.90) & (c("m20") > c("m20_5")) & (c("al") <= 1.01 * c("m20")) & (c("ac") >= c("m20"))
            & (c("amount") < 0.8 * c("amt20")) & (c("close") < ul - 0.005)).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("rps")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("RPB"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_rpb")).drop("_rk", "_r", "_n").sort("code", "trade_date")


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
        w = df.filter(c("RPB") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "sig_pct_med": round(float(w["pct_chg"].median()), 2),
                    "r120_med": round(float(w["r120"].median()), 3), "r20_med": round(float(w["r20"].median()), 3),
                    "amt_ratio_med": round(float((w["amount"] / w["amt20"]).median()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "RPB", "sc_rpb", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

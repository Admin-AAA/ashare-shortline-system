"""[短线研究 §304]「跳空高开不回补（突破缺口）→ 次日买、持 5 日」GUP_h5（经典缺口理论：当天最低价都高于前一日最高价、
缺口全天未回补，且放量收在开盘之上 = 突破缺口 / 强势启动）。预注册 docs/strategy-research.md §304，结果出来前写定，不调参。
  ah/al/ao = high/low/open × ac/close（复权高/低/开）。
  条件：liq；al_T > ah_{T−1}（全天未回补缺口）；ao_T / ac_{T−1} − 1 ≥ 0.02（开盘跳空 ≥2%）；ac_T ≥ ao_T（收盘不低于开盘）；
        amount_T ≥ 1.5·amt20_T；T 非涨停收盘（close < 涨停价 −0.005，缺涨停价按前收 +9.8%/+19.8%）。
  每个信号日按 amt20 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/gup_s304.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "GUP_h5"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns((c("high") * c("ac") / c("close")).alias("ah"), (c("low") * c("ac") / c("close")).alias("al"))
    df = df.with_columns(c("ah").shift(1).over("code").alias("ah1"), c("ac").shift(1).over("code").alias("ac1"))
    df = df.with_columns((c("ao") / c("ac1") - 1).alias("gap"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    cand = (c("liq") & (c("al") > c("ah1")) & (c("gap") >= 0.02) & (c("ac") >= c("ao"))
            & (c("amount") >= 1.5 * c("amt20")) & (c("close") < ul - 0.005)).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("amt20")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("GUP"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_gup")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("GUP") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "sig_pct_med": round(float(w["pct_chg"].median()), 2),
                    "gap_med": round(float(w["gap"].median()), 3), "r20_med": round(float(w["r20"].median()), 3),
                    "amt_ratio_med": round(float((w["amount"] / w["amt20"]).median()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "GUP", "sc_gup", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

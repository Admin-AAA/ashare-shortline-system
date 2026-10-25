"""[短线研究 §296]「大盘普跌日逆势收涨的强势股 → 次日买、持 5 日」ADS_h5（A 股常见的"抗跌股/逆势股"思路：普跌日有资金逆势
承接的票，后几天更强；邢不行第 103 期《抄底总在半山腰？》讲的是"在市场情绪低点入场"，这里落到个股：情绪低点里的最强者）。
预注册 docs/strategy-research.md §296，结果出来前写定，不调参。
  mkt_T = 当日全市场（面板内全部股票）pct_chg 截断 ±20 后的等权均值（%）。信号日：mkt_T ≤ −1.5。
  候选：liq & pct_chg_T ≥ +3 & T 非涨停收盘（close < up_limit − 0.005，缺涨停价按前收 +9.8%/+19.8%）。
  每个信号日按 amt20 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/ads_s296.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "ADS_h5"
MKT_TH, UP_TH = -1.5, 3.0


def prep():
    df = build().sort("code", "trade_date")
    mk = df.group_by("trade_date").agg(c("pct_chg").cast(pl.Float64).clip(-20, 20).mean().alias("mkt"))
    df = df.join(mk, on="trade_date", how="left").sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    not_lu = (c("close") < ul - 0.005)
    cand = (c("liq") & (c("mkt") <= MKT_TH) & (c("pct_chg") >= UP_TH) & not_lu).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("amt20").fill_null(0)).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("ADS"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_ads")).drop("_rk").sort("code", "trade_date")


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
        dd = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e))).group_by("trade_date").agg(c("mkt").first())
        w = df.filter(c("ADS") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"down_days": int((dd["mkt"] <= MKT_TH).sum()), "days": dd.height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "sig_pct_med": round(float(w["pct_chg"].median()), 2),
                         "mkt_med": round(float(w["mkt"].median()), 2), "r20_med": round(float(w["r20"].median()), 3)})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "ADS", "sc_ads", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

"""[短线研究 §318]「跌停翻红（盘中触及跌停、收盘收涨）→ 次日开盘买、持 5 日」DTR_h5（极端日内反转：恐慌盘砸到跌停后被资金
撬板拉红，邢不行《抄底总在半山腰》《大A为何频繁跳水》思路的个股版）。预注册 docs/strategy-research.md §318，结果出来前写定，不调参。
  候选：liq（amt20>5000万 & 上市>250日 & 非ST & 非北交所）；当日 low ≤ 跌停价 + 0.005（缺跌停价按前收×(1−9.8%)，创业板/科创板 19.8%）；
        当日 close > 前收（pct_chg>0，收红）。不排除收盘涨停（地天板）；不加其他过滤。
  每个信号日按 amt20 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/dtr_s318.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "DTR_h5"


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    df = df.with_columns(pc.alias("pc"), dl.alias("dl"), ul.alias("ul"))
    cand = (c("liq") & (c("low") <= c("dl") + 0.005) & (c("close") > c("pc"))).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("amt20")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("DTR"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_dtr")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("DTR") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        a = df.filter(c("cand") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "n_cand_all": a.height,
                    "sig_pct_med": round(float(w["pct_chg"].median()), 2),
                    "dlu_share": round(float((w["close"] >= w["ul"] - 0.005).mean()), 3),
                    "r20_med": round(float(w["r20"].median()), 3), "r60_med": round(float(w["r60"].median()), 3),
                    "amt_ratio_med": round(float((w["amount"] / w["amt20"]).median()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "DTR", "sc_dtr", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

"""[短线研究 §342]「首板次日低开高走翻红（弱转强）→ 再次日开盘买、持 5 日」WTS_h5。
预注册 docs/strategy-research.md §342，结果出来前写定，不调参。
  T−1 收盘封涨停、T−2 未封（首板）；T 开盘 ≤ T−1 收盘×0.99；T 收盘 ≥ T−1 收盘 且 收盘 > 开盘；T 未封涨停；T 日 liq。
  同日按 (收盘/开盘−1) 降序前 5。引擎 realistic：T+1 开盘买、持 5 日、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/wts_s342.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "WTS_h5"


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("luc"))
    pac = c("ac").shift(1).over("code")
    df = df.with_columns(c("luc").shift(1).over("code").fill_null(False).alias("lu1"),
                         c("luc").shift(2).over("code").fill_null(True).alias("lu2"),
                         (c("ao") / pac - 1).alias("gap"), (c("ac") / pac - 1).alias("chg"),
                         (c("close") / c("open") - 1).alias("ib"))
    cand = (c("lu1") & ~c("lu2") & (c("gap") <= -0.01) & (c("chg") >= 0) & (c("close") > c("open"))
            & ~c("luc") & c("liq")).fill_null(False)
    df = df.with_columns(cand.alias("cand"), (c("lu1") & ~c("lu2")).alias("ev"))
    df = df.with_columns(pl.when(c("cand")).then(-c("ib")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("WTS"),
                           pl.when(c("cand")).then(c("ib")).otherwise(-1e9).alias("sc_wts")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("WTS") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_cand": df.filter(c("cand") & c("trade_date").is_between(pl.lit(s), pl.lit(e))).height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
                         "gap_med": round(float(w["gap"].median()), 3), "ib_med": round(float(w["ib"].median()), 3),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "WTS", "sc_wts", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

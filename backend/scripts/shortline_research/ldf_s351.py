"""[短线研究 §351]「单日跌停后次日未封死、收盘翻红 → 再次日开盘买、持 5 日」LDF_h5。
预注册 docs/strategy-research.md §351，结果出来前写定，不调参。
  T−1 收盘封跌停；T 日开盘 > 跌停价、收盘涨幅>0 且未封涨停；liq；同日按 T 日涨幅降序前 5。
  与 §318（同日盘中触跌停收红）、§328（连续≥2 跌停后首次开板）不同。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/ldf_s351.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "LDF_h5"


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    dl = pl.coalesce(c("down_limit"), c("close").shift(1).over("code") * (1 - band))
    df = df.with_columns(
        (c("close") >= ul - 0.005).fill_null(False).alias("luc"),
        (c("close") <= dl + 0.005).fill_null(False).alias("ldc"),
        (c("open") <= dl + 0.005).fill_null(False).alias("ldo"),
        (c("pct_chg") / 100).alias("ret1"),
    )
    prev_ld = c("ldc").shift(1).over("code").fill_null(False)
    cand = (c("liq") & prev_ld & ~c("ldo") & (c("ret1") > 0) & ~c("luc")).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("ret1")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns(
        (c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("LDF"),
        pl.when(c("cand")).then(c("ret1")).otherwise(-1e9).alias("sc_ldf"),
    ).drop("_rk").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        w = df.filter(c("LDF") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_cand": df.filter(c("cand") & c("trade_date").is_between(pl.lit(s), pl.lit(e))).height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
                         "ret1_med": round(float(w["ret1"].median()), 4),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1)})
        res.setdefault("_info", {})[ph] = info
        print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "LDF", "sc_ldf", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

"""[短线研究 §346]「底部岛形反转（跳空下跌 → 1–5 日孤岛 → 跳空上涨）→ 次日开盘买、持 5 日」ISL_h5。
预注册 docs/strategy-research.md §346，结果出来前写定，不调参。
  复权高低：ah = high·ac/close，al = low·ac/close。对 k=1..5：孤岛 = T−k..T−1；
    跳空进岛：max(ah[T−k..T−1]) < al[T−k−1]；跳空出岛：al[T] > max(ah[T−k..T−1])；
    岛前已跌：ac[T−k−1]/ac[T−k−21] − 1 ≤ −10%。任一 k 满足即可，取最小 k。
  T 日 liq、收盘未封涨停。同日按 出岛缺口 al[T]/岛内最高 − 1 降序前 5。
  引擎 realistic：T+1 开盘买、持 5 日、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/isl_s346.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "ISL_h5"


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("luc"),
                         (c("high") * c("ac") / c("close")).alias("ah"), (c("low") * c("ac") / c("close")).alias("al"))
    gaps, ks = [], []
    for k in range(1, 6):
        imax = c("ah").shift(1).rolling_max(k).over("code")
        ok = ((imax < c("al").shift(k + 1).over("code")) & (c("al") > imax)
              & (c("ac").shift(k + 1).over("code") / c("ac").shift(k + 21).over("code") - 1 <= -0.10)).fill_null(False)
        gaps.append(pl.when(ok).then(c("al") / imax - 1).otherwise(None).alias(f"_g{k}"))
    df = df.with_columns(gaps)
    df = df.with_columns(pl.coalesce([c(f"_g{k}") for k in range(1, 6)]).alias("gap"),
                         pl.coalesce([pl.when(c(f"_g{k}").is_not_null()).then(pl.lit(k)) for k in range(1, 6)]).alias("isl_k")
                         ).drop([f"_g{k}" for k in range(1, 6)])
    cand = (c("liq") & c("gap").is_not_null() & ~c("luc")).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("gap")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("ISL"),
                           pl.when(c("cand")).then(c("gap")).otherwise(-1e9).alias("sc_isl")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("ISL") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_cand": df.filter(c("cand") & c("trade_date").is_between(pl.lit(s), pl.lit(e))).height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
                         "gap_med": round(float(w["gap"].median()), 4), "k_dist": w["isl_k"].value_counts().sort("isl_k").rows(),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "ISL", "sc_isl", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

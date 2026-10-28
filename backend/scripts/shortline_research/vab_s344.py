"""[短线研究 §344]「急跌后放量止跌（恐慌盘被承接）→ 次日开盘买、持 5 日」VAB_h5。
预注册 docs/strategy-research.md §344，结果出来前写定，不调参。
  T 日 liq；复权 10 日涨跌 r10 = ac_T/ac_{T-10} − 1 ≤ −10%；T 日成交额 ≥ 2.5 × 前 20 日（T−20..T−1）平均成交额；
  T 日涨跌幅 ∈ [−1%, +2%]；CLV=(收−低)/(高−低) ≥ 0.5（高=低视为不合格）；T 收盘未封涨停、未封跌停。
  同日按 放量倍数（T 成交额 / 前 20 日均额）降序前 5。引擎 realistic：T+1 开盘买、持 5 日、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/vab_s344.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "VAB_h5"


def prep():
    df = build().sort("code", "trade_date")
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    pc = c("close").shift(1).over("code")
    ul = pl.coalesce(c("up_limit"), pc * (1 + band))
    dl = pl.coalesce(c("down_limit"), pc * (1 - band))
    df = df.with_columns((c("close") >= ul - 0.005).fill_null(False).alias("luc"),
                         (c("close") <= dl + 0.005).fill_null(False).alias("ldc"),
                         (c("ac") / c("ac").shift(10).over("code") - 1).alias("r10"),
                         c("amount").shift(1).rolling_mean(20).over("code").alias("pamt20"))
    df = df.with_columns((c("amount") / c("pamt20")).alias("vr"),
                         pl.when(c("high") > c("low")).then((c("close") - c("low")) / (c("high") - c("low"))).otherwise(None).alias("clv"))
    cand = (c("liq") & (c("r10") <= -0.10) & (c("vr") >= 2.5) & c("pct_chg").is_between(-1.0, 2.0)
            & (c("clv") >= 0.5) & ~c("luc") & ~c("ldc")).fill_null(False)
    df = df.with_columns(cand.alias("cand"))
    df = df.with_columns(pl.when(c("cand")).then(-c("vr")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= MAX_NEW)).fill_null(False).alias("VAB"),
                           pl.when(c("cand")).then(c("vr")).otherwise(-1e9).alias("sc_vab")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("VAB") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {"n_sig": w.height, "n_cand": df.filter(c("cand") & c("trade_date").is_between(pl.lit(s), pl.lit(e))).height}
        if w.height:
            info.update({"sigdays": w["trade_date"].n_unique(), "n_codes": w["code"].n_unique(),
                         "r10_med": round(float(w["r10"].median()), 3), "vr_med": round(float(w["vr"].median()), 2),
                         "mv_med_yi": round(float(w["total_mv"].cast(pl.Float64, strict=False).median()) / 1e4, 1),
                         "month_top": w.group_by(c("trade_date").str.slice(0, 7)).len().sort("len", descending=True).head(3).rows()})
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "VAB", "sc_vab", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

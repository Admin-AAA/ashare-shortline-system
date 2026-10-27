"""[短线研究 §316]「W 底（双底）放量突破颈线 → 次日买、持 5 日」WDB_h5（经典形态学：两次探底在同一价位附近、第二次不破第一次，
收盘放量站上两底之间的颈线 = 底部确认）。预注册 docs/strategy-research.md §316，结果出来前写定，不调参。
  ah/al = high/low × ac/close（复权高/低）。
  L1 = T−40..T−21 的最低 al；L2 = T−15..T−3 的最低 al；neck = T−20..T−1 的最高 ah；pk = T−80..T−41 的最高 ah。
  条件：liq；|L2/L1 − 1| ≤ 3%；neck / max(L1,L2) − 1 ≥ 8%；pk ≥ 1.15·L1（第一底之前先跌过 ≥15%）；
        ac_T > neck（收盘突破颈线）；ac_T ≥ ao_T（阳线）；amount_T ≥ 1.5·amt20_T；T 非涨停收盘（close < 涨停价 −0.005）。
  每个信号日按 amt20 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/wdb_s316.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "WDB_h5"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns((c("high") * c("ac") / c("close")).alias("ah"), (c("low") * c("ac") / c("close")).alias("al"))
    df = df.with_columns(c("al").shift(21).rolling_min(20).over("code").alias("L1"),
                         c("al").shift(3).rolling_min(13).over("code").alias("L2"),
                         c("ah").shift(1).rolling_max(20).over("code").alias("neck"),
                         c("ah").shift(41).rolling_max(40).over("code").alias("pk"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    cand = (c("liq") & ((c("L2") / c("L1") - 1).abs() <= 0.03) & (c("neck") / pl.max_horizontal("L1", "L2") - 1 >= 0.08)
            & (c("pk") >= 1.15 * c("L1")) & (c("ac") > c("neck")) & (c("ac") >= c("ao"))
            & (c("amount") >= 1.5 * c("amt20")) & (c("close") < ul - 0.005)).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("amt20")).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("WDB"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_wdb")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("WDB") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "sig_pct_med": round(float(w["pct_chg"].median()), 2),
                    "depth_med": round(float(w.select(c("neck") / pl.max_horizontal("L1", "L2") - 1).to_series().median()), 3),
                    "r20_med": round(float(w["r20"].median()), 3), "r60_med": round(float(w["r60"].median()), 3),
                    "amt_ratio_med": round(float((w["amount"] / w["amt20"]).median()), 3)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "WDB", "sc_wdb", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

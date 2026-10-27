"""[短线研究 §298]「均线粘合后放量向上发散 → 次日买、持 5 日」MAB_h5（A 股经典技术形态"均线粘合向上发散"：
5/10/20/60 日均线挤在一起说明多空成本趋同、方向待选；某天放量收在全部均线之上 = 向上选择方向）。
预注册 docs/strategy-research.md §298，结果出来前写定，不调参。
  均线用复权收盘 ac：ma5/ma10/ma20/ma60。spread = max(四线)/min(四线) − 1。
  条件：spread_{T-1} ≤ 3%；T 日 ac > max(四线_T)；pct_chg_T ≥ +3；T 非涨停收盘（close < up_limit − 0.005，
        缺涨停价按前收 +9.8%/+19.8%）；amount_T ≥ 1.5 × amt20_{T-1}；liq。
  每个信号日按 amt20 降序取前 5。引擎 realistic：T+1 开盘买（开盘涨停买不进、不递补）、持 5 日、跌停锁死顺延、0.3% 成本；
  K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/mab_s298.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "MAB_h5"


def prep():
    df = build().sort("code", "trade_date")
    df = df.with_columns([c("ac").rolling_mean(n).over("code").alias(f"ma{n}") for n in (5, 10, 20)])
    hi = pl.max_horizontal("ma5", "ma10", "ma20", "m60"); lo = pl.min_horizontal("ma5", "ma10", "ma20", "m60")
    df = df.with_columns((hi / lo - 1).alias("spread"), hi.alias("mahi"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    ul = pl.coalesce(c("up_limit"), c("close").shift(1).over("code") * (1 + band))
    cand = (c("liq") & (c("spread").shift(1).over("code") <= 0.03) & (c("ac") > c("mahi")) & (c("pct_chg") >= 3)
            & (c("close") < ul - 0.005) & (c("amount") >= 1.5 * c("amt20").shift(1).over("code"))).fill_null(False)
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(-c("amt20").fill_null(0)).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("MAB"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_mab")).drop("_rk").sort("code", "trade_date")


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
        w = df.filter(c("MAB") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        info = {}
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "n_sig": w.height, "sig_pct_med": round(float(w["pct_chg"].median()), 2),
                    "spread_med": round(float(w["spread"].median()), 4), "r20_med": round(float(w["r20"].median()), 3),
                    "amt_x_med": round(float((w["amount"] / w["amt20"]).median()), 2)}
        res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "MAB", "sc_mab", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

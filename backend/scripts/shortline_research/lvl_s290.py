"""[短线研究 §290]「缩量下跌的输家 → 次日买、持 5 日」LVL_h5（Conrad–Hameed–Niden 1994 / Cooper 1999：
成交量萎缩中的短期输家更容易反弹、放量下跌的输家更容易续跌；邢不行精华帖《从量价异动看A股市场有效性：低位放量与高位放量的收益差异分析》的量价不对称思路）。
预注册 docs/strategy-research.md §290，结果出来前写定，不调参。
  r5  = ac / ac.shift(5) - 1（近 5 个交易日复权收益，含 T）。
  vr5 = 近 5 日（含 T）成交额均值 / 再往前 20 日（T-24..T-5）成交额均值。
  候选：liq & r5 <= -0.10 & vr5 <= 0.80 & 信号日非跌停收盘（close > down_limit+0.005，缺涨跌停价时按前收 -9.8%/-19.8%）。
  每日 r5 升序（跌得最多优先；同值按 amt20 降序）取前 5。
引擎 realistic：T 收盘信号、T+1 开盘买（开盘涨停买不进、不递补）、持 5 日（fixed(5)）、跌停锁死顺延、0.3% 成本；
K=20、max_new=5（仅回测，实盘不设仓位上限）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/lvl_s290.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "LVL_h5"


def prep():
    df = build().sort("code", "trade_date")
    amt = c("amount").cast(pl.Float64)
    df = df.with_columns((c("ac") / c("ac").shift(5).over("code") - 1).alias("r5"),
                         amt.rolling_mean(5).over("code").alias("_a5"),
                         amt.shift(5).rolling_mean(20).over("code").alias("_a20p"))
    df = df.with_columns((c("_a5") / c("_a20p")).alias("vr5"))
    band = pl.when(c("code").str.starts_with("3") | c("code").str.starts_with("688")).then(0.198).otherwise(0.098)
    dl = pl.coalesce(c("down_limit"), c("close").shift(1).over("code") * (1 - band))
    not_ld = (c("close") > dl + 0.005)
    cand = (c("liq") & (c("r5") <= -0.10) & (c("vr5") <= 0.80) & c("vr5").is_finite() & not_ld).fill_null(False)
    key = c("r5") - c("amt20").fill_null(0) * 1e-15
    df = df.with_columns(cand.alias("cand")).with_columns(pl.when(c("cand")).then(key).rank("ordinal").over("trade_date").alias("_rk"))
    return df.with_columns((c("cand") & (c("_rk") <= 5)).fill_null(False).alias("LVL"),
                           (-c("_rk").cast(pl.Float64)).fill_null(-1e9).alias("sc_lvl")).drop("_rk", "_a5", "_a20p").sort("code", "trade_date")


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
        w = df.filter(c("LVL") & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        if w.height:
            info = {"sigdays": w["trade_date"].n_unique(), "r5_med": round(float(w["r5"].median()), 3),
                    "vr5_med": round(float(w["vr5"].median()), 3), "sig_pct_med": round(float(w["pct_chg"].median()), 2)}
            res.setdefault("_info", {})[ph] = info; print("INFO", ph, info, flush=True)
        t, cur = run_real(df, "LVL", "sc_lvl", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

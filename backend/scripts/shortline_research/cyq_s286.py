"""[短线研究 §286]「筹码穿越：获利盘一天内从 ≤50% 跳到 ≥90%，次日买、持 5 日」CYQX_h5（邢不行第 102 期筹码分布）。
预注册 docs/strategy-research.md §286，结果出来前写定，不调参。筹码算法见 midterm/cyq_chips.py。
  CYQX：liq（amt20>5000万、上市>250日、非ST、非北交所）& WR_{T−1} ≤ 0.50 & WR_T ≥ 0.90。排序 = amt20 降序（中性）。
引擎 realistic：T 收盘信号、T+1 开盘买（开盘涨停买不进、不递补）、持 5 日（fixed(5)）、跌停锁死顺延、0.3% 成本；
K=20、max_new=5（仅回测，实盘不设仓位上限）。cohort_bench 同市值五分位对照只作参考。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/cyq_s286.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
from cyq_chips import add_chips
c = pl.col
HOLD, K, MAX_NEW, NAME = 5, 20, 5, "CYQX_h5"


def prep():
    df = add_chips(build())
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-6
    return df.with_columns(
        (c("liq") & (c("wr").shift(1).over("code") <= 0.50) & (c("wr") >= 0.90)).fill_null(False).alias("CYQX"),
        (c("amt20").fill_null(0) / 1e6 - tie).alias("sc_amt")).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(c("CYQX").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "CYQX", "sc_amt", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{NAME}_{ph.split()[0]}.csv"))
        res.setdefault(NAME, {})[ph] = m
        print(NAME, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

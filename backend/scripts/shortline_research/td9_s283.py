"""[短线研究 §283] 「神奇九转逃顶信号反用：连涨结构第 9 天次日买、持 5 日」TD9U_h5（邢不行第 91 期《支付宝的付费选股指标「神奇九转」有用吗?》：
逃顶信号 60 万次之后 1/5/10 天平均收益显著为正、且比抄底信号更高）。预注册 docs/strategy-research.md §283，结果出来前写定，不调参。
  TD 卖出结构计数：ac_t > ac_{t-4} 记 1 天，连续计数，断开归零（ac=复权收盘，同 lt_research.build）。
  TD9U：liq（成交额20日>5000万、上市>250日、非ST、非北交所）& 计数 == 9（第一次走到 9，不含 10、11…）。
  排序分 = amt20 降序（流动性最好的优先，中性排序）。
引擎 realistic：T 收盘出信号、T+1 开盘买（开盘涨停买不进、不递补）、持 5 个交易日（fixed(5)）、跌停锁死顺延、0.3% 成本；K=20、max_new=5（仅回测，实盘不设仓位上限）。
对照：cohort_bench 同日同市值五分位（参考项，不否决）。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/td9_s283.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import PH, full_metrics
c = pl.col
HOLD, K, MAX_NEW = 5, 20, 5


def prep():
    df = build().sort("code", "trade_date")
    up = (c("ac") > c("ac").shift(4).over("code")).fill_null(False)
    grp = (~up).cast(pl.Int32).cum_sum().over("code")
    df = df.with_columns(up.alias("_up")).with_columns(grp.alias("_g"))
    df = df.with_columns(pl.when(c("_up")).then(c("_up").cast(pl.Int32).cum_sum().over(["code", "_g"])).otherwise(0).alias("td_up"))
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-6
    return df.with_columns(
        (c("liq") & (c("td_up") == 9)).fill_null(False).alias("TD9U"),
        (c("amt20").fill_null(0) / 1e6 - tie).alias("sc_amt"),
    ).drop("_up", "_g").sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(c("TD9U").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        t, cur = run_real(df, "TD9U", "sc_amt", fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
        m = full_metrics(df, t, cur)
        if t is not None and t.height:
            m["streak"] = float(streak(t)); m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
            t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_TD9U_h5_{ph.split()[0]}.csv"))
        res.setdefault("TD9U_h5", {})[ph] = m
        print("TD9U_h5", ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

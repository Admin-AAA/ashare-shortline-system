"""[短线研究 §280] 「散户大卖、次日买入、持 3 日」RSO_1 / RSO_2（邢不行第 89 期《量化1000万条散户交易记录后，反向操作有多爽》）。
预注册 docs/strategy-research.md §280，结果出来前写定，不调参。
rs = 小单净流入(元) / 当日成交额(元)（capital_flow.small_net_inflow_yi×1e8 / amount）；越负 = 散户净卖出占成交越大。
RSO_1：liq & rs<0，排序分 −rs；RSO_2：再要求 主力净流入 main_net_inflow_yi>0（主力承接）。
引擎 realistic：T 收盘出信号、T+1 开盘买（开盘涨停买不进）、持 3 日（T+4 开盘卖，跌停锁死顺延）、0.3% 成本；K=20、max_new=5（仅回测）。
对照：CTL_s = 同日 liq 且有资金流数据的股票随机排序（hash 种子 0..9）；cohort_bench 同日同市值五分位。
用法：cd backend/scripts/midterm && LT_START=.. LT_END=.. PYTHONPATH=../..:. python ../shortline_research/rso_s280.py "<phase>" out.json"""
import sys, json, os
import polars as pl
from lt_research import fixed, build
from realistic import run_real
from lt_research46 import full_metrics
c = pl.col
PH = {"W0 2022-04~2023-03": ("2022-04-01", "2023-03-31"), "W00 2023-04~12": ("2023-04-01", "2023-12-31"),
      "W1 2024": ("2024-01-01", "2024-12-31"), "W2 2025-01~09": ("2025-01-01", "2025-09-30"),
      "R12 2025-10~2026-10": ("2025-10-01", "2026-10-16"), "N3 2024-01~2026-10": ("2024-01-01", "2026-10-16")}
HOLD, K, MAX_NEW, NSEED = 3, 20, 5, 10
CF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../output/research/s280/capital_flow.parquet")


def prep():
    df = build()
    cf = pl.read_parquet(CF)
    df = df.join(cf, on=["code", "trade_date"], how="left")
    rs = c("small_net") * 1e8 / c("amount")
    tie = c("code").cast(pl.Int64, strict=False).fill_null(0) * 1e-10
    ok = c("liq") & c("small_net").is_not_null() & (c("amount") > 0)
    df = df.with_columns(rs.alias("rs"))
    cols = [(ok & (c("rs") < 0)).fill_null(False).alias("RSO_1"),
            (ok & (c("rs") < 0) & (c("main_net") > 0)).fill_null(False).alias("RSO_2"),
            ok.fill_null(False).alias("CTL"),
            (-c("rs")).fill_null(0).sub(tie).alias("sc_rs")]
    for s in range(NSEED):
        cols.append((pl.concat_str([c("code"), c("trade_date"), pl.lit(str(s))]).hash(s + 7).cast(pl.Float64) / 1.8446744073709552e19).alias(f"h{s}"))
    return df.with_columns(cols).sort("code", "trade_date")


def streak(t):
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0; mx = max(mx, k)
    return mx


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    yr = c("trade_date").str.slice(0, 4).alias("y")
    print("signals per year:", df.group_by(yr).agg(c("RSO_1").sum(), c("RSO_2").sum(), c("CTL").sum()).sort("y").to_dicts(), flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    V = {"RSO_1": ("RSO_1", "sc_rs"), "RSO_2": ("RSO_2", "sc_rs")}
    V.update({f"CTL_{s}": ("CTL", f"h{s}") for s in range(NSEED)})
    for ph in phases:
        s, e = PH[ph]
        for n, (sig, sc) in V.items():
            t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t))
                if not n.startswith("CTL"):
                    m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                    t.write_csv(os.path.join(os.path.dirname(out), f"trades_{n}_{ph.split()[0]}.csv"))
            res.setdefault(n, {})[ph] = m
            if not n.startswith("CTL") or n in ("CTL_0", "CTL_9"):
                print(n, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)

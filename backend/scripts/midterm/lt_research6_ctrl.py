"""[中线] 股东户数下降 — 市值匹配对照（判断收益是信号还是小盘 beta）。预设，不调参。
对 lt_research6 每笔成交：同一入场/出场日，取信号日同市值五分位、流动性合格的全体股票，
等权计算 开盘入→开盘出 收益（同样扣 0.3% 成本）作为基准；报告每笔超额 = 策略 − 基准。
采纳附加条件：R12 与 8 阶段多数的每笔超额 > 0。"""
import sys, json
import polars as pl
from lt_research import fixed, PH
from realistic import run_real, metrics_real, COST
import lt_research6 as R6
from lt_research import build
from lt_research2 import regime
c = pl.col

def cohort_bench(df, t):
    """t: trades (code, entry, exit). benchmark = mean over same-quintile liquid stocks on signal day."""
    td = df.select("trade_date").unique().sort("trade_date").with_columns(c("trade_date").shift(1).alias("sig"))
    q = (df.filter(c("liq") & c("total_mv").is_not_null())
           .with_columns((c("total_mv").rank().over("trade_date") * 5 / pl.len().over("trade_date")).ceil().clip(1, 5).alias("q"))
           .select("code", "trade_date", "q"))
    px = df.select("code", "trade_date", "ao")
    t = (t.with_row_index("tid").join(td.rename({"trade_date": "entry"}), on="entry", how="left")
          .join(q.rename({"trade_date": "sig"}), on=["code", "sig"], how="left"))
    coh = (t.select("tid", "sig", "q", "entry", "exit").join(q.rename({"trade_date": "sig", "code": "m"}), on=["sig", "q"])
             .join(px.rename({"trade_date": "entry", "code": "m", "ao": "a0"}), on=["m", "entry"])
             .join(px.rename({"trade_date": "exit", "code": "m", "ao": "a1"}), on=["m", "exit"])
             .group_by("tid").agg((c("a1") / c("a0") - 1 - COST).clip(-0.9, 5).mean().alias("bench"), pl.len().alias("nc")))
    return t.join(coh, on="tid", how="left")

if __name__ == "__main__":
    df = build()
    ev = R6.holder_events(df.select("trade_date").unique().sort("trade_date"))
    df = regime(df.join(ev, on=["code", "trade_date"], how="left"))
    base = c("liq") & c("hn_chg").is_not_null() & (c("hn_chg") <= -0.10)
    g20 = c("ewi") > c("e20")
    df = df.with_columns(base.fill_null(False).alias("H1"), (base & (c("hn_chg_prev") < 0)).fill_null(False).alias("H2"),
        (base & (c("r20") < 0.15) & (c("ac") > c("m60"))).fill_null(False).alias("H3"), (base & g20).fill_null(False).alias("H4"),
        (base & (c("total_mv") < 1.5e6)).fill_null(False).alias("H5"), (-c("hn_chg")).fill_null(0).alias("sc_hn"))
    C = {"hn10_h20": ("H1", 20), "hn10_2q_h20": ("H2", 20), "hn10_trend_h20": ("H3", 20),
         "hn10_g20_h20": ("H4", 20), "hn10_small_h20": ("H5", 20), "hn10_h40": ("H1", 40)}
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lt6c.json"
    res = {}
    for n, (sig, h) in C.items():
        print(f"## {n}", flush=True)
        for ph, (s, e) in PH.items():
            t, cur = run_real(df, sig, "sc_hn", fixed(h), s, e, k_slots=20, max_new=5, max_hold=h)
            m = metrics_real(t, cur)
            if t is not None and t.height:
                b = cohort_bench(df, t)
                m["bench"] = round(100 * float(b["bench"].mean()), 2); m["excess"] = round(m["avg"] - m["bench"], 2)
                m["ex_win"] = round(100 * float(((b["ret"] - b["bench"]) > 0).mean()), 1)
            res.setdefault(n, {})[ph] = {a: float(v) for a, v in m.items()}
            print(" ", ph, m, flush=True)
        print("  pos", sum(v.get("avg", -1) > 0 for v in res[n].values()), "/8  excess+",
              sum(v.get("excess", -1) > 0 for v in res[n].values()), "/8", flush=True)
    json.dump(res, open(out, "w"), indent=1)

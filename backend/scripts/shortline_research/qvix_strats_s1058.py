"""[短线研究 §1058] QVIX 恐慌尖峰 × 流动性篮子：REF_CAP / QV2_ONLY / QVC_OR。

预注册 docs/strategy-research.md §1058（早于任何收益回测）。
生产同构：simulate + execution_policy_for_next_open_entry，E1=TP5/SL4.5/trailing关/h5，0.3% 成本。
载体/排序三版本相同：liq & 未封涨停 & 当日成交额>=1亿，当日成交额降序前 10，K=20/max_new=10。
唯一变量是恐慌触发器：PRICE_CAP=RSI6<20 & lu_ld<=0.34；QV_SPIKE=QVIX50 5日涨幅>25%。

用法：cd backend/scripts/midterm
  set -a && . ../../.env && set +a
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/qvix_strats_s1058.py "<phase>" out.json
"""
from __future__ import annotations

import json
import os
import sys

import polars as pl

from lt_research46 import PH
from mrsi_exit_s1030 import build_policy, metrics, run_sim
from mrsi_s1013 import market_rsi

c = pl.col
D = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "s1058")
PANEL = os.path.join(D, "panel_qvix.parquet")
K, MAX_NEW, HOLD = 20, 10, 5
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
CONFIGS = [("REF_CAP", "S_REF"), ("QV2_ONLY", "S_QV2"), ("QVC_OR", "S_OR")]


def prep() -> pl.DataFrame:
    df = pl.read_parquet(PANEL)
    mr = market_rsi(df).select("trade_date", "rsi6")
    df = df.join(mr, on="trade_date", how="left")
    price_cap = (c("rsi6") < 20) & (c("mkt_lu_ld") <= 0.34)
    qv_spike = c("qv_chg5") > 0.25
    pool = c("liq") & ~c("luc") & (c("amount") >= 1e8)
    df = df.with_columns(
        (pool & price_cap).fill_null(False).alias("S_REF"),
        (pool & qv_spike).fill_null(False).alias("S_QV2"),
        (pool & (price_cap | qv_spike)).fill_null(False).alias("S_OR"),
        price_cap.fill_null(False).alias("PRICE_CAP"),
        qv_spike.fill_null(False).alias("QV_SPIKE"),
        c("amount").alias("sc_amt"),
    )
    return df.sort("code", "trade_date")


if __name__ == "__main__":
    phase_arg, out = sys.argv[1], sys.argv[2]
    phases = [p for p in phase_arg.split(",") if p in PH]
    if not phases:
        raise SystemExit(f"unknown phase: {phase_arg}")
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        # 市场级触发日诊断
        day = (df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
               .select("trade_date", "PRICE_CAP", "QV_SPIKE").unique())
        res.setdefault("_days", {})[ph] = {
            "price_cap": int(day.filter(c("PRICE_CAP")).height),
            "qv_spike": int(day.filter(c("QV_SPIKE")).height),
            "qv_only": int(day.filter(c("QV_SPIKE") & ~c("PRICE_CAP")).height),
            "or": int(day.filter(c("PRICE_CAP") | c("QV_SPIKE")).height),
        }
        for name, sig in CONFIGS:
            policy = build_policy(HOLD, "TP5")
            w = df.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            res.setdefault("_info", {}).setdefault(ph, {})[name] = {
                "n_sig": w.height, "sigdays": w["trade_date"].n_unique()}
            t = run_sim(df, sig, "sc_amt", policy, s, e, k_slots=K, max_new=MAX_NEW, horizon=HOLD)
            m = metrics(t, df, K)
            if t is not None and t.height:
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, {k: m.get(k) for k in ("n", "win", "avg", "pf", "mdd", "days")}, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
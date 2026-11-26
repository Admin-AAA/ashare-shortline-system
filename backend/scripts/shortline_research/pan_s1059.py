"""[短线研究 §1059–§1062] 恐慌出清后的新篮子 / 新确认日（4 个想法一次预注册）。

预注册 docs/strategy-research.md §1059–§1062（写于跑任何收益之前，规则写定后不改）。
面板复用 §1058 的 panel_qvix.parquet（2018-01-05~2026-11-13，含 liq/luc/amount/ac/ao/pe_ttm/dv_ttm/total_mv/
mkt_lu_ld/qvix50/qv_chg5），市场等权收益与 RSI6 用 mrsi_s1013.market_rsi 同口径重算。
生产同构：simulate + execution_policy_for_next_open_entry，E1=TP5/SL4.5/trailing关/h5，往返 0.3%，
K=20、max_new=10（仅回测口径；实盘不设仓位上限）。

  PRICE_CAP = 等权 RSI6<20 且 mkt_lu_ld<=0.34；QV_SPIKE = QVIX50 五日涨幅>25%
  PROD_TRIG = PRICE_CAP 或 QV_SPIKE（现役 rsi6-capitulation 0.2.0 的触发器）
  pool      = liq & 信号日收盘未封涨停 & 当日成交额>=1 亿

  §1059 RPDROP_h5 : 任意日；pool & 收盘未封跌停 & T 日涨跌幅<=-5% & 近 60 个交易日内（T-59..T-1，不含 T）有回购预案
                    且金额>=市值 0.5%；按 T 日涨跌幅升序（跌得越多越先）前 10
  §1060 SMALL_h5  : PROD_TRIG 日；pool 按总市值升序前 10
  §1061 VALUE_h5  : PROD_TRIG 日；pool & 0<pe_ttm<=20 & dv_ttm>=2.0；按 dv_ttm 降序前 10
  §1062 RPLAN_h5  : PROD_TRIG 日；pool & 近 60 个交易日（含 T）内有回购预案且金额>=市值 0.5%；按回购金额/市值降序前 10
  REF_LIQ（对照）: PROD_TRIG 日；pool 按当日成交额降序前 10（= 现役 rsi6-capitulation 口径）

用法：cd backend/scripts/midterm
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/pan_s1059.py count
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/pan_s1059.py "<phase>" out.json
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
PANEL = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "s1058", "panel_qvix.parquet")
K, MAX_NEW, HOLD = 20, 10, 5
DROP_TH = -5.0
RP_WIN, RP_RATIO = 60, 0.005
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
CONFIGS = [
    ("RPDROP_h5", "S_RPDROP", "sc_drop"),
    ("SMALL_h5", "S_SMALL", "sc_small"),
    ("VALUE_h5", "S_VALUE", "sc_dv"),
    ("RPLAN_h5", "S_RPLAN", "sc_rp"),
    ("REF_LIQ", "S_REF", "sc_amt"),
]


def repo_plan(td: pl.DataFrame) -> pl.DataFrame:
    """回购预案（同一股票 365 天内首次）的信号日 sd 与金额，复用 lt_research12.repo_sig 口径。"""
    from lt_research12 import repo_sig

    return repo_sig(td).select("code", "trade_date", "rp_amt")


def prep() -> pl.DataFrame:
    cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "ac", "ao", "pct_chg",
            "liq", "luc", "ldc", "pe_ttm", "dv_ttm", "total_mv", "mkt_lu_ld", "qvix50", "qv_chg5"]
    df = pl.read_parquet(PANEL, columns=cols)
    mr = market_rsi(df).select("trade_date", "mkt_ret", "rsi6")
    day = (df.select("trade_date", "mkt_lu_ld", "qv_chg5").unique("trade_date").sort("trade_date")
           .join(mr, on="trade_date", how="left").sort("trade_date"))
    day = day.with_columns(
        ((c("rsi6") < 20) & (c("mkt_lu_ld") <= 0.34)).fill_null(False).alias("PRICE_CAP"),
        (c("qv_chg5") > 0.25).fill_null(False).alias("QV_SPIKE"),
    ).with_columns((c("PRICE_CAP") | c("QV_SPIKE")).alias("PROD_TRIG"))
    df = df.drop("mkt_lu_ld", "qv_chg5").join(
        day.select("trade_date", "mkt_ret", "PRICE_CAP", "QV_SPIKE", "PROD_TRIG"), on="trade_date", how="left")

    # 回购预案窗口：近 RP_WIN 个交易日（含 T）内最近一次预案的金额/市值
    td = df.select("trade_date").unique().sort("trade_date")
    rp = repo_plan(td)
    df = df.sort("code", "trade_date").join(rp, on=["code", "trade_date"], how="left")
    df = df.with_columns(
        (c("rp_amt") / (c("total_mv").cast(pl.Float64, strict=False) * 1e4)).alias("_rpr_evt"))
    df = df.with_columns(
        c("_rpr_evt").forward_fill(limit=RP_WIN - 1).over("code").alias("rp_ratio_win"),
        # 不含 T：上一行的窗口值（T-59..T-1）
        c("_rpr_evt").shift(1).forward_fill(limit=RP_WIN - 2).over("code").alias("rp_ratio_prev"))

    pool = (c("liq") & ~c("luc") & (c("amount") >= 1e8)).fill_null(False)
    trig = c("PROD_TRIG").fill_null(False)
    value = (c("pe_ttm") > 0) & (c("pe_ttm") <= 20) & (c("dv_ttm") >= 2.0)
    df = df.with_columns(
        (pool & ~c("ldc").fill_null(False) & (c("pct_chg") <= DROP_TH).fill_null(False)
         & (c("rp_ratio_prev") >= RP_RATIO).fill_null(False)).alias("S_RPDROP"),
        (pool & trig).alias("S_SMALL"),
        (pool & trig & value.fill_null(False)).alias("S_VALUE"),
        (pool & trig & (c("rp_ratio_win") >= RP_RATIO).fill_null(False)).alias("S_RPLAN"),
        (pool & trig).alias("S_REF"),
        c("amount").alias("sc_amt"),
        (-c("pct_chg")).fill_null(-1e9).alias("sc_drop"),
        (-c("total_mv")).fill_null(-1e18).alias("sc_small"),
        c("dv_ttm").fill_null(-1e9).alias("sc_dv"),
        c("rp_ratio_win").fill_null(-1e9).alias("sc_rp"),
    )
    return df.sort("code", "trade_date")


def count(df: pl.DataFrame) -> dict:
    out = {}
    for ph, (s, e) in PH.items():
        w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
        dd = w.select("trade_date", "PRICE_CAP", "PROD_TRIG").unique("trade_date")
        row = {"price_cap_days": int(dd["PRICE_CAP"].sum()), "prod_trig_days": int(dd["PROD_TRIG"].sum())}
        for name, sig, _ in CONFIGS:
            x = w.filter(c(sig))
            row[name] = {"n_sig": x.height, "sigdays": x["trade_date"].n_unique()}
        out[ph] = row
        print(ph, row, flush=True)
    return out


if __name__ == "__main__":
    phase_arg = sys.argv[1]
    df = prep()
    if phase_arg == "count":
        od = os.path.join(os.path.dirname(PANEL), "..", "s1059")
        os.makedirs(od, exist_ok=True)
        json.dump(count(df), open(os.path.join(od, "count.json"), "w"), indent=1, ensure_ascii=False)
        raise SystemExit(0)
    out = sys.argv[2]
    phases = [p for p in phase_arg.split(",") if p in PH]
    if not phases:
        raise SystemExit(f"unknown phase: {phase_arg}")
    res = json.load(open(out)) if os.path.exists(out) else {}
    keep = ["code", "trade_date", "open", "high", "low", "close", "ac", "ao", "volume", "liq", "total_mv"] + \
        [x for _, s, sc in CONFIGS for x in (s, sc)]
    sim = df.select(list(dict.fromkeys(keep)))
    for ph in phases:
        s, e = PH[ph]
        for name, sig, sc in CONFIGS:
            policy = build_policy(HOLD, "TP5")
            w = sim.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            res.setdefault("_info", {}).setdefault(ph, {})[name] = {"n_sig": w.height, "sigdays": w["trade_date"].n_unique()}
            t = run_sim(sim, sig, sc, policy, s, e, k_slots=K, max_new=MAX_NEW, horizon=HOLD)
            m = metrics(t, sim, K)
            if t is not None and t.height:
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, {k: m.get(k) for k in ("n", "win", "avg", "pf", "mdd", "days")}, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

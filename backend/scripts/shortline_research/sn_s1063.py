"""[短线研究 §1063–§1064] 次新股（上市不满一年）两个想法：解禁周年日 / 深套超跌。

预注册 docs/strategy-research.md §1063–§1064（写于跑任何收益之前，规则写定后不改）。
系统以往所有研究股池都要求上市>250 个交易日，次新股从未被检验过；本批只在次新股里找信号。
面板复用 §1058 panel_qvix.parquet；上市日来自 MySQL stocks.list_date（只读导出到 s1063/list_date.parquet）。
引擎 realistic.run_real（与 §996/§997/§1000 及现役 mid-*-dip 同构）：T 收盘信号、T+1 开盘买（开盘涨停＝一字买不进、
不递补）、持 5 个交易日开盘卖、跌停锁死顺延、往返 0.3%；K=20、max_new=5（仅回测口径；实盘不设仓位上限）。

  SN 股池 = 上市日>=2018-03-01（面板覆盖上市以来全部日线）& amt20>5000 万 & 非 ST & 非北交所 & T 收盘未封涨停
  §1063 SNUL_h5 : T = 上市满 6 个月或满 12 个月（自然月）后的第一个交易日（各股各一次）& SN；按 r20 升序前 5
  §1064 SNDD_h5 : SN & 上市第 60~250 个交易日 & 复权收盘较上市以来最高复权收盘回撤<=-50%；按回撤升序（最深先）前 5

用法：cd backend/scripts/midterm
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/sn_s1063.py count
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python ../shortline_research/sn_s1063.py "<phase>" out.json
"""
from __future__ import annotations

import json
import os
import sys

import polars as pl

from lt_research46 import PH
from lt_research import fixed
from lt_research46 import full_metrics
from realistic import run_real

c = pl.col
R = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research")
PANEL = os.path.join(R, "s1058", "panel_qvix.parquet")
LIST = os.path.join(R, "s1063", "list_date.parquet")
K, MAX_NEW, HOLD = 20, 5, 5
AGE_LO, AGE_HI, DD_TH = 60, 250, -0.50
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
CONFIGS = [("SNUL_h5", "S_SNUL", "sc_r20"), ("SNDD_h5", "S_SNDD", "sc_dd")]


def prep() -> pl.DataFrame:
    cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "amt20", "ac", "ao",
            "st", "luc", "r20", "liq", "total_mv", "lim_up_open", "lock_down"]
    df = pl.read_parquet(PANEL, columns=cols)
    ld = pl.read_parquet(LIST)
    df = df.join(ld, on="code", how="left").sort("code", "trade_date")
    df = df.with_columns(
        c("list_date").str.to_date(strict=False).alias("_ld"),
        c("trade_date").str.to_date().alias("_td"),
        pl.int_range(pl.len()).over("code").alias("ag"),
        (c("ac") / c("ac").cum_max().over("code") - 1).alias("dd_lst"),
    )
    sn = ((c("list_date") >= "2018-03-01") & (c("amt20") > 5e7) & ~c("st").fill_null(False)
          & ~c("code").str.slice(0, 1).is_in(["4", "8", "9"]) & ~c("luc").fill_null(False)).fill_null(False)
    # 周年日：上市满 6 / 12 个月后的第一个交易日（各股各一次）
    ann = pl.lit(False)
    for m in (6, 12):
        after = c("_td") >= c("_ld").dt.offset_by(f"{m}mo")
        first = after & ~after.shift(1).over("code").fill_null(False)
        ann = ann | first.fill_null(False)
    df = df.with_columns(
        (sn & ann).alias("S_SNUL"),
        (sn & c("ag").is_between(AGE_LO, AGE_HI) & (c("dd_lst") <= DD_TH).fill_null(False)).alias("S_SNDD"),
        (-c("r20")).fill_null(-1e9).alias("sc_r20"),
        (-c("dd_lst")).fill_null(-1e9).alias("sc_dd"),
    )
    return df.drop("_ld", "_td")


if __name__ == "__main__":
    phase_arg = sys.argv[1]
    df = prep()
    if phase_arg == "count":
        out = {}
        for ph, (s, e) in PH.items():
            w = df.filter(c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            out[ph] = {n: {"n_sig": int(w[sig].sum()), "sigdays": w.filter(c(sig))["trade_date"].n_unique()} for n, sig, _ in CONFIGS}
            print(ph, out[ph], flush=True)
        json.dump(out, open(os.path.join(R, "s1063", "count.json"), "w"), indent=1, ensure_ascii=False)
        raise SystemExit(0)
    out = sys.argv[2]
    phases = [p for p in phase_arg.split(",") if p in PH]
    if not phases:
        raise SystemExit(f"unknown phase: {phase_arg}")
    res = json.load(open(out)) if os.path.exists(out) else {}
    keep = ["code", "trade_date", "ac", "ao", "lim_up_open", "lock_down", "liq", "total_mv", "r20"] + \
        [x for _, s, sc in CONFIGS for x in (s, sc)]
    sim = df.select(list(dict.fromkeys(keep)))
    od = os.path.dirname(out) or "."
    for ph in phases:
        s, e = PH[ph]
        for name, sig, sc in CONFIGS:
            w = sim.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            res.setdefault("_info", {}).setdefault(ph, {})[name] = {"n_sig": w.height, "sigdays": w["trade_date"].n_unique()}
            t, cur = run_real(sim, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = full_metrics(sim, t, cur)
            if t is not None and t.height:
                m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                t.write_csv(os.path.join(od, f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, {k: m.get(k) for k in ("n", "win", "avg", "pf", "mdd", "days", "excess")}, flush=True)
        os.makedirs(od, exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

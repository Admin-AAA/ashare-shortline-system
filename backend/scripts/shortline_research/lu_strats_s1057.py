"""[短线研究 §1057] 涨停断板反包族粗筛（realistic + fixed(5)，0.3% 成本）。

预注册 docs/strategy-research.md §1057（commit 7dcb5b84，早于任何收益回测）。
粗筛只用于淘汰明显无效版本；最终结论用 lu_exit_s1057.py（生产 simulate）。
信号/股池/排序全部按预注册写死，不调参。

用法：cd backend/scripts/midterm
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python \
    ../shortline_research/lu_strats_s1057.py "<phase>[,<phase>...]" out.json
"""
from __future__ import annotations

import json
import os
import sys

import polars as pl

from lt_research import fixed
from lt_research46 import PH, full_metrics
from realistic import run_real

c = pl.col
HOLD, K, MAX_NEW = 5, 20, 5
PANEL = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "lu_events", "panel.parquet")
VERSIONS = [("FBC", "S_FBC", "sc_fbc"), ("DBF1", "S_DBF1", "sc_dbf1"),
            ("DBF2", "S_DBF2", "sc_dbf2"), ("NMB", "S_NMB", "sc_nmb")]


def streak(t: pl.DataFrame) -> int:
    mx = k = 0
    for x in t.sort("entry")["ret"].to_list():
        k = k + 1 if x <= 0 else 0
        mx = max(mx, k)
    return mx


def prep() -> pl.DataFrame:
    df = pl.read_parquet(PANEL)
    # 用日期感知涨停价重算可交易性（与 luc 同口径）
    df = df.with_columns(
        (c("open") >= c("ul_px") - 0.005).fill_null(False).alias("lim_up_open"),
        (c("high") <= c("dl_px") + 0.005).fill_null(False).alias("lock_down"),
    )
    pool = c("liq") & (c("amount") >= 1e8)
    for name, sig, _sc in VERSIONS:
        df = df.with_columns((c(name) & pool).fill_null(False).alias(sig))
    return df.sort("code", "trade_date")


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        for name, sig, sc in VERSIONS:
            w = df.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            info = {"n_sig": w.height, "sigdays": w["trade_date"].n_unique()}
            res.setdefault("_info", {}).setdefault(ph, {})[name] = info
            t, cur = run_real(df, sig, sc, fixed(HOLD), s, e, k_slots=K, max_new=MAX_NEW, max_hold=HOLD)
            m = full_metrics(df, t, cur)
            if t is not None and t.height:
                m["streak"] = float(streak(t))
                m["p05"] = round(100 * float(t["ret"].quantile(0.05)), 2)
                m["median"] = round(100 * float(t["ret"].median()), 2)
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
            res.setdefault(name, {})[ph] = m
            print(name, ph, m, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
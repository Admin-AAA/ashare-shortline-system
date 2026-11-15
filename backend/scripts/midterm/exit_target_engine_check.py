#!/usr/bin/env python3
"""用生产同构引擎（realistic.run_real）复核「达标离场」在组合/名额口径下的表现。

与 exit_target_research.py 的简化组合模拟互为校验：这里直接复用项目引擎
（开盘涨停不买、跌停封死顺延、K=20 名额、每日最多新开 5 只、1/20 名义逐日盯市）。
exit_fn 在收盘判定，次日开盘卖（生产可执行口径；与"当天尾盘卖"的差异已在逐笔检验中量化）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(HERE))
import lt_data as L  # noqa: E402
from realistic import add_limits, run_real, metrics_real  # noqa: E402

c = pl.col
COST = 0.003
OUT = BACKEND / "output" / "research" / "exit_target"
OUT.mkdir(parents=True, exist_ok=True)
PH = {"R12": ("2025-10-01", "2026-10-16"), "N3": ("2024-01-01", "2026-10-16"),
      "DEV": ("2019-01-01", "2023-12-31")}


def panel():
    df = L.prices()
    df = df.with_columns(
        ((1 + c("pct_chg").fill_null(0).clip(-35, 35) / 100).cum_prod().over("code")).alias("ac")
    ).with_columns((c("open") * c("ac") / c("close")).alias("ao"))
    return add_limits(df.sort("code", "trade_date"))


def fixed(n):
    return lambda a, e, i: i - e >= n - 1


def main():
    df = panel()
    out = {}
    for S in ("EA", "FA"):
        x = pl.read_parquet(BACKEND / "output/research/rv1030" / f"sig_{S}.parquet").filter(
            ~c("partial") & ~c("lu_e").fill_null(False) & (c("trade_date") <= "2026-10-16"))
        if S == "EA":
            n = pl.len().over("trade_date")
            x = x.with_columns(((c("d0").rank().over("trade_date") / n)
                                + ((-c("total_mv")).rank().over("trade_date") / n)).alias("rk_score"))
            score = "rk_score"
        else:
            score = "score"
        sig = x.select("code", "trade_date", score).with_columns(pl.lit(True).alias("SIG"))
        d = df.join(sig, on=["code", "trade_date"], how="left").with_columns(c("SIG").fill_null(False))
        # 开发期目标（与逐笔脚本一致，含成本前口径差异可忽略）
        t_dev = {"EA": 0.0036, "FA": 0.0065}[S]
        variants = {"base": fixed(20), "T_dev": (lambda a, e, i, t=t_dev: a["ac"][i] / a["ao"][e] - 1 >= t),
                    "T5": (lambda a, e, i: a["ac"][i] / a["ao"][e] - 1 >= 0.05)}
        for vn, fn in variants.items():
            for pn, (s, e) in PH.items():
                t, cur = run_real(d, "SIG", score, fn, s, e, k_slots=20, max_new=5, max_hold=20)
                m = metrics_real(t, cur)
                out.setdefault(S, {}).setdefault(vn, {})[pn] = m
                print(S, vn, pn, m, flush=True)
    (OUT / "engine_check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("saved", OUT / "engine_check.json", flush=True)


if __name__ == "__main__":
    main()

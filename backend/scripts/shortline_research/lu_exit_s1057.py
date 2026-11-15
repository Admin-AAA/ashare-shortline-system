"""[短线研究 §1057] 涨停断板反包族 — 生产同构回测。

预注册 docs/strategy-research.md §1057（commit 7dcb5b84，早于任何收益回测）。
执行：app.engine.simulation.simulate + execution_policy_for_next_open_entry()
（T+1 开盘买、一字涨停买不进、跌停锁死顺延、盘中止盈/止损 STOP_LOSS 优先、到期 horizon_close）。
E1 主口径（采纳判定）：TP5（default5/max15）+ SL4.5（default4.5/max4.7）+ trailing 关 + h5。
E2 敏感性（只报告）：生产默认 TP6 + trailing 2/0.8/0.3/0.5 + SL4.5，h5。
费用：佣金 0.01%×2 + 印花税 0.05% + 滑点 11.5bp×2 = 往返 0.30%。
复用 mrsi_exit_s1030 的 run_sim/metrics/build_policy（rsi6-capitulation 已验证同构 harness）。

用法：cd backend/scripts/midterm
  PYTHONPATH=../..:.:../shortline_research ../../.venv/bin/python \
    ../shortline_research/lu_exit_s1057.py "<phase>" out.json
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import polars as pl

from lt_research46 import PH
from mrsi_exit_s1030 import build_policy, metrics, run_sim

c = pl.col
PANEL = os.path.join(os.path.dirname(__file__), "..", "..", "output", "research", "lu_events", "panel.parquet")
K, MAX_NEW, HOLD = 20, 5, 5
PH = dict(PH)
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")

# (name, sig_col, score_col, exit_mode)
MAIN = [("FBC_E1", "S_FBC", "sc_fbc", "TP5"),
        ("DBF1_E1", "S_DBF1", "sc_dbf1", "TP5"),
        ("DBF2_E1", "S_DBF2", "sc_dbf2", "TP5"),
        ("NMB_E1", "S_NMB", "sc_nmb", "TP5")]
E2 = [("FBC_E2", "S_FBC", "sc_fbc", "PROD"),
      ("DBF1_E2", "S_DBF1", "sc_dbf1", "PROD"),
      ("DBF2_E2", "S_DBF2", "sc_dbf2", "PROD"),
      ("NMB_E2", "S_NMB", "sc_nmb", "PROD")]
CTRL = [("ALL_E1", "S_ALL", "sc_all", "TP5")]
E2_PHASES = {"R12 2025-10~2026-10", "N3 2024-01~2026-10"}
CTRL_PHASES = E2_PHASES | {"W0 2022-04~2023-03", "W00 2023-04~12"}


def prep() -> pl.DataFrame:
    df = pl.read_parquet(PANEL)
    pool = c("liq") & (c("amount") >= 1e8)
    for name, sig, _sc in [("FBC", "S_FBC", None), ("DBF1", "S_DBF1", None),
                           ("DBF2", "S_DBF2", None), ("NMB", "S_NMB", None)]:
        df = df.with_columns((c(name) & pool).fill_null(False).alias(sig))
    df = df.with_columns(c("liq").fill_null(False).alias("S_ALL"),
                         c("amount").alias("sc_all"))
    return df.sort("code", "trade_date")


def lu_hit(panel: pl.DataFrame, t: pl.DataFrame) -> dict:
    """抓涨停命中率：持有期内触板率/收盘封板率/最大浮盈（复权）。"""
    if t is None or t.height == 0:
        return {}
    p = panel.select("code", "trade_date", "touch", "luc", "ah")
    touches, seals, gains = [], [], []
    parts = {code: part for (code,), part in p.partition_by("code", as_dict=True).items()}
    for code, entry, exit_, epx in t.select("code", "entry", "exit", "entry_px").iter_rows():
        part = parts.get(code)
        if part is None or epx <= 0:
            continue
        w = part.filter((c("trade_date") >= entry) & (c("trade_date") <= exit_))
        if w.height == 0:
            continue
        touches.append(bool(w["touch"].max()))
        seals.append(bool(w["luc"].max()))
        gains.append(float(w["ah"].max()) / epx - 1.0)
    n = len(touches)
    if n == 0:
        return {}
    gs = sorted(gains)
    return {"n": n, "touch_rate": round(100 * sum(touches) / n, 1),
            "seal_rate": round(100 * sum(seals) / n, 1),
            "maxgain_med": round(100 * gs[n // 2], 2),
            "maxgain_p90": round(100 * gs[int(n * 0.9)], 2)}


if __name__ == "__main__":
    phase_arg, out = sys.argv[1], sys.argv[2]
    phases = [p for p in phase_arg.split(",") if p in PH]
    if not phases:
        raise SystemExit(f"unknown phase: {phase_arg}")
    df = prep()
    res = json.load(open(out)) if os.path.exists(out) else {}
    for ph in phases:
        s, e = PH[ph]
        configs = list(MAIN)
        if ph in E2_PHASES:
            configs += E2
        if ph in CTRL_PHASES:
            configs += CTRL
        for name, sig, sc, mode in configs:
            policy = build_policy(HOLD, mode)
            w = df.filter(c(sig) & c("trade_date").is_between(pl.lit(s), pl.lit(e)))
            res.setdefault("_info", {}).setdefault(ph, {})[name] = {
                "n_sig": w.height, "sigdays": w["trade_date"].n_unique()}
            t = run_sim(df, sig, sc, policy, s, e, k_slots=K, max_new=MAX_NEW, horizon=HOLD)
            m = metrics(t, df, K)
            if t is not None and t.height:
                t.write_csv(os.path.join(os.path.dirname(out) or ".", f"trades_{name}_{ph.split()[0]}.csv"))
                res.setdefault("_lu_hit", {}).setdefault(ph, {})[name] = lu_hit(df, t)
            res.setdefault(name, {})[ph] = m
            print(name, ph, {k: m.get(k) for k in ("n", "win", "avg", "pf", "mdd", "days", "excess")}, flush=True)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)
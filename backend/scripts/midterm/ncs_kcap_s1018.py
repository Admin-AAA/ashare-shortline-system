"""§1018 NCS_h60 并发持仓上限（k_slots）扫描 — 预注册 docs/strategy-research.md §1018（结果出来前写定，不调参）。

现役 long-net-cash-switch 生产口径滚动重放 R12 每笔 −7.59% / PF0.52（100 笔），
而上线快照（k_slots=20）是 +3.64% / PF1.47（61 笔）。差异来源：生产「不设仓位上限」。
本节用同一 realistic 引擎、同一事件与排序，只扫 k_slots，跨 2019 起 9 个窗口判死或判活。

口径：事件/排序/持有/max_new 全部与 lt_research98 的 NCS_h60 一致，只改 k_slots。
主判据用每笔口径（跨 k_slots 可比）；tot/mdd 依赖 1/k_slots 归一，另报 peak_conc 与 mdd_peak。

用法：PYTHONPATH=../..:. python ncs_kcap_s1018.py "<phase>,<phase>" out.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import polars as pl

from lt_research import fixed
from lt_research46 import PH, full_metrics
from realistic import run_real
import lt_research98 as R98

c = pl.col
HOLD, MAX_NEW = 60, 5
PH = dict(PH)
PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")

# 版本名 -> k_slots。1e6 等效「无并发上限」= 生产现状基线。
VARIANTS = {
    "NCS_uncap_h60": 1_000_000,
    "NCS_k60_h60": 60,
    "NCS_k40_h60": 40,
    "NCS_k30_h60": 30,
    "NCS_k20_h60": 20,
}
BASE = "NCS_uncap_h60"


def per_trade(t: pl.DataFrame) -> dict:
    """每笔口径统计，与生产 rolling_r12.trade_stats 同义（去尾 5%、PF、胜率）。跨 k_slots 可比。"""
    if t is None or not t.height:
        return {"n": 0, "win": None, "avg": None, "pf": None, "trim5": None, "med": None, "days": None}
    r = t["ret"].to_numpy()
    n = len(r)
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    srt = np.sort(r)
    k = int(n * 0.05)
    core = srt[k:n - k] if n - 2 * k > 0 else srt
    return {
        "n": n,
        "win": round(100 * float((r > 0).mean()), 1),
        "avg": round(100 * float(r.mean()), 2),
        "med": round(100 * float(np.median(r)), 2),
        "pf": round(float(w / l), 2) if l > 0 else (99.0 if w > 0 else None),
        "trim5": round(100 * float(core.mean()), 2),
        "days": round(float(t["days"].mean()), 1),
    }


def peak_concurrent(t: pl.DataFrame) -> tuple[int, str | None]:
    """该窗口内实际达到的最大并发持仓数（entry <= d < exit），以及达到峰值的日期。"""
    if t is None or not t.height:
        return 0, None
    ev = []
    for e, x in t.select("entry", "exit").iter_rows():
        ev.append((e, 1))
        ev.append((x, -1))
    # 同一天先平后开，与 run_real 里「先剔除已到期 held 再进新仓」一致
    ev.sort(key=lambda z: (z[0], z[1]))
    cur = best = 0
    best_d = None
    for d, delta in ev:
        cur += delta
        if cur > best:
            best, best_d = cur, d
    return best, best_d


def mdd_at(curve, k_slots: int, divisor: int) -> float | None:
    """把引擎按 1/k_slots 归一的权益曲线还原成原始 pnl，再按 divisor 重新归一算最大回撤。

    无上限版本 k_slots=1e6，引擎自身 mdd 恒约 0（除数巨大），没有意义；
    用 peak_conc 作除数才是「按峰值资金占用」的诚实回撤。公式同 metrics_real。"""
    if curve is None or divisor <= 0:
        return None
    ds, eq = curve
    if not len(ds):
        return None
    eq = np.asarray(eq, dtype=float)
    pnl = np.diff(eq, prepend=1.0) * k_slots          # 还原每日原始 pnl（单位：1 份名义）
    e2 = 1 + np.cumsum(pnl / divisor)                 # 按 divisor 份资金归一
    dd = e2 / np.maximum.accumulate(np.maximum(e2, 1)) - 1
    return round(100 * float(dd.min()), 1)


def run_variant(df, name: str, k_slots: int, phase: str) -> dict:
    s, e = PH[phase]
    t, cur = run_real(df, "NCS", "sc_ncs", fixed(HOLD), s, e,
                      k_slots=k_slots, max_new=MAX_NEW, max_hold=HOLD)
    m = full_metrics(df, t, cur)          # 引擎口径：n/win/avg/pf/days/tot/mdd + bench/excess/ex_win
    m.update(per_trade(t))                # 每笔口径覆盖同名键（trim5/med 新增）
    pk, pk_d = peak_concurrent(t)
    m["k_slots"] = k_slots
    m["peak_conc"] = pk
    m["peak_conc_date"] = pk_d
    m["mdd_peak"] = mdd_at(cur, k_slots, pk or k_slots)
    m["mdd_engine"] = m.get("mdd")        # 保留引擎原值（按自身 k_slots 归一）
    return m


if __name__ == "__main__":
    phases, out = sys.argv[1].split(","), sys.argv[2]
    df = R98.prep()
    print("NCS signals/year:",
          df.group_by(c("trade_date").str.slice(0, 4).alias("y")).agg(c("NCS").sum()).sort("y").to_dicts(),
          flush=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    for ph in phases:
        for name, k in VARIANTS.items():
            m = run_variant(df, name, k, ph)
            res.setdefault(name, {})[ph] = m
            print(f"[{ph}] {name:16s} k={k:<7} n={m['n']:<4} avg={m['avg']} pf={m['pf']} "
                  f"trim5={m['trim5']} peak_conc={m['peak_conc']} mdd_peak={m['mdd_peak']} "
                  f"excess={m.get('excess')}", flush=True)
            json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
    print("done", flush=True)

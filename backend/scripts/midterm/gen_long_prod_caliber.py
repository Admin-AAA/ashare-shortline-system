"""长线现役策略「生产口径」指标重算 — 配套 docs/strategy-research.md §1018 的处置决定。

背景：`long-net-cash-switch` / `long-growth-streak` 上线时明确「实盘推荐不设仓位上限
（k_slots/max_new 只用于回测指标）」，但 `midterm_backtest_metrics.json` 里展示给用户的
recent_12m / phases / quality 全是 k_slots=20 的研究快照 —— 页面数字描述的是一个实盘并不存在的配置。
§1018 已判定「加并发上限」不是稳健的每笔改进（REJECTED，不改实盘行为），
owner 决定先把**展示口径**对齐到实盘实际在跑的配置（无并发上限），之后再按新口径决定去留。

本脚本只读本地 parquet、只写 output/research/s1018/prod_caliber.json，不改生产代码、不写库。
产物随后由 gen_metrics 步骤写入 midterm_backtest_metrics.json，原 k20 快照保留在
`onboarding_research_k20` 字段里不丢（上线证据与审计链）。

口径：事件定义、排序分、持有期、max_new=5、liq 股池、realistic 引擎全部与上线研究脚本一致，
**只把 k_slots 从 20 改成 1e6（等效无并发上限）**，即实盘实际行为。
因为引擎权益曲线按 1/k_slots 归一，无上限口径下 tot/mdd 会被巨大除数压成 0，
所以另算 peak_conc（窗口内实际最大并发持仓）与按 peak_conc 归一的 tot_peak / mdd_peak。

用法：PYTHONPATH=../..:. python gen_long_prod_caliber.py out.json
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
import lt_research102 as R102
from ncs_kcap_s1018 import per_trade, peak_concurrent, mdd_at

c = pl.col
BASE_K = 1_000_000  # 等效「无并发上限」，与 §1018 的 NCS_uncap_h60 同一取值
PH = dict(PH)
PH["N3 2024-01~2026-10"] = ("2024-01-01", "2026-10-16")
PH["EXT 2024-01~2025-09"] = ("2024-01-01", "2025-09-30")
PHASES = ["P19 2019", "P20 2020", "P21 2021", "W0 2022-04~2023-03", "W00 2023-04~12",
          "W1 2024", "W2 2025-01~09", "R12 2025-10~2026-10", "EXT 2024-01~2025-09",
          "N3 2024-01~2026-10"]

# strategy_id -> (prep 函数, 信号列, 排序分列, 持有日, 上线研究脚本)
TARGETS = {
    "long-net-cash-switch": (R98.prep, "NCS", "sc_ncs", 60, "lt_research98.py NCS_h60"),
    "long-growth-streak": (R102.prep, "CG4", "sc_cg", 60, "lt_research102.py CG4_h60"),
}


def tot_at(curve, k_slots: int, divisor: int) -> float | None:
    """把引擎按 1/k_slots 归一的总收益还原后，按 divisor 份资金重新归一。

    引擎 eq[-1]−1 = sum(每日 pnl)/k_slots，故 sum(pnl) = (eq[-1]−1)*k_slots；
    按 divisor 份资金等权（每份 1/divisor 名义）的总收益 = 100*sum(pnl)/divisor。
    与 metrics_real 的 tot 同义（简单累加、不复利），只是除数换成峰值并发。"""
    if curve is None or divisor <= 0:
        return None
    _, eq = curve
    if not len(eq):
        return None
    total = (float(np.asarray(eq, dtype=float)[-1]) - 1.0) * k_slots
    return round(100 * total / divisor, 1)


def run(df, sig: str, score: str, hold: int, k_slots: int, phase: str) -> dict:
    s, e = PH[phase]
    t, cur = run_real(df, sig, score, fixed(hold), s, e,
                      k_slots=k_slots, max_new=5, max_hold=hold)
    m = full_metrics(df, t, cur)
    m.update(per_trade(t))
    pk, pk_d = peak_concurrent(t)
    m["k_slots"] = k_slots
    m["peak_conc"] = pk
    m["peak_conc_date"] = pk_d
    m["tot_peak"] = tot_at(cur, k_slots, pk or k_slots)
    m["mdd_peak"] = mdd_at(cur, k_slots, pk or k_slots)
    return m


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    res = json.load(open(out)) if os.path.exists(out) else {}
    for sid, (prep, sig, score, hold, src) in TARGETS.items():
        print(f"=== {sid} ({src}) ===", flush=True)
        df = prep()
        for label, k in (("prod_uncap", BASE_K), ("research_k20", 20)):
            for ph in PHASES:
                m = run(df, sig, score, hold, k, ph)
                res.setdefault(sid, {}).setdefault(label, {})[ph] = m
                print(f"  [{label:12}] {ph:24} n={m['n']:<4} avg={m['avg']:<7} pf={m['pf']:<5} "
                      f"win={m['win']:<5} med={m['med']:<7} trim5={m['trim5']:<7} "
                      f"peak_conc={m['peak_conc']:<4} tot_peak={m['tot_peak']:<7} mdd_peak={m['mdd_peak']:<7} "
                      f"days={m['days']} bench={m.get('bench')} excess={m.get('excess')} ex_win={m.get('ex_win')}",
                      flush=True)
                json.dump(res, open(out, "w"), indent=1, ensure_ascii=False)
        del df
    print("done", flush=True)

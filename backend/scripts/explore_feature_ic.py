#!/usr/bin/env python3
"""探索性特征区分度分析（纯测量，不改生产策略）。

对冻结快照 holdout_244d 的全候选卡，测量各特征与 T+N 收益的关系：
- Spearman IC（横截面，按信号日分组后取均值）→ 排序力
- 按特征三分位分层的 T+N 平均收益 → 单调性
- 时间分布（覆盖多少信号日）→ 防单日 regime 伪影（feature_signal_guard 思路）

口径：信号日 card → T+1 open 入场 → T+N close 离场，净收益 = close_T+N/open_T+1 - 1。
只统计 tradable 的 outcome bar。这是 universe/候选池口径（非精选 6 只），用于识别方向，
不作发布依据（发布需 production-policy 双臂 + purged OOS + ReleasePolicy v2）。
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict

SNAPSHOT = "/Users/wangao/Desktop/ashare-shortline-system/backend/output/frozen_snapshots/holdout_244d.json"

# 入场后第 N 根 bar（index 0 = T+1）。entry=open[0]，exit=close[N-1]
HOLD_BARS = 5

FEATURES = [
    "final_score",
    "chg20",
    "pos60",
    "pct",
    "amount_yi",
    "evidence_quality_score",
    "data_completeness_score",
    "limit_up_open_times",
    "limit_up_turnover_ratio",
    "limit_up_fd_amount_yi",
]


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 5:
        return None

    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _to_float(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def main() -> None:
    snap = json.load(open(SNAPSHOT))
    days = snap["days"]

    # 按信号日收集 (feature_value, fwd_return) 横截面
    per_day = []  # list of dict: date -> list of (feat_dict, ret)
    for day in days:
        sig = day["signal_date"]
        ob = day.get("outcome_bars") or {}
        rows = []
        for card in day.get("cards") or []:
            code = card.get("code")
            bars = ob.get(code)
            if not bars or len(bars) < HOLD_BARS + 0:
                continue
            # 需要 T+1 (index0) 可交易入场 + 第 HOLD_BARS-1 根有收盘
            entry_bar = bars[0]
            if not entry_bar.get("tradable"):
                continue
            entry = _to_float(entry_bar.get("open"))
            exit_bar = bars[min(HOLD_BARS - 1, len(bars) - 1)]
            exitp = _to_float(exit_bar.get("close"))
            if not entry or not exitp or entry <= 0:
                continue
            ret = exitp / entry - 1.0
            feats = {f: _to_float(card.get(f)) for f in FEATURES}
            rows.append((feats, ret))
        if rows:
            per_day.append((sig, rows))

    n_days = len(per_day)
    total = sum(len(r) for _, r in per_day)
    print(f"信号日: {n_days}  候选卡(可交易): {total}")
    print(f"口径: T+1 open 入场 → T+{HOLD_BARS} close 离场")
    print()

    # 每个特征：逐日 IC + 全局分层
    print(f"{'feature':<26}{'IC均值':>9}{'IC>0占比':>9}{'Q1_ret%':>9}{'Q3_ret%':>9}{'单调':>5}{'覆盖日':>7}")
    print("-" * 78)
    results = {}
    for feat in FEATURES:
        daily_ic = []
        # 全局 pooled 用于分层
        pooled = []  # (feat_val, ret)
        for sig, rows in per_day:
            xs = [f[feat] for f, _ in rows]
            ys = [r for _, r in rows]
            pairs = [(x, y) for x, y in zip(xs, ys) if x is not None]
            if len(pairs) < 5:
                continue
            daily_ic.append(_spearman([p[0] for p in pairs], [p[1] for p in pairs]))
            pooled.extend(pairs)
        daily_ic = [v for v in daily_ic if v is not None]
        if not daily_ic or not pooled:
            continue
        ic_mean = statistics.mean(daily_ic)
        ic_pos = sum(1 for v in daily_ic if v > 0) / len(daily_ic)
        # 三分位分层（pooled）
        pooled.sort(key=lambda p: p[0])
        third = len(pooled) // 3
        q1 = pooled[:third]
        q3 = pooled[-third:] if third else pooled
        q1_ret = statistics.mean([p[1] for p in q1]) * 100 if q1 else float("nan")
        q3_ret = statistics.mean([p[1] for p in q3]) * 100 if q3 else float("nan")
        mono = "升" if q3_ret > q1_ret else "降"
        results[feat] = dict(ic=ic_mean, ic_pos=ic_pos, q1=q1_ret, q3=q3_ret, days=len(daily_ic))
        print(f"{feat:<26}{ic_mean:>9.4f}{ic_pos:>9.2f}{q1_ret:>9.3f}{q3_ret:>9.3f}{mono:>5}{len(daily_ic):>7}")

    print()
    print("解读: IC均值绝对值>0.02 且 IC>0占比>0.6 或 <0.4 → 有稳定排序力;")
    print("      Q3-Q1 同号于 IC → 单调可分层; 覆盖日多 → 非单日伪影。")
    print("      这是候选池口径，仅用于选方向，不作发布依据。")


if __name__ == "__main__":
    main()

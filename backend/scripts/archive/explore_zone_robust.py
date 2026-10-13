#!/usr/bin/env python3
"""毒区稳健性检验：候选毒区是否跨时间稳定（防单日regime伪影）。

对候选毒区（chg20 5-15 / pos60>95），按月份拆分看收益是否一致为负，
并检查样本是否集中在少数几天（feature_signal_guard 思路）。
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict

SNAPSHOT = "/Users/wangao/Desktop/ashare-shortline-system/backend/output/frozen_snapshots/holdout_244d.json"
SCORE_LINE = 65.0
HOLD_BARS = 5


def _f(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _ret(code, ob):
    bars = ob.get(code)
    if not bars or not bars[0].get("tradable"):
        return None
    entry = _f(bars[0].get("open"))
    xp = _f(bars[min(HOLD_BARS - 1, len(bars) - 1)].get("close"))
    if not entry or not xp or entry <= 0:
        return None
    return xp / entry - 1.0


snap = json.load(open(SNAPSHOT))

ZONES = {
    "chg20_5_15(尴尬位)": lambda c: 5 < (_f(c.get("chg20")) or 0) <= 15,
    "pos60>95(极高位)": lambda c: (_f(c.get("pos60")) or 0) > 95,
    "chg20_0_5(最优温和)": lambda c: 0 < (_f(c.get("chg20")) or 0) <= 5,
}

for zname, pred in ZONES.items():
    by_month = defaultdict(list)
    by_day = defaultdict(int)
    total = []
    for day in snap["days"]:
        sig = day["signal_date"]
        month = sig[:7]
        ob = day.get("outcome_bars") or {}
        for c in day.get("cards") or []:
            if (c.get("final_score") or 0) < SCORE_LINE:
                continue
            if not pred(c):
                continue
            r = _ret(c.get("code"), ob)
            if r is None:
                continue
            by_month[month].append(r)
            by_day[sig] += 1
            total.append(r)
    if not total:
        continue
    wr = 100 * sum(1 for r in total if r > 0) / len(total)
    avg = 100 * statistics.mean(total)
    # 时间集中度：最大单日占比
    max_day_share = max(by_day.values()) / len(total) if total else 0
    days_active = len(by_day)
    print(f"\n=== {zname} ===  n={len(total)} 胜率={wr:.1f}% 票均={avg:.3f}% 覆盖{days_active}日 最大单日占比={max_day_share:.1%}")
    # 逐月
    neg_months = 0
    for m in sorted(by_month):
        rs = by_month[m]
        if len(rs) < 5:
            continue
        mwr = 100 * sum(1 for r in rs if r > 0) / len(rs)
        mavg = 100 * statistics.mean(rs)
        if mavg < 0:
            neg_months += 1
        print(f"  {m}: n={len(rs):>4} 胜率={mwr:>5.1f}% 票均={mavg:>7.3f}%")
    print(f"  负票均月份数: {neg_months}")

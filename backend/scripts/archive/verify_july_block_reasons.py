#!/usr/bin/env python3
"""分析 7 月 formal run 的 score_line 拦截原因（2026-08-02）。

final_score=83.54 的候选被拦（passed=False），reason/regime 字段为乱码——
本脚本从 filter_audit_json 提取 score_line 阶段 rows 的 final_score 与
market_regime 原始字节，归类拦截分布，判断 7 月零产出真实根因。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    rows = db.execute(
        text(
            "SELECT trade_date, filter_audit_json, market_snapshot_json "
            "FROM recommendation_runs WHERE run_mode='formal' AND trade_date BETWEEN '2026-07-01' AND '2026-07-31' "
            "AND status='completed' ORDER BY trade_date"
        )
    ).all()
finally:
    db.close()

per_day: dict[str, list[tuple[float, str, str]]] = {}
for trade_date, faudit, msnap in rows:
    try:
        d = json.loads(faudit or "{}")
        sl_rows = [
            r for r in d.get("rows", [])
            if isinstance(r, dict) and r.get("final_stage") == "score_line"
        ]
        snaps = json.loads(msnap or "{}")
        regime = (snaps.get("breadth") or {}).get("regime") or "?"
    except Exception:  # noqa: BLE001
        continue
    day = []
    for r in sl_rows:
        slc = r.get("score_line") or {}
        fs = slc.get("final_score")
        mregime = slc.get("market_regime") or "?"
        reason = slc.get("reason") or r.get("final_reason") or "?"
        if isinstance(fs, (int, float)):
            day.append((fs, str(mregime), str(reason)))
    per_day.setdefault(trade_date, []).extend(day)

print(f"{'日期':<11}{'拦数':<5}{'最高分':<8}{'regime(快照)':<10}{'拦截行 market_regime 样例'}")
for td in sorted(per_day):
    day = per_day[td]
    if not day:
        continue
    top = max(x[0] for x in day)
    regimes = Counter(x[1] for x in day)
    print(f"{td:<11}{len(day):<5}{top:<8.2f}{str(regimes.most_common(1)[0][0])[:9]:<10}{repr(regimes.most_common(1)[0][0])[:30]}")

# 拦截原因分布（全月）
reasons = Counter()
for day in per_day.values():
    for _, _, reason in day:
        reasons[reason] += 1
print("\n全月拦截 reason 分布（原始字节 repr）:")
for reason, n in reasons.most_common(10):
    print(f"  {n:>4}  {repr(reason)[:80]}")

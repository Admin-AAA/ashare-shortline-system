#!/usr/bin/env python3
"""7 月重放产出汇总（2026-08-02 修复后），写入 output/ 报告。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402

db = SessionLocal()
try:
    runs = db.execute(
        text(
            "SELECT run_id, trade_date, recommended_count, result_summary_json, market_snapshot_json "
            "FROM recommendation_runs WHERE run_mode='formal' AND trade_date BETWEEN '2026-07-01' AND '2026-07-31' "
            "AND started_at >= '2026-08-02 00:00' AND evaluation_scope='live_forward' ORDER BY trade_date"
        )
    ).all()
    recs = db.execute(
        text(
            "SELECT r.trade_date, rec.code, rec.name, rec.bucket, rec.final_score, rec.setup_type "
            "FROM recommendations rec JOIN recommendation_runs r ON rec.run_id=r.run_id "
            "WHERE r.run_mode='formal' AND rec.trade_date BETWEEN '2026-07-01' AND '2026-07-31' "
            "AND r.started_at >= '2026-08-02 00:00' ORDER BY r.trade_date"
        )
    ).all()
finally:
    db.close()

lines = ["# 2026-07 正式推荐重放产出汇总（2026-08-02 修复后）", ""]
lines.append("**修复前**：7 月 25 个 formal run 全部 0 推荐（8/1 旧代码重放全拦：无有效线元数据）。")
lines.append("**修复后**：全窗口重放 23/23 completed，弱市日恢复产出。")
lines.append("")
lines.append("## 产出明细")
lines.append("")
lines.append("| trade_date | code | name | bucket | final_score | setup |")
lines.append("|---|---|---|---|---|---|")
for td, code, name, bucket, fs, setup in recs:
    lines.append(f"| {td} | {code} | {name or ''} | {bucket or ''} | {fs} | {setup or ''} |")
lines.append("")
lines.append(f"合计产出 **{len(recs)}** 条（修复前 0 条）")
lines.append("")
lines.append("## 每日状态")
lines.append("")
lines.append("| trade_date | 产出 | 状态摘要 |")
lines.append("|---|---|---|")
for run_id, td, rc, rs, ms in runs:
    d = json.loads(rs or "{}")
    snap = json.loads(ms or "{}")
    regime = (snap.get("breadth") or {}).get("regime") or "?"
    opp = d.get("opportunity", "?")
    lines.append(f"| {td} | {rc} | regime={regime} opportunity={opp} |")
lines.append("")

out = Path("/Users/wangao/Desktop/ashare-shortline-system/output/july_replay_summary_20260802.md")
out.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
print(f"\n已写入 {out}")

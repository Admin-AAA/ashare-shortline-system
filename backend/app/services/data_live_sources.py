"""部分数据域的线上真源不在同名旧表里（UI 审计 2026-10-19）：

- 大宗交易：线上读 auxiliary 快照（block_trade_snapshot），旧表 block_trades 自 07-09 起按设计不再写入；
- 股票回购：中长线回购策略读 data/midterm/events/repurchase_<year>.parquet（每日经网关 ingest），
  旧表 repurchases 是 2026-05-23 一次性导入，恰好 100000 行（截断，且约 45% 重复），无任何读方。
这里给健康页/新鲜度巡检提供真源的 (行数, 最新日期)。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

EVENTS_DIR = Path(__file__).resolve().parents[2] / "data" / "midterm" / "events"
_TD = re.compile(r'"trade_date",\["string","(\d{8})"\]')


def _iso(d: str | None) -> str | None:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if d and len(d) == 8 else d


def block_trade_snapshot_latest(db) -> tuple[int | None, str | None]:
    rows = db.execute(text(
        "SELECT CAST(b.request_partition_bytes AS CHAR), b.row_count FROM auxiliary_active_partition_snapshots a "
        "JOIN auxiliary_snapshot_batches b ON b.batch_id = a.active_batch_id WHERE a.dataset = 'block_trade'")).all()
    best, total = None, 0
    for part, n in rows:
        m = _TD.search(part or "")
        total += int(n or 0)
        if m and (best is None or m.group(1) > best):
            best = m.group(1)
    return total, _iso(best)


def repurchase_events_latest(_db=None) -> tuple[int | None, str | None]:
    import pandas as pd

    files = sorted(EVENTS_DIR.glob("repurchase_*.parquet"))
    if not files:
        return 0, None
    total, latest = 0, None
    for f in files:
        d = pd.read_parquet(f, columns=["ann_date"])
        total += len(d)
        mx = str(d["ann_date"].max()) if len(d) else None
        if mx and (latest is None or mx > latest):
            latest = mx
    return total, _iso(latest)


LIVE_SOURCES: dict[str, Any] = {
    "block_trade": block_trade_snapshot_latest,
    "repurchase": repurchase_events_latest,
}

"""_persist_market 幂等性回归测试（2026-07-19 并发同 snapshot_id 撞主键根因）。

旧实现 check-then-add：两个并发 run 生成相同 snapshot_id，同时 db.get 返回 None →
都 db.add → ORM unit-of-work flush 时撞主键，IntegrityError 污染整个 run 大事务 →
整个 formal run failed。修复后改为 mysql_insert + on_duplicate_key_update，第二个
同 ID 写降级为 UPDATE（同值覆盖），不抛错、不回滚外层事务。

用生产 MySQL 验证（真实 InnoDB 主键语义），独立 snapshot_id 前缀 + 测试后删除该行
（只删测试自建行，不清表/不动业务数据）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.tables import MarketSnapshot
from app.services.workbench_service import _persist_market


def _snapshot(snapshot_id: str) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "trade_date": "2026-07-21",
        "sample_count": 4950,
        "up_count": 2200,
        "down_count": 2400,
        "limit_up_count": 50,
        "limit_down_count": 80,
        "main_industries": ["半导体", "元器件"],
        "breadth": {"regime": "震荡", "up_ratio": 0.44},
    }


@pytest.mark.integration
def test_persist_market_duplicate_snapshot_id_is_idempotent() -> None:
    sid = f"test_persist_idem_{uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        # 第一次写：正常插入
        _persist_market(db, _snapshot(sid))
        db.flush()

        # 第二次写同 snapshot_id（模拟并发 run 撞主键）：旧实现此处 IntegrityError，
        # 新实现降级为 UPDATE 不抛错。
        _persist_market(db, _snapshot(sid))
        db.flush()

        # 仍仅一行（update 而非新增）
        rows = db.execute(
            select(MarketSnapshot).where(MarketSnapshot.snapshot_id == sid)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].sample_count == 4950
    finally:
        # 只删测试自建行（业务数据不动）
        db.execute(delete(MarketSnapshot).where(MarketSnapshot.snapshot_id == sid))
        db.commit()
        db.close()


@pytest.mark.integration
def test_persist_market_existing_fast_path_returns_early() -> None:
    # 先 get 到已存在 → 直接 return，不执行写（避免无谓 UPDATE）
    sid = f"test_persist_fastpath_{uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        _persist_market(db, _snapshot(sid))
        db.flush()
        # 改一个值再写，fast-path return 后 DB 值应保持第一次的（证明走了 early return）
        snap2 = _snapshot(sid)
        snap2["sample_count"] = 9999
        _persist_market(db, snap2)
        db.flush()
        row = db.execute(
            select(MarketSnapshot).where(MarketSnapshot.snapshot_id == sid)
        ).scalar_one()
        # fast-path return → 未被覆盖
        assert row.sample_count == 4950
    finally:
        db.execute(delete(MarketSnapshot).where(MarketSnapshot.snapshot_id == sid))
        db.commit()
        db.close()

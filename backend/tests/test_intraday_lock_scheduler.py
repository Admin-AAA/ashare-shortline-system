"""盘中锁利影子链路调度接线的 L1 单元测试。

聚焦 scheduler_service 侧（不动真实 Tushare/生产库）：
- _run_intraday_lock_shadow 的 action 分发、create 默认 trade_date 时机、未知 action 报错
- _ensure_intraday_lock_tasks 幂等注册两个表驱动任务

service 三个入口的触发/幂等逻辑由 test_intraday_lock_service.py 覆盖，这里用 mock 替换。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tables import Base, ScheduledTask
from app.services import scheduler_service as sched


# ── _run_intraday_lock_shadow 分发与时机 ─────────────────────────────────────

def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[ScheduledTask.__table__])
    return sessionmaker(bind=engine)()


def test_run_create_uses_previous_weekday_default(monkeypatch):
    captured = {}

    def fake_create(db, trade_date, target_pcts=None, lock_ratio=None):
        captured["trade_date"] = trade_date
        return 5

    monkeypatch.setattr(
        "app.services.intraday_lock_service.create_monitors_for_trade_date", fake_create
    )
    # create 不应触碰 get_pro（盘前建监控不需行情）
    monkeypatch.setattr(
        "app.services.tushare_client.get_pro",
        lambda: (_ for _ in ()).throw(AssertionError("create 不应调用 get_pro")),
    )
    db = _session()
    result = sched._run_intraday_lock_shadow(db, {"action": "create"})
    # 默认 trade_date = 前一工作日（推荐日）；昨天 T+1 入场、今天 T+2 可卖
    assert captured["trade_date"] == sched.previous_weekday(datetime.now().date()).isoformat()
    assert result["action"] == "create"
    assert result["created"] == 5


def test_run_create_explicit_trade_date(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.intraday_lock_service.create_monitors_for_trade_date",
        # 必须返回 int（真实实现返回新建数）；setdefault 返回日期字符串会让 %d 日志格式化抛 TypeError
        lambda db, trade_date, **kw: captured.update(trade_date=trade_date) or 0,
    )
    db = _session()
    sched._run_intraday_lock_shadow(db, {"action": "create", "trade_date": "2026-07-28"})
    assert captured["trade_date"] == "2026-07-28"


def test_run_settle_calls_service_with_pro(monkeypatch):
    sentinel_pro = object()
    captured = {}
    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: sentinel_pro)

    def fake_settle(db, pro):
        captured["pro"] = pro
        return {"settled": 2, "skipped": 1, "errors": 0}

    monkeypatch.setattr("app.services.intraday_lock_service.settle_signals", fake_settle)
    db = _session()
    result = sched._run_intraday_lock_shadow(db, {"action": "settle"})
    assert captured["pro"] is sentinel_pro
    assert result["action"] == "settle"
    assert result["settled"] == 2


def test_run_unknown_action_raises():
    db = _session()
    with pytest.raises(RuntimeError, match="未知盘中锁利 action"):
        sched._run_intraday_lock_shadow(db, {"action": "bogus"})


# ── _ensure_intraday_lock_tasks 幂等注册 ────────────────────────────────────

def test_ensure_intraday_lock_tasks_idempotent(monkeypatch):
    session = _session()
    monkeypatch.setattr(sched, "SessionLocal", lambda: session)
    sched._ensure_intraday_lock_tasks()
    sched._ensure_intraday_lock_tasks()  # 第二次不应重复插入
    rows = session.query(ScheduledTask).filter_by(
        task_type=sched.TASK_TYPE_INTRADAY_LOCK_SHADOW
    ).all()
    task_ids = {r.task_id for r in rows}
    assert task_ids == {sched.LOCK_CREATE_TASK_ID, sched.LOCK_SETTLE_TASK_ID}
    assert len(rows) == 2  # 幂等：仍只有 2 条
    for r in rows:
        assert r.enabled is True

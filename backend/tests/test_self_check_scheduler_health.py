"""T12 调度健康诊断测试（check_scheduler_health）。

锁定盘后流水线调度健康可观测性：post_sync_formal_recommendation 的 last_status=failed
或超过 2 天未跑时，self_check 应给出告警（outcome 结算 + formal 推荐未执行 → D 项阻塞）。

背景（2026-07-23 调研）：07-22 推荐未结算（outcome=None），因为盘后流水线
last_status=failed（07-21 10:20），07-22 17:35 没跑。流水线含 outcome 结算步骤 →
不跑则前瞻样本无法积累（D 项阻塞）。
"""
import pathlib
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]

_DDL = """
CREATE TABLE scheduled_tasks (
    task_id TEXT PRIMARY KEY,
    task_type TEXT,
    cron_expression TEXT,
    enabled INTEGER,
    last_run_at TEXT,
    last_status TEXT,
    last_error TEXT
)
"""


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(_DDL))
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


def _insert(db, *, task_id, task_type, last_run_at, last_status, last_error=None, enabled=1):
    db.execute(
        text(
            "INSERT INTO scheduled_tasks"
            " (task_id, task_type, cron_expression, enabled, last_run_at, last_status, last_error)"
            " VALUES (:task_id, :task_type, '35 17 * * 1-5', :enabled, :last_run_at, :last_status, :last_error)"
        ),
        {
            "task_id": task_id,
            "task_type": task_type,
            "enabled": enabled,
            "last_run_at": last_run_at,
            "last_status": last_status,
            "last_error": last_error,
        },
    )
    db.commit()


def test_scheduler_health_empty_table_returns_no_warnings(db):
    assert self_check.check_scheduler_health(db) == []


def test_scheduler_health_detects_failed_postclose(db):
    _insert(db, task_id="post_sync_formal_recommendation", task_type="recommendation_run",
            last_run_at=None, last_status="failed")
    warnings = self_check.check_scheduler_health(db)
    assert "post_sync_formal_recommendation: 上次失败" in warnings
    assert any("outcome 结算" in w for w in warnings)


def test_scheduler_health_detects_stale_postclose(db):
    old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    _insert(db, task_id="post_sync_formal_recommendation", task_type="recommendation_run",
            last_run_at=old, last_status="completed")
    warnings = self_check.check_scheduler_health(db)
    assert "post_sync_formal_recommendation: 3天未跑" in warnings


def test_scheduler_health_ignores_recent_completed_run(db):
    recent = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    _insert(db, task_id="post_sync_formal_recommendation", task_type="recommendation_run",
            last_run_at=recent, last_status="completed")
    assert self_check.check_scheduler_health(db) == []


def test_scheduler_health_ignores_disabled_task(db):
    old = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    _insert(db, task_id="post_sync_formal_recommendation", task_type="recommendation_run",
            last_run_at=old, last_status="failed", enabled=0)
    assert self_check.check_scheduler_health(db) == []

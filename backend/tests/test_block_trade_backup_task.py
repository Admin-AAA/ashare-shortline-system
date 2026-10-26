"""08:03 盘前大宗交易备份任务（替代已退役的 strategy_exposure_run 宿主）。"""

from sqlalchemy import delete

from app.models.tables import ScheduledTask
from app.services import scheduler_service as ss


def test_backup_task_registered_and_legacy_exposure_disabled(monkeypatch, db):
    monkeypatch.setattr(ss, "SessionLocal", lambda: db)
    db.execute(delete(ScheduledTask).where(ScheduledTask.task_id.in_([ss.BLOCK_TRADE_BACKUP_TASK_ID, ss.EXPOSURE_TASK_ID])))
    db.add(ScheduledTask(task_id=ss.EXPOSURE_TASK_ID, name="legacy", task_type=ss.TASK_TYPE_STRATEGY_EXPOSURE_RUN,
                         cron_expression="3 8 * * mon-fri", payload_json="{}", enabled=True))
    db.commit()

    ss._ensure_block_trade_backup_task()
    ss._ensure_block_trade_backup_task()

    rows = db.query(ScheduledTask).filter(ScheduledTask.task_id == ss.BLOCK_TRADE_BACKUP_TASK_ID).all()
    assert len(rows) == 1
    assert rows[0].task_type == ss.TASK_TYPE_BLOCK_TRADE_BACKUP
    assert rows[0].cron_expression == "3 8 * * mon-fri" and rows[0].enabled is True
    assert db.get(ScheduledTask, ss.EXPOSURE_TASK_ID).enabled is False
    assert ss.BLOCK_TRADE_BACKUP_TASK_ID in ss.BLOCK_TRADE_HOOK_TASK_IDS
    assert ss.EXPOSURE_TASK_ID not in ss.BLOCK_TRADE_HOOK_TASK_IDS


def test_backup_hook_only_targets_block_trade_strategy(monkeypatch, tmp_path):
    import app.services.midterm_service as ms

    monkeypatch.setattr(ms, "OUT_DIR", tmp_path / "picks")

    monkeypatch.setattr(ss, "_latest_allowed_bar_date", lambda: "2026-10-16")
    out = {}
    monkeypatch.setattr(ms, "_block_trade_picks", lambda db, sid, meta, t, *a, **k: out.setdefault(sid, t) and 0)
    res = ms.generate_block_trade_picks(None, "2026-10-16")
    assert list(res) == ["mid-block-trade-premium"]

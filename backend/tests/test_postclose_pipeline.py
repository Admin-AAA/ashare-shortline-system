from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from app.db.session import SessionLocal
from app.models.tables import RecommendationRun, ScheduledTask
from app.services import scheduler_service
from app.services.auxiliary.state_adapters import TushareStateProvider
from app.services.data_health_service import recommendation_readiness_from_diagnosis
from app.services.data_gap_remediation_service import _refresh_auxiliary_dataset
from app.services.registry import data_source as data_source_registry


def test_concept_sector_nan_member_count_is_normalized(db):
    client = SimpleNamespace(
        ths_index=lambda: pd.DataFrame(
            [{"ts_code": "885001.TI", "name": "测试概念", "count": float("nan")}]
        )
    )

    rows = TushareStateProvider(client, db).concept_sector(
        SimpleNamespace(trade_date=__import__("datetime").date(2026, 7, 16))
    )

    assert rows[0]["member_count"] == 0


def test_default_is_local_first_but_explicit_auto_uses_local_plus_realtime_intraday(monkeypatch):
    class Intraday(__import__("datetime").datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 17, 14, 30)

    monkeypatch.setattr(data_source_registry, "datetime", Intraday)

    assert data_source_registry.DEFAULT_RECOMMENDATION_DATA_SOURCE == "local_pool_preferred"
    assert data_source_registry.normalize_data_source(None) == "local_pool_preferred"
    assert data_source_registry.normalize_data_source("auto") == "local_pool_realtime"


def test_auxiliary_sync_uses_closed_trade_date_and_commits(monkeypatch):
    seen = {}

    class FakeDb:
        committed = False

        def commit(self):
            self.committed = True

        def close(self):
            pass

    fake_db = FakeDb()
    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.remediate_data_health_gaps",
        lambda _db, **_kwargs: {"status": "completed", "actions": [], "action_summary": {}},
    )

    def fake_run(_db, *, instruments, trade_date, refresh, datasets):
        seen.update(
            instruments=tuple(instruments),
            trade_date=trade_date,
            refresh=refresh,
            datasets=tuple(datasets),
        )
        return SimpleNamespace(orchestrated=SimpleNamespace(results=()))

    monkeypatch.setattr("app.services.auxiliary.application.run_auxiliary_application", fake_run)
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-07-16")
    # 窗口回填与 stk_limit 回填是独立阶段，此测试聚焦「参考日补数 + commit」契约，
    # 将其置空避免 FakeDb 不支持 db.scalars。
    monkeypatch.setattr(scheduler_service, "_backfill_auxiliary_window_gaps", lambda *_a, **_kw: {"backfilled_dates": []})
    monkeypatch.setattr(scheduler_service, "_backfill_stk_limit_window", lambda *_a, **_kw: {"backfilled_dates": []})

    result = scheduler_service._run_auxiliary_remediation({})

    assert seen["trade_date"] == "2026-07-16"
    assert fake_db.committed is True
    assert result["trade_date"] == "2026-07-16"


def test_postclose_pipeline_orders_sync_health_gate_and_recommendation(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-07-16")
    monkeypatch.setattr(
        scheduler_service,
        "_run_data_sync",
        lambda _db, payload: calls.append(("sync", payload["sync_scope"])) or {"status": "completed"},
    )
    monkeypatch.setattr(
        scheduler_service,
        "_diagnose_postclose_readiness",
        lambda _db, trade_date: calls.append(("health", trade_date))
        or {"can_run_local_recommendation": True, "status": "ready"},
    )
    monkeypatch.setattr(
        scheduler_service,
        "_run_outcome_label",
        lambda _db, payload: calls.append(("outcome", payload["horizon_days"])) or {"labeled": 0},
    )

    async def fake_recommendation(_db, payload):
        calls.append(
            (
                "recommendation",
                payload["data_source"],
                payload["skip_aux_refresh"],
            )
        )
        return {"related_run_id": "run_postclose", "queued": True}

    async def fake_wait(_db, run_id, **_kwargs):
        calls.append(("wait", run_id))
        return {"status": "completed", "recommended_count": 3}

    monkeypatch.setattr(scheduler_service, "_run_recommendation", fake_recommendation)
    monkeypatch.setattr(scheduler_service, "_wait_for_recommendation_run", fake_wait)

    result = asyncio.run(
        scheduler_service._run_postclose_pipeline(
            object(),
            {
                "preset_id": "preset_formal_opening",
                "data_source": "local_pool_preferred",
                "skip_aux_refresh": False,
            },
        )
    )

    assert calls == [
        ("sync", "all"),
        ("health", "2026-07-16"),
        ("outcome", 5),
        ("recommendation", "local_pool", True),
        ("wait", "run_postclose"),
    ]
    assert result["status"] == "completed"
    assert result["recommended_count"] == 3


def test_wait_for_recommendation_run_observes_worker_commit(production_mysql):
    """轮询必须越过 MySQL REPEATABLE-READ 快照看到独立 worker 的提交。"""
    run_id = f"__test_wait_refresh_{uuid.uuid4().hex[:12]}"
    setup_db = SessionLocal()
    waiting_db = SessionLocal()
    worker_db = SessionLocal()
    try:
        setup_db.add(
            RecommendationRun(
                run_id=run_id,
                run_name="__test_wait_refresh__",
                trade_date="2026-07-17",
                status="queued",
                recommended_count=0,
            )
        )
        setup_db.commit()

        # 先读一次以建立 REPEATABLE-READ 快照，再模拟独立 worker 提交终态。
        assert waiting_db.get(RecommendationRun, run_id).status == "queued"
        worker_run = worker_db.get(RecommendationRun, run_id)
        worker_run.status = "completed"
        worker_run.recommended_count = 2
        worker_run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        worker_db.commit()

        result = asyncio.run(
            scheduler_service._wait_for_recommendation_run(
                waiting_db,
                run_id,
                timeout_seconds=1,
                poll_seconds=0.05,
            )
        )

        assert result["status"] == "completed"
        assert result["recommended_count"] == 2
    finally:
        setup_db.close()
        waiting_db.close()
        worker_db.close()
        cleanup_db = SessionLocal()
        try:
            row = cleanup_db.get(RecommendationRun, run_id)
            if row is not None:
                cleanup_db.delete(row)
                cleanup_db.commit()
        finally:
            cleanup_db.close()


def test_postclose_task_configuration_disables_racing_legacy_crons(db):
    for task_id in scheduler_service.LEGACY_POSTCLOSE_TASK_IDS:
        task = db.get(ScheduledTask, task_id)
        if task is None:
            task = ScheduledTask(
                task_id=task_id,
                name=task_id,
                task_type="data_sync",
                cron_expression="0 18 * * 1-5",
                payload_json="{}",
                enabled=True,
            )
            db.add(task)
        else:
            task.enabled = True
    db.commit()

    scheduler_service._configure_postclose_pipeline_tasks(db)

    pipeline = db.get(ScheduledTask, scheduler_service.POSTCLOSE_PIPELINE_TASK_ID)
    assert pipeline is not None
    assert pipeline.enabled is True
    assert pipeline.task_type == "recommendation_run"
    assert pipeline.cron_expression == scheduler_service.POSTCLOSE_PIPELINE_CRON
    assert __import__("json").loads(pipeline.payload_json)["pre_run_full_sync"] is True
    assert all(db.get(ScheduledTask, task_id).enabled is False for task_id in scheduler_service.LEGACY_POSTCLOSE_TASK_IDS)


def test_postclose_readiness_blocks_when_required_table_misses_closed_session():
    diagnosis = {
        "reference_trade_date": "2026-07-16",
        "tables": {
            "daily_kbar": {"label": "日K线", "max_date": "2026-07-16"},
            "stock_quote": {"label": "实时快照", "max_date": "2026-07-16"},
            "index_daily": {"label": "指数日线", "max_date": "2026-07-16"},
            "limit_up_board": {"label": "涨停板", "max_date": "2026-07-15"},
        },
        "recent_trade_day_coverage": {"tables": {}},
        "issues": [],
    }

    readiness = recommendation_readiness_from_diagnosis(diagnosis)

    assert readiness["can_run_local_recommendation"] is False
    assert readiness["status"] == "blocked"
    assert any("涨停板" in blocker for blocker in readiness["blockers"])


def test_auxiliary_gap_refresh_commits_successful_write(monkeypatch):
    class FakeDb:
        committed = False

        def commit(self):
            self.committed = True

    fake_db = FakeDb()
    item = SimpleNamespace(errors=(), coverage=SimpleNamespace(record_count=100))
    application_result = SimpleNamespace(
        orchestrated=SimpleNamespace(result_for=lambda _dataset: item),
        audit_payload={"limit_up_board": {"record_count": 100}},
    )
    monkeypatch.setattr(
        "app.services.data_gap_remediation_service.run_auxiliary_application",
        lambda *_args, **_kwargs: application_result,
    )

    count, _audit = _refresh_auxiliary_dataset(
        fake_db,
        dataset="limit_up_board",
        trade_date="2026-07-16",
    )

    assert count == 100
    assert fake_db.committed is True

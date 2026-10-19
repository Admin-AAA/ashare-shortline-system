from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.db.session import SessionLocal
from app.models.tables import RecommendationRun, ScheduledTask
from app.services import scheduler_service
from app.services.auxiliary.state_adapters import TushareStateProvider
from app.services.data_health_service import recommendation_readiness_from_diagnosis
from app.services.data_gap_remediation_service import _refresh_auxiliary_dataset
from app.services.registry import data_source as data_source_registry



@pytest.fixture(autouse=True)
def _stub_ma250_fill(monkeypatch):
    import app.services.ma250_fill as ma250_fill

    monkeypatch.setattr(ma250_fill, "fill_ma250", lambda _db, **_kw: {"rows_changed": 0})
    monkeypatch.setattr(ma250_fill, "fill_moving_averages", lambda _db, **_kw: {"rows_changed": 0})
    import app.services.ivol_fill as ivol_fill

    monkeypatch.setattr(ivol_fill, "fill_ivol_incremental", lambda _db, _d, **_kw: {"stub": True})
    import app.services.local_indicator_fill as local_indicator_fill

    monkeypatch.setattr(local_indicator_fill, "fill_local_indicators", lambda _db, **_kw: {"stub": True})
    import app.services.daily_basic_ingest as daily_basic_ingest

    monkeypatch.setattr(daily_basic_ingest, "ingest_daily_basic_with_alert", lambda _db, _d: {"stub": True})


@pytest.fixture(autouse=True)
def _no_kbar_cache_refresh(monkeypatch):
    """盘后流程会增量刷新日K Parquet 缓存（6f8ac43）；这里用的是桩 db，关掉缓存刷新免得调到真实查询。"""
    monkeypatch.setenv("ASHARE_KBAR_CACHE", "0")


@pytest.fixture(autouse=True)
def _single_standalone_strategy(monkeypatch):
    """单策略独立运行后，盘后/安全网对每个策略各派发一个 run；这里固定为 1 个策略，
    保持原有「派发 → 等待」时序断言。"""
    monkeypatch.setattr(
        scheduler_service,
        "_standalone_run_payloads",
        lambda _db, payload: [{**payload, "single_skill_id": "s1", "run_mode": "formal"}],
    )




@pytest.fixture(autouse=True)
def _stub_db_side_steps(monkeypatch):
    """盘后流水线里的 RSI/MACD 补缺与中线选股直接查库；这些单测传入的是假 db，统一桩掉。"""
    import app.services.midterm_service as midterm_service
    import app.services.tdx_indicator_fill as tdx_indicator_fill

    monkeypatch.setattr(tdx_indicator_fill, "fill_recent_indicator_gaps", lambda _db, _trade_date: {"filled": 0})
    monkeypatch.setattr(midterm_service, "generate_picks", lambda _db, _trade_date: {})

def test_concept_sector_nan_member_count_is_normalized(db):
    client = SimpleNamespace(
        ths_index=lambda: pd.DataFrame(
            [
                {
                    "ts_code": "885001.TI",
                    "name": "测试概念",
                    "count": float("nan"),
                    "type": "N",
                }
            ]
        )
    )

    rows = TushareStateProvider(client, db).concept_sector(
        SimpleNamespace(trade_date=__import__("datetime").date(2026, 7, 16))
    )

    assert rows[0]["member_count"] == 0


def test_default_is_local_history_plus_realtime_snapshot(monkeypatch):
    class Intraday(__import__("datetime").datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 17, 14, 30)

    monkeypatch.setattr(data_source_registry, "datetime", Intraday)

    # 默认：历史日K/指标走本地池，当日行情拼实时快照
    assert data_source_registry.DEFAULT_RECOMMENDATION_DATA_SOURCE == "local_pool_realtime"
    assert data_source_registry.normalize_data_source(None) == "local_pool_realtime"
    assert data_source_registry.normalize_data_source("auto") == "local_pool_realtime"
    # 显式选择仍保留旧的纯本地优先模式
    assert data_source_registry.normalize_data_source("local_pool_preferred") == "local_pool_preferred"


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


def test_auxiliary_trade_date_never_exceeds_latest_closed_session():
    assert scheduler_service._bounded_auxiliary_trade_date(None, "2026-09-21") == "2026-09-21"
    assert scheduler_service._bounded_auxiliary_trade_date("20260918", "2026-09-21") == "2026-09-18"
    assert scheduler_service._bounded_auxiliary_trade_date("2026-09-22", "2026-09-21") == "2026-09-21"


def test_postclose_pipeline_orders_sync_health_gate_and_recommendation(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-07-16")
    import app.services.midterm_service as midterm_service
    import app.services.tdx_indicator_fill as tdx_indicator_fill

    monkeypatch.setattr(
        tdx_indicator_fill,
        "fill_recent_indicator_gaps",
        lambda _db, trade_date: calls.append(("indicator_fill", trade_date)) or {"filled": 0},
    )
    monkeypatch.setattr(
        midterm_service,
        "generate_picks",
        lambda _db, trade_date: calls.append(("midterm_picks", trade_date)) or {},
    )
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
        ("indicator_fill", "2026-07-16"),
        ("midterm_picks", "2026-07-16"),
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


def test_daily_sync_payloads_cover_delayed_domains_and_register_late_repair(db):
    scheduler_service._configure_postclose_pipeline_tasks(db)

    postclose = db.get(ScheduledTask, scheduler_service.POSTCLOSE_PIPELINE_TASK_ID)
    postclose_payload = __import__("json").loads(postclose.payload_json)
    late_repair = db.get(ScheduledTask, scheduler_service.POSTCLOSE_LATE_DATA_REPAIR_TASK_ID)
    late_payload = __import__("json").loads(late_repair.payload_json)

    assert "chip" in postclose_payload["health_remediation_tables"]
    assert late_repair.enabled is True
    assert late_repair.cron_expression == scheduler_service.POSTCLOSE_LATE_DATA_REPAIR_CRON
    assert late_payload["sync_scope"] == "all"
    assert "chip" in late_payload["health_remediation_tables"]


def test_postclose_readiness_blocks_when_required_table_misses_closed_session():
    diagnosis = {
        "reference_trade_date": "2026-07-16",
        "tables": {
            "daily_kbar": {"label": "日K线", "max_date": "2026-07-16"},
            "stock_quote": {"label": "实时快照", "max_date": "2026-07-16"},
            "index_daily": {"label": "指数日线", "max_date": "2026-07-16"},
            "limit_up_board": {"label": "涨停板", "max_date": "2026-07-15"},
        },
        "recent_trade_day_coverage": {
            "tables": {
                "limit_up_board": {
                    "label": "涨停板",
                    "effective_missing_dates": ["2026-07-16"],
                    "effective_incomplete_dates": [],
                    "effective_gap_count": 1,
                }
            }
        },
        "issues": [],
    }

    readiness = recommendation_readiness_from_diagnosis(diagnosis)

    assert readiness["can_run_local_recommendation"] is False
    assert readiness["status"] == "blocked"
    assert any("涨停板" in blocker for blocker in readiness["blockers"])
    detail = next(item for item in readiness["blocker_details"] if item.get("table") == "limit_up_board")
    assert detail["code"] == "table_not_current"
    assert detail["expected_trade_date"] == "2026-07-16"


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


def _recoverable_readiness(trade_date: str = "2026-07-16") -> dict:
    current_message = f"涨停板未覆盖 {trade_date}（最新 2026-07-15）"
    gap_message = "涨停板近期仍有缺口"
    return {
        "can_run_local_recommendation": False,
        "status": "blocked",
        "blockers": [current_message, gap_message],
        "blocker_details": [
            {
                "code": "table_not_current",
                "message": current_message,
                "table": "limit_up_board",
                "expected_trade_date": trade_date,
                "latest_trade_date": "2026-07-15",
            },
            {
                "code": "recent_coverage_gap",
                "message": gap_message,
                "table": "limit_up_board",
                "expected_trade_date": trade_date,
                "missing_dates": [trade_date],
                "incomplete_dates": [],
                "gap_count": 1,
            },
        ],
    }


def _patch_postclose_success_path(monkeypatch, *, health):
    monkeypatch.setattr(scheduler_service, "_latest_allowed_bar_date", lambda: "2026-07-16")
    monkeypatch.setattr(
        scheduler_service,
        "_run_data_sync",
        lambda _db, _payload: {"status": "completed"},
    )
    monkeypatch.setattr(scheduler_service, "_diagnose_postclose_readiness", health)
    monkeypatch.setattr(scheduler_service, "_run_outcome_label", lambda _db, _payload: {"labeled": 0})

    async def fake_recommendation(_db, _payload):
        return {"related_run_id": "run_postclose", "queued": True}

    async def fake_wait(_db, _run_id, **_kwargs):
        return {"status": "completed", "recommended_count": 2}

    monkeypatch.setattr(scheduler_service, "_run_recommendation", fake_recommendation)
    monkeypatch.setattr(scheduler_service, "_wait_for_recommendation_run", fake_wait)


def test_postclose_refreshes_recoverable_aux_gap_then_continues(monkeypatch):
    diagnoses = [_recoverable_readiness(), {"can_run_local_recommendation": True, "status": "ready"}]
    _patch_postclose_success_path(monkeypatch, health=lambda _db, _td: diagnoses.pop(0))
    refreshes = []
    monkeypatch.setattr(
        scheduler_service,
        "_refresh_recoverable_postclose_aux",
        lambda _db, *, trade_date, tables: refreshes.append((trade_date, tables))
        or {"limit_up_board": {"status": "completed", "record_count": 10}},
    )

    result = asyncio.run(scheduler_service._run_postclose_pipeline(object(), {}))

    assert result["status"] == "completed"
    assert result["recommended_count"] == 2
    assert refreshes == [("2026-07-16", ["limit_up_board"])]
    # 纯观测：流水线阶段计时写入结果，供 17:35 profile。
    timings = result["pipeline_timings_seconds"]
    for stage in ("sync", "readiness", "aux_refresh", "readiness_recheck", "outcome", "kbar_cache_refresh", "recommendation", "total"):
        assert timings[stage] >= 0, stage


def test_postclose_propagates_persistent_aux_gap_for_prefect_retry(monkeypatch):
    _patch_postclose_success_path(monkeypatch, health=lambda _db, _td: _recoverable_readiness())
    refreshes = []
    monkeypatch.setattr(
        scheduler_service,
        "_refresh_recoverable_postclose_aux",
        lambda _db, *, trade_date, tables: refreshes.append((trade_date, tables)) or {},
    )

    with pytest.raises(scheduler_service.RetryablePostcloseReadinessError) as captured:
        asyncio.run(scheduler_service._run_postclose_pipeline(object(), {}))

    assert captured.value.result["retryable"] is True
    assert captured.value.result["retry_reason"] == "postclose_aux_upstream_lag"
    assert "aux_refresh" in captured.value.result["pipeline_timings_seconds"]
    assert refreshes == [("2026-07-16", ["limit_up_board"])]


def test_postclose_mixed_blockers_fail_without_aux_retry(monkeypatch):
    recoverable = _recoverable_readiness()
    hard_message = "日K线未覆盖 2026-07-16（最新 2026-07-15）"
    recoverable["blockers"].append(hard_message)
    recoverable["blocker_details"].append(
        {
            "code": "table_not_current",
            "message": hard_message,
            "table": "daily_kbar",
            "expected_trade_date": "2026-07-16",
            "latest_trade_date": "2026-07-15",
        }
    )
    _patch_postclose_success_path(monkeypatch, health=lambda _db, _td: recoverable)

    def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("混合硬阻断不得刷新辅助表")

    monkeypatch.setattr(scheduler_service, "_refresh_recoverable_postclose_aux", unexpected_refresh)

    result = asyncio.run(scheduler_service._run_postclose_pipeline(object(), {}))

    assert result["status"] == "failed"
    assert result["retryable"] is False
    assert result["blocker_classification"]["recoverable_tables"] == ["limit_up_board"]
    assert result["blocker_classification"]["hard_blockers"][0]["table"] == "daily_kbar"


def test_postclose_refresh_error_is_not_mislabeled_as_upstream_lag(monkeypatch):
    _patch_postclose_success_path(monkeypatch, health=lambda _db, _td: _recoverable_readiness())

    def broken_refresh(*_args, **_kwargs):
        raise RuntimeError("database connection lost")

    monkeypatch.setattr(scheduler_service, "_refresh_recoverable_postclose_aux", broken_refresh)

    with pytest.raises(RuntimeError, match="database connection lost"):
        asyncio.run(scheduler_service._run_postclose_pipeline(object(), {}))


def test_postclose_stale_aux_table_without_single_day_gap_evidence_is_hard_blocker():
    readiness = _recoverable_readiness()
    readiness["blockers"] = readiness["blockers"][:1]
    readiness["blocker_details"] = readiness["blocker_details"][:1]

    classification = scheduler_service._classify_postclose_blockers(
        readiness,
        trade_date="2026-07-16",
    )

    assert classification["recoverable_tables"] == []
    assert classification["hard_blockers"][0]["code"] == "table_not_current"


def test_refresh_recoverable_aux_routes_index_daily_to_index_backfill(monkeypatch):
    """index_daily 不是 S4 辅助数据集；不得进 orchestrator（2026-10-15 KeyError 回归）。"""
    import app.services.data_gap_remediation_service as gap

    aux_calls, index_calls = [], []
    monkeypatch.setattr(
        gap,
        "_refresh_auxiliary_dataset",
        lambda _db, *, dataset, trade_date, codes=None: aux_calls.append((dataset, trade_date)) or (7, {"ok": 1}),
    )
    monkeypatch.setattr(gap, "_index_ts_codes", lambda _db: ["000001.SH", "399001.SZ"])
    monkeypatch.setattr(
        gap,
        "_backfill_index_daily_dates",
        lambda _db, dates, codes: index_calls.append((dates, codes)) or {"row_count": 2},
    )
    monkeypatch.setattr("app.services.data_health_service.clear_data_health_cache", lambda: None)

    result = scheduler_service._refresh_recoverable_postclose_aux(
        object(), trade_date="2026-10-15", tables=["index_daily", "limit_up_board"]
    )

    assert index_calls == [(["2026-10-15"], ["000001.SH", "399001.SZ"])]
    assert aux_calls == [("limit_up_board", "2026-10-15")]
    assert result["index_daily"]["record_count"] == 2
    assert result["limit_up_board"]["record_count"] == 7


def test_refresh_auxiliary_dataset_never_sends_index_daily_to_orchestrator(monkeypatch):
    """Structural guard for the 2026-10-15 KeyError: any caller passing index_daily is rerouted."""
    import app.services.data_gap_remediation_service as dgr

    monkeypatch.setattr(dgr, "_index_ts_codes", lambda db: ["000001.SH"])
    monkeypatch.setattr(dgr, "_backfill_index_daily_dates", lambda db, d, c: {"row_count": 1, "dates": d})

    def _boom(*a, **k):
        raise AssertionError("orchestrator must not be called for index_daily")

    monkeypatch.setattr(dgr, "run_auxiliary_application", _boom)
    count, audit = dgr._refresh_auxiliary_dataset(object(), dataset="index_daily", trade_date="2026-10-15")
    assert count == 1 and audit["dates"] == ["2026-10-15"]


def test_refresh_auxiliary_dataset_rejects_unknown_dataset_before_orchestrator(monkeypatch):
    import pytest as _pytest

    import app.services.data_gap_remediation_service as dgr

    monkeypatch.setattr(dgr, "run_auxiliary_application", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    with _pytest.raises(ValueError, match="不是 S4"):
        dgr._refresh_auxiliary_dataset(object(), dataset="no_such_dataset", trade_date="2026-10-15")

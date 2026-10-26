from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.orchestration import api
from app.orchestration.deployments import schedule_all_enabled_tasks


def test_manual_scheduled_task_trigger_uses_one_prefect_deployment(monkeypatch) -> None:
    calls = []
    flow_run_id = uuid4()

    def fake_run_deployment(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=flow_run_id)

    monkeypatch.setattr(api, "run_deployment", fake_run_deployment)

    ref = api.trigger_scheduled_task("post_sync_formal_recommendation")

    assert ref.run_id == str(flow_run_id)
    assert ref.deployment_name == "scheduled-task/post_sync_formal_recommendation"
    assert len(calls) == 1
    call = calls[0]
    assert call["name"] == "scheduled-task/post_sync_formal_recommendation"
    assert call["parameters"]["task_id"] == "post_sync_formal_recommendation"
    assert call["parameters"]["trigger_source"] == "manual"
    assert len(call["parameters"]["correlation_id"]) == 32
    assert call["timeout"] == 0
    assert call["_sync"] is True


def test_recommendation_trigger_carries_only_durable_run_id(monkeypatch) -> None:
    calls = []
    flow_run_id = uuid4()
    monkeypatch.setattr(
        api,
        "run_deployment",
        lambda **kwargs: (calls.append(kwargs), SimpleNamespace(id=flow_run_id))[1],
    )

    ref = api.trigger_recommendation("run_20260730_demo")

    assert ref.run_id == str(flow_run_id)
    assert calls[0]["parameters"]["run_id"] == "run_20260730_demo"
    assert len(calls[0]["parameters"]["correlation_id"]) == 32
    assert calls[0]["timeout"] == 0
    assert calls[0]["_sync"] is True


def test_trigger_forces_sync_path_in_async_context(monkeypatch) -> None:
    """L-052 redispatch 静默失败回归：trigger 在 async event loop 中被调用时，
    run_deployment 的 async_dispatch 会派发到 arun_deployment 返回未 await 的
    coroutine（'arun_deployment was never awaited'），调度实际未执行。
    _sync=True 强制同步路径，确保 redispatch 真实发生。"""
    import asyncio

    calls = []

    def fake_run_deployment(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(api, "run_deployment", fake_run_deployment)

    async def _run():
        ref = api.trigger_recommendation("run_async_ctx")
        return ref

    ref = asyncio.run(_run())

    assert ref.run_id
    assert calls[0]["_sync"] is True


def test_schedule_all_enabled_tasks_registers_every_flow(monkeypatch) -> None:
    """schedule_all_enabled_tasks must import and register every referenced flow.

    Regression guard for the 9c308a6 NameError (intraday_lock_poll_flow /
    stale_run_recovery_flow referenced without import), which silently killed
    the Prefect worker on startup and stalled all cron-driven pipeline runs.
    """
    applied: list[str] = []
    scheduled: list[str] = []
    calls: list[str] = []
    job_vars_list: list[dict] = []
    queues: list = []

    class _FakeFlow:
        """to_deployment must bind as a real method (SimpleNamespace instance
        attributes skip descriptor binding and lose ``self``)."""

        def __init__(self, flow_name: str) -> None:
            self._flow_name = flow_name

        def to_deployment(self, *, name, work_pool_name=None, work_queue_name=None, schedules=None, tags=None, job_variables=None):
            if self._flow_name == "recommendation_flow":
                queues.append(work_queue_name)
            scheduled.append(name)
            calls.append(self._flow_name)
            job_vars_list.append(job_variables)

            class _Deployment:
                def apply(self):
                    applied.append(name)
                    return self

            return _Deployment()

    monkeypatch.setattr("app.orchestration.deployments.scheduled_task_flow", _FakeFlow("scheduled_task_flow"))
    monkeypatch.setattr("app.orchestration.deployments.recommendation_flow", _FakeFlow("recommendation_flow"))
    monkeypatch.setattr("app.orchestration.deployments.manual_preset_matrix_flow", _FakeFlow("manual_preset_matrix_flow"))
    monkeypatch.setattr("app.orchestration.deployments.manual_data_health_remediation_flow", _FakeFlow("manual_data_health_remediation_flow"))
    monkeypatch.setattr("app.orchestration.deployments.intraday_lock_poll_flow", _FakeFlow("intraday_lock_poll_flow"))
    monkeypatch.setattr("app.orchestration.deployments.stale_run_recovery_flow", _FakeFlow("stale_run_recovery_flow"))
    monkeypatch.setattr("app.orchestration.deployments.execution_confirmation_poll_flow", _FakeFlow("execution_confirmation_poll_flow"))
    monkeypatch.setattr("app.orchestration.deployments.SessionLocal", lambda: SimpleNamespace(
        query=lambda *a, **k: SimpleNamespace(filter=lambda *a, **k: SimpleNamespace(all=lambda: [])),
        close=lambda: None,
    ))

    result = schedule_all_enabled_tasks()

    # Every infrastructure flow must be registered, not just DB-backed tasks.
    assert "intraday_lock_poll_flow" in calls
    assert "stale_run_recovery_flow" in calls
    assert "execution_confirmation_poll_flow" in calls
    # 推荐子 run 必须走独立队列，否则父 flow 占满 default 槽会死锁
    assert queues == ["recommendation"]
    assert result.scheduled_task_count == 0
    # 6 non-DB flows: recommendation + 2 manual + 3 periodic (no enabled DB tasks).
    assert len(applied) == 6
    # Every deployment must pin the backend working_dir so the Process worker
    # does not spawn flow-run subprocesses in a throwaway temp directory.
    assert job_vars_list and all(
        jv is not None and jv.get("working_dir", "").endswith("backend")
        for jv in job_vars_list
    )


def test_execution_confirmation_poll_confirms_all_unsettled_in_batches(monkeypatch) -> None:
    """盘中买入确认必须由服务端轮询，而不是只靠前端按钮（10-14 批次无人点击全判 missed）。"""
    from app.orchestration.flows import poll_execution_confirmation
    import app.services.batch_execution_confirmation as bec

    ids = [f"rec_{i:03d}" for i in range(bec.MAX_BATCH_IDS + 3)]
    calls: list[tuple[list[str], bool]] = []
    monkeypatch.setattr(bec, "list_unsettled_confirmation_ids", lambda db: ids)
    monkeypatch.setattr(
        bec, "confirm_batch_executions",
        lambda db, batch, refresh=False: calls.append((batch, refresh)) or {"summary": {"requested_count": len(batch)}},
    )
    monkeypatch.setattr("app.services.tushare_client.is_open_trade_date", lambda d: True)
    monkeypatch.setattr("app.services.schedule_gate.schedule_skip_reason", lambda *a, **k: None)  # 不受真实当前时间(迟到超时)影响
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: SimpleNamespace(close=lambda: None))

    result = poll_execution_confirmation.fn("2026-10-21T10:00:00+08:00")

    assert result["pending"] == len(ids)
    assert [len(batch) for batch, _ in calls] == [bec.MAX_BATCH_IDS, 3]
    assert all(refresh for _, refresh in calls)


def test_execution_confirmation_poll_skips_closed_market(monkeypatch) -> None:
    from app.orchestration.flows import poll_execution_confirmation
    import app.services.batch_execution_confirmation as bec

    monkeypatch.setattr("app.services.tushare_client.is_open_trade_date", lambda d: False)
    monkeypatch.setattr(bec, "list_unsettled_confirmation_ids", lambda db: (_ for _ in ()).throw(AssertionError("must not query")))

    result = poll_execution_confirmation.fn("2026-10-24T10:00:00+08:00")

    assert result["skipped"] is True


def test_execution_confirmation_poll_skips_when_no_unsettled_recommendations(monkeypatch) -> None:
    """没有待确认推荐时不进入确认批次（不拉行情/逐股K线），直接跳过省额度。"""
    from app.orchestration.flows import poll_execution_confirmation
    import app.services.batch_execution_confirmation as bec

    monkeypatch.setattr("app.services.tushare_client.is_open_trade_date", lambda d: True)
    monkeypatch.setattr("app.services.schedule_gate.schedule_skip_reason", lambda *a, **k: None)  # 不受真实当前时间(迟到超时)影响
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(bec, "list_unsettled_confirmation_ids", lambda db: [])
    monkeypatch.setattr(bec, "confirm_batch_executions", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not confirm")))

    result = poll_execution_confirmation.fn("2026-10-26T10:00:00+08:00")

    assert result == {"skipped": True, "reason": "no_unsettled_recommendations", "pending": 0}

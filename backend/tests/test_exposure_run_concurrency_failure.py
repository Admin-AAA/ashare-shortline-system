"""曝光补跑中断回归测试。

10-09 实测：两个曝光补跑 run 并发写 stocks，后到者 1205 锁等待超时；失败处理把 107KB
原始异常写进 progress_message 触发 1406，连带回滚 fail 迁移，run 卡在 running 后被判中断。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.core.time import utc_isoformat
from app.services.runs import pipeline
from app.services.runs.contracts import RUN_ERROR_MESSAGE_MAX_CHARS, RunStatus
from app.services.runs.stages.contracts import StageData
from app.services.runs.stages.market_data import MarketDataStage


class _FakeDb:
    def __init__(self, run):
        self.run = run
        self.commits = 0

    def rollback(self):
        pass

    def expire_all(self):
        pass

    def get(self, _model, _run_id):
        return self.run

    def commit(self):
        self.commits += 1


class _FakeCoordinator:
    def __init__(self):
        self.messages: list[str] = []

    def fail(self, lease, *, now, message):
        self.messages.append(message)
        return True


def test_pipeline_failure_message_is_truncated_before_persisting(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "SQLAlchemyRunRepository",
        lambda db: SimpleNamespace(get_run=lambda run_id: SimpleNamespace(status=RunStatus.RUNNING)),
    )
    monkeypatch.setattr(
        "app.services.workbench_service.invalidate_bootstrap_cache", lambda: None
    )
    run = SimpleNamespace(progress_phase=None, progress_message=None, evidence_class=None, reconstruction_batch_id=None)
    db = _FakeDb(run)
    coordinator = _FakeCoordinator()
    huge = "(pymysql.err.OperationalError) (1205, 'Lock wait timeout exceeded') " + "x" * 110_000

    pipeline._mark_pipeline_run_failed(
        db, run_id="run_x", coordinator=coordinator, worker_lease=object(), message=huge
    )

    assert run.progress_phase == "failed"
    assert len(run.progress_message) <= RUN_ERROR_MESSAGE_MAX_CHARS
    assert run.progress_message.startswith("(pymysql.err.OperationalError) (1205")
    assert all(len(m) <= RUN_ERROR_MESSAGE_MAX_CHARS for m in coordinator.messages)
    assert db.commits == 1


class _RecordingPersistence:
    def __init__(self):
        self.calls: list[str] = []

    def persist_snapshot(self, snapshot):
        self.calls.append("snapshot")

    def persist_stocks_and_bars(self, stocks, *, persist_bars):
        self.calls.append("stocks_and_bars")

    def commit_shared_market_facts(self):
        self.calls.append("commit")

    def persist_run_market_context(self, snapshot, *, universe_count):
        self.calls.append("run_context")


class _Universe:
    def load_universe(self, *, data_source, as_of_date):
        return ({"code": "600000"},), {"trade_date": "2026-10-09", "breadth": {}}, {}


def test_market_data_stage_commits_shared_facts_before_run_scoped_work():
    persistence = _RecordingPersistence()
    stage = MarketDataStage(market_universe=_Universe(), market_persistence=persistence)
    stage.execute(SimpleNamespace(run_id="run_x"), StageData(params={"data_source": "realtime"}))
    # 共享行情事实写完立即提交释放行锁，run 自己的上下文仍留在 run 事务里
    assert persistence.calls == ["snapshot", "stocks_and_bars", "commit", "run_context"]


def test_utc_isoformat_marks_naive_utc_with_z():
    assert utc_isoformat(datetime(2026, 10, 9, 10, 3, 40)) == "2026-10-09T10:03:40Z"
    assert utc_isoformat(None) is None


class _BatchRecordingDb:
    def __init__(self):
        self.events: list[str] = []

    def execute(self, stmt):
        self.events.append(f"execute:{stmt.table.name}")

    def commit(self):
        self.events.append("commit")


def test_shared_market_write_is_serialized_by_named_lock(monkeypatch):
    from app.services.runs.stages import adapters

    events: list[str] = []

    class _LockConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            events.append("lock_conn_closed")

        def scalar(self, stmt, params):
            events.append(f"get_lock:{params['name']}")
            return 1

        def execute(self, stmt, params):
            events.append(f"release_lock:{params['name']}")

    class _Db:
        def get_bind(self):
            return SimpleNamespace(connect=lambda: _LockConn())

        def commit(self):
            events.append("commit")

    monkeypatch.setattr(
        "app.services.workbench_service._persist_stocks_and_bars",
        lambda db, stocks, persist_bars: events.append("write"),
    )
    adapters.ProductionMarketPersistence(_Db(), "run_x").persist_stocks_and_bars(
        [{"code": "600001"}], persist_bars=True
    )
    name = adapters._SHARED_MARKET_WRITE_LOCK
    # 拿锁 → 写入 → 提交 → 放锁：提交发生在放锁之前，后到的 run 拿到锁时前一批已可见且行锁已释放
    assert events == [f"get_lock:{name}", "write", "commit", f"release_lock:{name}", "lock_conn_closed"]


def test_shared_market_write_fails_loudly_when_lock_times_out(monkeypatch):
    import pytest

    from app.services.runs.stages import adapters

    class _LockConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def scalar(self, stmt, params):
            return 0

    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(connect=lambda: _LockConn()))
    monkeypatch.setattr(
        "app.services.workbench_service._persist_stocks_and_bars",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("拿不到锁不应写入")),
    )
    with pytest.raises(RuntimeError, match="共享行情写入锁"):
        adapters.ProductionMarketPersistence(db, "run_x").persist_stocks_and_bars([], persist_bars=True)


def test_stock_and_bar_rows_are_written_in_primary_key_order():
    from app.services import workbench_service as wb

    written: list[list[str]] = []

    class _Db:
        def execute(self, stmt):
            params = stmt.compile().params
            written.append([params[f"code_m{i}"] for i in range(3)])

    stocks = [{"code": c, "name": c, "_bars": []} for c in ("600003", "600001", "600002")]
    wb._persist_stocks_and_bars(_Db(), stocks, persist_bars=True)
    assert written == [["600001", "600002", "600003"]]

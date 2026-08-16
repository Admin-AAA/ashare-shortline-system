"""P1-4 — 数据源统一 fallback trace（R-SOURCE）测试。

锁定：
  - trace contract 校验（ordinal 连续、failure 必须有 category、success 必须有 selected_provider）
  - router 选路循环单点埋点：success / valid_empty / fallback / breaker-skip / 全失败
  - 合法空结果 → VALID_EMPTY，不触发 fallback，不误判 failure
  - 不可重试错误不 fallback（fallback_allowed=False 终止链）
  - 默认无 sink 时行为完全不变（不发 trace）
  - sink 幂等（同 trace_id 重跑 no-op）+ trace/attempt 原子写入
  - baostock 分钟 K 投影到同一契约
  - health 聚合 success/fallback/valid-empty/breaker-skip
  - credential / 敏感正文不落库
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models.tables import DataSourceAttempt, DataSourceRequestTrace as TraceRow
from app.services.data_source_trace import (
    DataSourceAttempt as AttemptContract,
    DataSourceAttemptOutcome,
    DataSourceRequestTrace,
    FinalOutcome,
    request_identity_hash,
)
from app.services import data_source_trace_service as svc
from app.services.tushare_transport import (
    FailureCategory,
    ProviderFailure,
    TushareProviderError,
    TushareProviderRouter,
)


# ── fake provider ────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, priority):
        self.priority = priority


class _FakeProvider:
    def __init__(self, name, priority, behavior):
        self.name = name
        self.config = _Cfg(priority)
        self._behavior = behavior  # ("success", items) / ("failure", category, retryable, fb_allowed)
        self.calls = 0

    def supports(self, api_name):
        return True

    def request(self, api_name, *, params=None, fields=""):
        self.calls += 1
        kind = self._behavior[0]
        if kind == "success":
            return {"code": 0, "data": {"items": self._behavior[1], "fields": ["a"]}}
        _, category, retryable, fb_allowed = self._behavior
        raise ProviderFailure(
            provider=self.name, api_name=api_name, category=category,
            retryable=retryable, fallback_allowed=fb_allowed,
        )


def _router(providers, sink=None):
    return TushareProviderRouter(providers, trace_sink=sink)


# ── trace contract ───────────────────────────────────────────────────

def test_trace_requires_contiguous_ordinals():
    with pytest.raises(ValueError, match="contiguous"):
        DataSourceRequestTrace(
            trace_id="t", correlation_id="c", operation_name="daily",
            request_identity_hash="h", contract_id="x", contract_version=1,
            attempts=(
                AttemptContract(ordinal=2, provider="a", outcome=DataSourceAttemptOutcome.SUCCESS, duration_ms=1),
            ),
            selected_provider="a", final_outcome=FinalOutcome.SUCCESS,
        )


def test_failure_attempt_requires_category():
    with pytest.raises(ValueError, match="failure_category"):
        AttemptContract(ordinal=1, provider="a", outcome=DataSourceAttemptOutcome.FAILURE, duration_ms=1)


def test_success_trace_requires_selected_provider():
    with pytest.raises(ValueError, match="selected_provider"):
        DataSourceRequestTrace(
            trace_id="t", correlation_id="c", operation_name="daily",
            request_identity_hash="h", contract_id="x", contract_version=1,
            attempts=(
                AttemptContract(ordinal=1, provider="a", outcome=DataSourceAttemptOutcome.SUCCESS, duration_ms=1),
            ),
            selected_provider=None, final_outcome=FinalOutcome.SUCCESS,
        )


# ── router instrumentation ───────────────────────────────────────────

def test_router_success_first_provider_no_fallback():
    sink = svc.InMemoryTraceSink()
    p1 = _FakeProvider("fastapic", 1, ("success", [[1, 2]]))
    p2 = _FakeProvider("xiaodefa", 2, ("failure", FailureCategory.NETWORK, True, True))
    r = _router([p1, p2], sink=sink)
    env = r.request("daily")
    assert env["code"] == 0
    assert p1.calls == 1 and p2.calls == 0  # 不 fallback
    assert len(sink.traces) == 1
    t = sink.traces[0]
    assert t.final_outcome == FinalOutcome.SUCCESS
    assert t.selected_provider == "fastapic"
    assert t.attempt_count == 1
    assert t.fallback_count == 0


def test_router_valid_empty_does_not_fallback():
    sink = svc.InMemoryTraceSink()
    p1 = _FakeProvider("fastapic", 1, ("success", []))  # 合法空
    p2 = _FakeProvider("xiaodefa", 2, ("success", [[9]]))
    r = _router([p1, p2], sink=sink)
    r.request("daily")
    assert p1.calls == 1 and p2.calls == 0  # 合法空不 fallback
    t = sink.traces[0]
    assert t.final_outcome == FinalOutcome.VALID_EMPTY
    assert t.attempts[0].outcome == DataSourceAttemptOutcome.VALID_EMPTY


def test_router_fallback_chain_recorded():
    sink = svc.InMemoryTraceSink()
    p1 = _FakeProvider("fastapic", 1, ("failure", FailureCategory.HTTP_SERVER, True, True))
    p2 = _FakeProvider("xiaodefa", 2, ("failure", FailureCategory.CIRCUIT_OPEN, False, True))
    p3 = _FakeProvider("quantdata", 3, ("success", [[5]]))
    r = _router([p1, p2, p3], sink=sink)
    env = r.request("daily")
    assert env["data"]["items"] == [[5]]
    t = sink.traces[0]
    assert t.final_outcome == FinalOutcome.SUCCESS
    assert t.selected_provider == "quantdata"
    assert t.attempt_count == 3
    # breaker skip 映射
    assert t.attempts[1].outcome == DataSourceAttemptOutcome.SKIPPED_BREAKER
    assert t.attempts[1].failure_category == FailureCategory.CIRCUIT_OPEN.value
    # fallback 链
    assert t.attempts[0].fallback_to == "xiaodefa"
    assert t.attempts[1].fallback_to == "quantdata"
    assert t.fallback_count == 2


def test_router_non_retryable_failure_stops_chain():
    sink = svc.InMemoryTraceSink()
    p1 = _FakeProvider("fastapic", 1, ("failure", FailureCategory.INVALID_REQUEST, False, False))
    p2 = _FakeProvider("xiaodefa", 2, ("success", [[1]]))
    r = _router([p1, p2], sink=sink)
    with pytest.raises(TushareProviderError):
        r.request("daily")
    assert p2.calls == 0  # 不可重试 → 不 fallback
    t = sink.traces[0]
    assert t.final_outcome == FinalOutcome.FAILURE
    assert t.attempt_count == 1
    assert t.attempts[0].fallback_to is None


def test_router_no_sink_behavior_unchanged():
    # 无 sink：不发 trace，行为与改动前一致
    p1 = _FakeProvider("fastapic", 1, ("success", [[1]]))
    r = _router([p1], sink=None)
    env = r.request("daily")
    assert env["code"] == 0  # 不抛、不依赖 sink


def test_router_trace_persist_failure_does_not_break_data():
    class _BadSink:
        def persist(self, trace):
            raise RuntimeError("db down")
    p1 = _FakeProvider("fastapic", 1, ("success", [[1]]))
    r = _router([p1], sink=_BadSink())
    env = r.request("daily")  # 持久化失败不影响取数
    assert env["code"] == 0


def test_request_identity_hash_excludes_credential():
    h1 = request_identity_hash("daily", code="000001", params={"trade_date": "2026-08-06"})
    h2 = request_identity_hash("daily", code="000001", params={"trade_date": "2026-08-06"})
    assert h1 == h2 and len(h1) == 64
    h3 = request_identity_hash("daily", code="000002", params={"trade_date": "2026-08-06"})
    assert h1 != h3


# ── MySQL sink 幂等 / 原子 / 聚合 ────────────────────────────────────

def _db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[TraceRow.__table__, DataSourceAttempt.__table__],
    )
    return Session(engine)


def _trace(tid="t1", corr="c1", final=FinalOutcome.SUCCESS, attempts=None):
    attempts = attempts or (
        AttemptContract(ordinal=1, provider="fastapic", outcome=DataSourceAttemptOutcome.SUCCESS, duration_ms=10),
    )
    return DataSourceRequestTrace(
        trace_id=tid, correlation_id=corr, operation_name="daily",
        request_identity_hash=request_identity_hash("daily", code="000001"),
        contract_id="data_source_request_trace", contract_version=1,
        attempts=attempts,
        selected_provider=attempts[-1].provider if final is FinalOutcome.SUCCESS else None,
        final_outcome=final,
    )


def test_mysql_sink_persist_and_idempotent_reuse():
    db = _db()
    sink = svc.MySQLTraceSink(lambda: db)
    r1 = sink.persist(_trace())
    assert r1.ok and not r1.reused
    assert db.scalar(select(func.count()).select_from(TraceRow)) == 1
    assert db.scalar(select(func.count()).select_from(DataSourceAttempt)) == 1
    # 同 trace_id 重跑 → 幂等 no-op
    r2 = sink.persist(_trace())
    assert r2.ok and r2.reused
    assert db.scalar(select(func.count()).select_from(TraceRow)) == 1
    assert db.scalar(select(func.count()).select_from(DataSourceAttempt)) == 1


def test_mysql_sink_trace_attempt_atomic():
    db = _db()
    sink = svc.MySQLTraceSink(lambda: db)
    attempts = (
        AttemptContract(ordinal=1, provider="a", outcome=DataSourceAttemptOutcome.FAILURE,
                        duration_ms=5, failure_category="http_server", retryable=True, fallback_allowed=True,
                        fallback_to="b"),
        AttemptContract(ordinal=2, provider="b", outcome=DataSourceAttemptOutcome.SUCCESS, duration_ms=7,
                        fallback_from="a"),
    )
    sink.persist(_trace(tid="tx", attempts=attempts))
    rows = list(db.scalars(select(DataSourceAttempt).order_by(DataSourceAttempt.ordinal)))
    assert len(rows) == 2
    assert rows[0].fallback_to == "b" and rows[1].fallback_from == "a"
    trace = db.get(TraceRow, "tx")
    assert trace.fallback_count == 1
    assert trace.duration_ms == 12


def test_list_traces_filter_and_fallback_chain():
    db = _db()
    sink = svc.MySQLTraceSink(lambda: db)
    sink.persist(_trace(tid="t_fb", attempts=(
        AttemptContract(ordinal=1, provider="a", outcome=DataSourceAttemptOutcome.FAILURE,
                        duration_ms=5, failure_category="network", fallback_to="b"),
        AttemptContract(ordinal=2, provider="b", outcome=DataSourceAttemptOutcome.SUCCESS, duration_ms=7,
                        fallback_from="a"),
    )))
    sink.persist(_trace(tid="t_ok"))
    all_rows = svc.list_traces(db)
    assert {r["trace_id"] for r in all_rows} == {"t_fb", "t_ok"}
    by_op = svc.list_traces(db, operation_name="daily")
    assert len(by_op) == 2
    chains = svc.recent_fallback_chains(db)
    assert len(chains) == 1 and chains[0]["trace_id"] == "t_fb"
    assert [c["provider"] for c in chains[0]["chain"]] == ["a", "b"]


def test_health_aggregation_counts():
    db = _db()
    sink = svc.MySQLTraceSink(lambda: db)
    sink.persist(_trace(tid="h1"))  # success
    sink.persist(_trace(tid="h2", final=FinalOutcome.VALID_EMPTY,
                        attempts=(AttemptContract(ordinal=1, provider="a", outcome=DataSourceAttemptOutcome.VALID_EMPTY, duration_ms=3),)))
    sink.persist(_trace(tid="h3", attempts=(
        AttemptContract(ordinal=1, provider="a", outcome=DataSourceAttemptOutcome.SKIPPED_BREAKER,
                        duration_ms=1, failure_category="circuit_open", fallback_to="b"),
        AttemptContract(ordinal=2, provider="b", outcome=DataSourceAttemptOutcome.SUCCESS, duration_ms=4, fallback_from="a"),
    )))
    agg = svc.health_aggregation(db, hours=24)
    assert agg["trace_count"] == 3
    assert agg["success_rate"] == round(2 / 3, 3)
    assert agg["valid_empty_rate"] == round(1 / 3, 3)
    assert agg["fallback_rate"] == round(1 / 3, 3)
    assert agg["breaker_skip_count"] == 1
    assert agg["p95_duration_ms"] is not None


def test_no_credential_in_persisted_rows():
    db = _db()
    sink = svc.MySQLTraceSink(lambda: db)
    sink.persist(_trace())
    trace = db.get(TraceRow, "t1")
    blob = json.dumps({
        "selected_provider": trace.selected_provider,
        "request_identity_hash": trace.request_identity_hash,
    })
    attempt = db.scalar(select(DataSourceAttempt))
    blob += json.dumps({"provider": attempt.provider, "failure_category": attempt.failure_category})
    for marker in ("token", "api_key", "secret", "password", "sk-"):
        assert marker not in blob.lower()

"""Audit item 2: whole-request deadline, tenacity retry budget, Sina timeout (fault injection)."""
import time

import pandas as pd
import pytest

import app.services.datasource.gateway as g
from app.services.datasource.gateway import DatasetGateway, DataSourceError, ErrorKind

ROW = {k: 1 for k in g.SCHEMAS["quote.realtime"]}


class P:
    config_keys = ()

    def __init__(self, name, behave):
        self.name, self.behave, self.calls = name, behave, 0

    def supports(self, d, q):
        return True

    def fetch(self, d, q):
        self.calls += 1
        return self.behave(self.calls, q)


def _gw(*ps):
    return DatasetGateway(list(ps), enabled=lambda p: True, failure_threshold=99)


def test_default_deadlines():
    assert g.default_deadline("quote.realtime") == 3.0
    assert g.default_deadline("tushare.daily") == 120.0


def test_realtime_whole_request_under_3s(monkeypatch):
    """整链仍压在 3s 内；单跳预算（2026-11-15 优化②）让三个源都真正被尝试，而不是前两跳吃光。"""
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: False)
    g._HEALTH.reset()
    hang = lambda n, q: time.sleep(10)
    a, b, c = P("a", hang), P("b", hang), P("c", lambda n, q: {"600000": ROW})
    t0 = time.perf_counter()
    r = _gw(a, b, c).fetch_fill("quote.realtime", {}, ["600000"])
    dt = time.perf_counter() - t0
    assert dt < 3.3
    # 改动前：a 独占 3s → b 拿 0 剩余记 "deadline"、c 根本没被调用、整单返回空。
    assert [x["outcome"] for x in r.attempts] == ["timeout", "timeout", "success"]
    assert r.provider == "c" and r.rows["600000"] is ROW
    g._HEALTH.reset()


def test_explicit_deadline_override():
    t0 = time.perf_counter()
    r = _gw(P("a", lambda n, q: time.sleep(5))).fetch_first("ds", {}, deadline_s=0.5)
    assert time.perf_counter() - t0 < 1.0 and r.attempts[0]["outcome"] == "timeout"


def test_transient_error_retried_with_tenacity():
    def flaky(n, q):
        if n == 1:
            raise ConnectionError("reset")
        return [{"x": 1}]

    a = P("a", flaky)
    r = _gw(a).fetch_first("ds", {})
    assert r.provider == "a" and a.calls == 2


def test_retry_budget_shared_across_providers(monkeypatch):
    monkeypatch.setattr(g, "_setting", lambda attr, d: 1 if attr == "datasource_retry_budget" else d)

    def down(n, q):
        raise ConnectionError("down")

    a, b = P("a", down), P("b", down)
    _gw(a, b).fetch_first("ds", {})
    assert a.calls + b.calls == 3  # 2 first tries + 1 retry from the shared budget


def test_non_retryable_not_retried():
    def rl(n, q):
        raise DataSourceError(ErrorKind.RATE_LIMIT)

    a = P("a", rl)
    _gw(a).fetch_first("ds", {})
    assert a.calls == 1


def test_sina_chunk_timeout(monkeypatch):
    import app.services.datasource.sina_realtime as s

    monkeypatch.setattr(s, "SINA_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(s, "_raw_frame_unbounded", lambda codes: time.sleep(5))
    t0 = time.perf_counter()
    with pytest.raises(DataSourceError) as ei:
        s._raw_frame(["600000.SH"])
    assert ei.value.kind is ErrorKind.TIMEOUT and time.perf_counter() - t0 < 1.0


def test_in_fetch_worker_marker_is_scoped_to_pool_worker():
    assert g.in_fetch_worker() is False
    assert g._call_with_timeout(g.in_fetch_worker, 1.0) is True
    assert g.in_fetch_worker() is False


def test_sina_inside_gateway_worker_skips_nested_pool_submission(monkeypatch):
    """provider.fetch 已在 gw-fetch worker 内：sina 分片不得再向同一线程池嵌套提交。"""
    import app.services.datasource.sina_realtime as s

    seen: list[bool] = []
    pool_calls: list[float] = []
    real_call = g._call_with_timeout

    def counting_call(fn, timeout):
        pool_calls.append(timeout)
        return real_call(fn, timeout)

    def fake_unbounded(codes):
        seen.append(g.in_fetch_worker())
        return pd.DataFrame([{"TS_CODE": codes[0], "PRICE": 10.0, "DATE": "20261106"}])

    monkeypatch.setattr(g, "_call_with_timeout", counting_call)
    monkeypatch.setattr(s, "_raw_frame_unbounded", fake_unbounded)

    s._raw_frame(["600000.SH"])          # 独立调用：仍走线程池兜底（1 次）
    assert seen == [True]                # 本身即在该次池任务里执行
    assert len(pool_calls) == 1

    # 经 gateway 调用：外层 gateway 提交 1 次；worker 内不再嵌套提交
    res = s.gateway().fetch_first(s.FRAME_DATASET, {"ts_codes": ["600000.SH"]}, deadline_s=1.0)
    assert res.provider == "sina_realtime"
    assert seen == [True, True]
    assert len(pool_calls) == 2          # 只多了 gateway 外层那一次

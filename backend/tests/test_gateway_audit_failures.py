"""Audit item 1: per-kind failure classification + thresholds (fault injection, no upstream)."""
import time

import pytest

import app.core.shared_breaker as sb
import app.services.datasource.gateway as g
from app.services.datasource.gateway import DatasetGateway, DataSourceError, ErrorKind


def _store(monkeypatch, tmp_path):
    import app.core.metrics as m

    monkeypatch.setattr(m, "_process_role", lambda: "backend")
    monkeypatch.setattr(m, "_store_path", lambda: tmp_path / "rt.sqlite")
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "live")
    sb._cache.clear()
    sb._pause_cache.clear()


class Faulty:
    config_keys = ()

    def __init__(self, name, exc=None, rows=None):
        self.name, self.exc, self.rows, self.calls = name, exc, rows, 0

    def supports(self, d, q):
        return True

    def fetch(self, d, q):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.rows


def _gw(*ps, threshold=10):
    return DatasetGateway(list(ps), enabled=lambda p: True, failure_threshold=threshold)


def _open_left(name, ds):
    sb._cache.clear()
    return sb.shared_open_until(f"live:gw:{name}:{ds}") - time.time()


def _paused_left(name, ds):
    sb._pause_cache.clear()
    return sb.shared_paused_until(f"live:gw:{name}:{ds}") - time.time()


def test_timeout_opens_after_two(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    p = Faulty("t", exc=TimeoutError("slow"))
    gw = _gw(p)
    gw.fetch_first("ds", {})
    assert _open_left("t", "ds") <= 0
    gw.fetch_first("ds", {})
    assert _open_left("t", "ds") > 290
    gw.fetch_first("ds", {})
    assert p.calls == 2  # third call skipped by breaker


def test_bad_data_opens_after_three(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    p = Faulty("b", exc=DataSourceError(ErrorKind.CONTRACT, "missing"))
    gw = _gw(p)
    for _ in range(2):
        gw.fetch_first("ds", {})
    assert _open_left("b", "ds") <= 0
    gw.fetch_first("ds", {})
    assert _open_left("b", "ds") > 290


def test_rate_limit_pauses_and_falls_back_instead_of_tripping_breaker(monkeypatch, tmp_path):
    """2026-11-15 重设：命中上游次数上限**不熔断**，只暂停本源 + 立即降级到下一个源。

    网关职责是「尽量少熔断」：限频是额度信号，等 retry-after 就能用；把它记成熔断会顺带
    降权健康分、触发半开探针与告警，把一个「等 20 秒」的源变成长期垫底源。
    """
    _store(monkeypatch, tmp_path)
    exc = DataSourceError(ErrorKind.RATE_LIMIT, "429")
    exc.retry_after_seconds = 20.0
    primary = Faulty("r", exc=exc)
    backup = Faulty("r_backup", rows=[{"x": 1}])
    gw = DatasetGateway([primary, backup], enabled=lambda x: True, failure_threshold=10)

    assert gw.fetch_first("ds", {}).rows == [{"x": 1}]   # 当次就换到下一个源
    assert backup.calls == 1
    assert _open_left("r", "ds") <= 0               # 熔断状态保持 closed
    assert 15 < _paused_left("r", "ds") <= 20.5     # 只打暂停，冷却听上游 retry-after

    gw.fetch_first("ds", {})                        # 暂停期内该源被跳过
    assert primary.calls == 1
    assert backup.calls == 2
    assert _open_left("r", "ds") <= 0               # 连续限频也不熔断


def test_rate_limit_without_hint_pauses_minute_scale(monkeypatch, tmp_path):
    """无 retry-after 时用分钟级暂停（60s，不再是 90s 熔断冷却，更不走 300s 探测间隔）。"""
    _store(monkeypatch, tmp_path)
    gw = _gw(Faulty("r2", exc=DataSourceError(ErrorKind.RATE_LIMIT)))
    gw.fetch_first("ds", {})
    assert _open_left("r2", "ds") <= 0
    assert 55 < _paused_left("r2", "ds") <= 60.5


def test_pause_expires_then_source_is_used_again(monkeypatch, tmp_path):
    """暂停到期即自动恢复，无需半开探测；探测失败计数不受影响。"""
    _store(monkeypatch, tmp_path)
    p = Faulty("r3", exc=DataSourceError(ErrorKind.RATE_LIMIT))
    gw = _gw(p)
    gw.fetch_first("ds", {})
    key = "live:gw:r3:ds"
    conn = sb._conn()
    with conn:
        conn.execute("UPDATE breaker_state SET open_until=? WHERE key=?", (time.time() - 1, key + sb.PAUSE_KEY_SUFFIX))
    conn.close()
    sb._pause_cache.clear()

    p.exc, p.rows = None, [{"y": 2}]
    assert gw.fetch_first("ds", {}).rows == [{"y": 2}]
    assert p.calls == 2
    assert _paused_left("r3", "ds") <= 0            # 成功后暂停被清除
    assert sb._get_streak(f"{key}") == 0             # 限频不累加半开探测 streak


def test_quota_exhausted_still_trips_a_real_breaker(monkeypatch, tmp_path):
    """日额度耗尽属「当天不可再用」，仍走真熔断（默认 1h），与分钟级限频区分。"""
    _store(monkeypatch, tmp_path)
    gw = _gw(Faulty("q", exc=DataSourceError(ErrorKind.QUOTA_EXHAUSTED)))
    gw.fetch_first("ds", {})
    assert _open_left("q", "ds") > 3500
    assert _paused_left("q", "ds") <= 0


def test_rate_limit_does_not_penalise_health_score(monkeypatch, tmp_path):
    """限频不扣健康分：暂停结束后本源立即回到原排序，不被长期排到链尾。"""
    _store(monkeypatch, tmp_path)
    gw = _gw(Faulty("h", exc=DataSourceError(ErrorKind.RATE_LIMIT)))
    gw.fetch_first("ds", {})
    assert g._HEALTH.snapshot("h", "ds")[0] == 0


def test_thresholds_configurable(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "breaker_threshold_timeout", 1)
    _gw(Faulty("c", exc=TimeoutError())).fetch_first("ds", {})
    assert _open_left("c", "ds") > 290


def test_empty_in_session_is_bad_data_and_falls_back(monkeypatch):
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: True)
    a, b = Faulty("a", rows={}), Faulty("b", rows={"600000": {k: 1 for k in g.SCHEMAS["quote.realtime"]}})
    r = _gw(a, b).fetch_fill("quote.realtime", {}, ["600000"])
    assert [x["outcome"] for x in r.attempts] == ["bad_data", "success"] and r.provider == "b"


def test_empty_off_session_is_empty_not_success(monkeypatch):
    monkeypatch.setattr(g, "in_trading_session", lambda now=None: False)
    a = Faulty("a", rows={})
    gw = _gw(a, threshold=1)
    br = gw._breaker("a", "quote.realtime")
    br.record_failure(kind="timeout")  # one timeout pending
    r = gw.fetch_fill("quote.realtime", {}, ["600000"])
    assert r.attempts[0]["outcome"] == "empty"
    assert br._kind_counts == {"timeout": 1}  # empty did NOT reset counters as a success would


def test_invalid_request_never_trips(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    gw = _gw(Faulty("i", exc=DataSourceError(ErrorKind.INVALID_REQUEST)), threshold=1)
    for _ in range(3):
        gw.fetch_first("ds", {})
    assert _open_left("i", "ds") <= 0


@pytest.mark.parametrize("cat,kind", [
    ("http_rate_limit", "rate_limit"), ("business_rate_limit", "rate_limit"),
    ("timeout", "timeout"), ("malformed_response", "bad_data"), ("network", None),
])
def test_transport_kind_mapping(cat, kind):
    from app.services.tushare_transport import FailureCategory, _breaker_kind

    assert _breaker_kind(FailureCategory(cat)) == kind


def test_transport_timeout_twice_opens_shared(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    import app.services.tushare_transport as tt

    p = tt.TushareHttpProvider(tt.ProviderConfig(
        name="fake", base_url="http://x", credential="k", auth_mode=tt.AuthMode.JSON_TOKEN,
        rate_limit_per_minute=0, max_attempts=1, failure_threshold=5))

    def boom(*a, **kw):
        raise tt.ProviderFailure(provider="fake", api_name="daily", category=tt.FailureCategory.TIMEOUT,
                                 retryable=True, fallback_allowed=True)

    monkeypatch.setattr(p, "_request_once", boom)
    for _ in range(2):
        with pytest.raises(tt.ProviderFailure):
            p.request("daily")
    sb._cache.clear()
    assert sb.shared_open_until("live:tushare:fake:daily") - time.time() > 290

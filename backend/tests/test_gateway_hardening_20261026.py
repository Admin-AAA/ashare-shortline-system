"""2026-10-26 网关加固：探测失败指数退避、上游"容量忙"识别、非 promax 源盘中限速、teajoin 配置不泄密。"""
import random
import time

import app.core.shared_breaker as sb
from app.services import tushare_transport as tt


def test_probe_backoff_grows_and_caps():
    rng = random.Random(1)
    d = [sb.probe_backoff_delay(n, rng=rng) for n in range(6)]
    assert 300 <= d[0] <= 330                     # 第一次失败≈5 分钟（兼容旧行为）
    assert 540 <= d[1] <= 660                     # 第二次≈10 分钟
    assert d[2] > d[1]
    assert all(x <= sb.PROBE_MAX_SECONDS * 1.1 + 1 for x in d)   # 封顶（含 10% 抖动）
    assert sb.probe_backoff_delay(0, base=300, cap=900, rng=random.Random(2)) >= 300


def _store(monkeypatch, tmp_path):
    import app.core.metrics as m

    monkeypatch.setattr(m, "_process_role", lambda: "backend")
    monkeypatch.setattr(m, "_store_path", lambda: tmp_path / "rt.sqlite")
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "live")
    sb._cache.clear()


def test_repeated_probe_failures_lengthen_cooldown_and_success_resets(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    a = sb.SharedCircuitBreaker("gw:p:d", 1, 300.0)
    a.record_failure(force_open=True)
    lefts = []
    for _ in range(3):
        conn = sb._conn()
        with conn:
            conn.execute("UPDATE breaker_state SET open_until=? WHERE key=?", (time.time() - 1, a.key))
        conn.close()
        sb._cache.clear()
        assert a.allow_request() is True        # 抢到半开探测
        a.record_failure()                      # 探测失败
        sb._cache.clear()
        lefts.append(sb.shared_open_until(a.key) - time.time())
    assert lefts[0] < lefts[1] < lefts[2] + 1   # 5 → 10 → 20 分钟（含抖动）
    assert sb._get_streak(a.key) == 3
    a.record_success()
    assert sb._get_streak(a.key) == 0


def _provider(name="teajoin"):
    return tt.TushareHttpProvider(tt.ProviderConfig(name=name, base_url="http://x", credential="secret-token-xyz",
                                                    auth_mode=tt.AuthMode.JSON_TOKEN, rate_limit_per_minute=0))


def test_capacity_busy_is_short_cooldown_not_provider_error():
    p = _provider()
    f = p._business_failure("daily", {"code": 500, "msg": "query capacity busy"})
    assert f.category is tt.FailureCategory.HTTP_RATE_LIMIT and f.fallback_allowed and f.retry_after_seconds == 30.0
    # 参数错误仍然是 invalid_request，不被误判成容量忙
    g = p._business_failure("daily", {"code": 2002, "msg": "参数错误"})
    assert g.category is tt.FailureCategory.INVALID_REQUEST


def test_non_promax_research_intraday_limiter(monkeypatch):
    monkeypatch.setattr(tt, "_RESEARCH_OTHER_LIMITERS", {})
    from app.core.config import get_settings

    st = get_settings()
    assert st.tushare_teajoin_research_intraday_rpm == 30
    assert st.tushare_quantdata_research_intraday_rpm == 0      # 默认不改变 quantdata 现有行为
    assert tt._research_provider_limiter("quantdata") is None
    assert tt._research_provider_limiter("teajoin") is not None


def test_teajoin_settings_do_not_leak_token():
    from app.core.config import get_settings

    st = get_settings()
    assert "teajoin" in st.providers
    rendered = repr(st)
    # 字段名本身含 token（如 agent_max_tokens），不能用子串 "token" 判断泄漏。
    # 空凭证时 `"" in repr` 恒为真，只在确有密钥时检查明文没有出现。
    assert "tushare_teajoin_token" not in rendered
    if st.tushare_teajoin_token:
        assert st.tushare_teajoin_token not in rendered


# ── 容量忙：批量/补数专用短冷却；live 保守 ──────────────────────────────
def _busy_provider(monkeypatch, *, research: bool, script):
    """script: 依次返回/抛出的结果列表；返回 (provider, calls, sleeps)。"""
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: research)
    p = _provider()
    calls, sleeps = [], []
    import threading

    monkeypatch.setattr(
        tt.time, "sleep", lambda s: sleeps.append(s) if threading.current_thread() is threading.main_thread() else None
    )
    monkeypatch.setattr(p, "_wait_for_capacity", lambda api: None)

    def once(api, *, params, fields):
        calls.append(api)
        r = script[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(p, "_request_once", once)
    return p, calls, sleeps


def _busy(p, research):
    return p._business_failure("limit_list_d", {"code": 500, "msg": "query capacity busy"})


OK_ENV = {"data": {"fields": ["x"], "items": [[1]]}}


def test_capacity_busy_batch_retries_in_place_with_jittered_short_waits(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "research")
    sb._cache.clear()
    p0 = _provider()
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: True)
    busy = p0._business_failure("limit_list_d", {"code": 500, "msg": "query capacity busy"})
    assert busy.capacity_busy and busy.retryable                        # 批量：可就地重试
    p, calls, sleeps = _busy_provider(monkeypatch, research=True, script=[busy, busy, OK_ENV])
    env = p.request("limit_list_d", params={"trade_date": "20250102"})
    assert env["data"]["items"] and len(calls) == 3
    assert len(sleeps) == 2 and all(1.5 <= s <= 16 for s in sleeps)  # 数秒级、带抖动
    assert p._breaker("limit_list_d").state != "open"                   # 不开熔断


def test_capacity_busy_batch_exhausted_does_not_open_breaker(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "research")
    sb._cache.clear()
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: True)
    busy = _provider()._business_failure("limit_list_d", {"code": 500, "msg": "query capacity busy"})
    p, calls, sleeps = _busy_provider(monkeypatch, research=True, script=[busy] * 40)
    import pytest

    for _ in range(4):  # 连续多个请求都忙：路由器会换源，但本源熔断不应被打开
        with pytest.raises(tt.ProviderFailure) as ei:
            p.request("limit_list_d", params={})
        assert ei.value.capacity_busy and ei.value.fallback_allowed
    assert len(calls) == 4 * tt.BUSY_BATCH_ATTEMPTS
    assert p._breaker("limit_list_d").allow_request() is True


def test_capacity_busy_live_stays_conservative(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    monkeypatch.setattr(tt, "_is_research_namespace", lambda: False)
    busy = _provider()._business_failure("limit_list_d", {"code": 500, "msg": "query capacity busy"})
    assert busy.capacity_busy and not busy.retryable and busy.retry_after_seconds == 30.0
    p, calls, sleeps = _busy_provider(monkeypatch, research=False, script=[busy, OK_ENV])
    import pytest

    with pytest.raises(tt.ProviderFailure):
        p.request("limit_list_d", params={})
    assert len(calls) == 1 and not sleeps       # live：不重试，立刻交给路由器降级


class _Resp:
    def __init__(self, status, payload, headers=None):
        self.status_code, self._p, self.headers = status, payload, headers or {}
        import json as _j

        self.text = _j.dumps(payload)

    def json(self):
        return self._p

    def close(self):
        pass


_BUSY429 = lambda: _Resp(429, {"code": -1, "msg": "query capacity busy"}, {"Retry-After": "2"})  # noqa: E731
_OK200 = lambda: _Resp(200, {"code": 0, "msg": "", "data": {"fields": ["x"], "items": [[1]]}})  # noqa: E731


def _http_provider(monkeypatch, responses, *, research):
    import threading

    monkeypatch.setattr(tt, "_is_research_namespace", lambda: research)
    sleeps = []
    monkeypatch.setattr(tt.time, "sleep", lambda s: sleeps.append(s) if threading.current_thread() is threading.main_thread() else None)
    p = _provider()
    monkeypatch.setattr(p, "_wait_for_capacity", lambda api: None)
    posts = []

    def post(url, json=None, timeout=None, **kw):
        posts.append(timeout)
        return responses[len(posts) - 1]()

    monkeypatch.setattr(p._session, "post", post)
    return p, posts, sleeps


def test_http_429_capacity_busy_batch_retries_then_succeeds_without_breaker(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    p, posts, sleeps = _http_provider(monkeypatch, [_BUSY429, _BUSY429, _OK200], research=True)
    env = p.request("limit_list_d", params={"trade_date": "20250102"})
    assert env["data"]["items"] == [[1]] and len(posts) == 3
    assert len(sleeps) == 2 and all(1.5 <= s <= 16 for s in sleeps)
    assert p._breaker("limit_list_d").allow_request() is True


def test_http_429_capacity_busy_live_is_not_retried(monkeypatch, tmp_path):
    import pytest

    _store(monkeypatch, tmp_path)
    p, posts, sleeps = _http_provider(monkeypatch, [_BUSY429, _OK200], research=False)
    with pytest.raises(tt.ProviderFailure) as ei:
        p.request("limit_list_d", params={})
    assert ei.value.capacity_busy and not ei.value.retryable and ei.value.fallback_allowed
    assert len(posts) == 1 and not sleeps


def test_http_429_without_busy_text_is_still_plain_rate_limit(monkeypatch, tmp_path):
    import pytest

    _store(monkeypatch, tmp_path)
    p, posts, sleeps = _http_provider(monkeypatch, [lambda: _Resp(429, {"msg": "too many requests"}, {"Retry-After": "7"})], research=True)
    with pytest.raises(tt.ProviderFailure) as ei:
        p.request("limit_list_d", params={})
    assert not ei.value.capacity_busy and not ei.value.retryable and len(posts) == 1


# ── 2026-10-26 21:00：teajoin 偶发整段 403 "invalid api key"（同一把 key 几十秒后恢复）按瞬时拥塞处理 ──
_BADKEY403 = lambda: _Resp(403, {"code": -1, "msg": "invalid api key"})  # noqa: E731


def test_teajoin_transient_invalid_api_key_batch_retries_then_succeeds_without_breaker(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path)
    p, posts, sleeps = _http_provider(monkeypatch, [_BADKEY403, _BADKEY403, _OK200], research=True)
    assert p.name == "teajoin"
    env = p.request("limit_list_d", params={"trade_date": "20250102"})
    assert env["data"]["items"] == [[1]] and len(posts) == 3 and len(sleeps) == 2
    assert p._breaker("limit_list_d").allow_request() is True


def test_teajoin_transient_invalid_api_key_live_falls_back_immediately(monkeypatch, tmp_path):
    import pytest

    _store(monkeypatch, tmp_path)
    p, posts, sleeps = _http_provider(monkeypatch, [_BADKEY403, _OK200], research=False)
    with pytest.raises(tt.ProviderFailure) as ei:
        p.request("limit_list_d", params={})
    assert ei.value.capacity_busy and not ei.value.retryable and ei.value.fallback_allowed and len(posts) == 1

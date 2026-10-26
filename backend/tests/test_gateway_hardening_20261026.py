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
    assert "token" not in repr(st).lower() or st.tushare_teajoin_token not in repr(st)

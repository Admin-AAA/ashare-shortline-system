"""2026-11-15 熔断策略重设：命中限频不再熔断数据源，只「暂停 + 换源」。

三个真实上游的限频形态各不相同，任一形态被误分类成 provider_error / server_error，
都会退回按次数累计熔断，等于策略失效：

* promax   —— HTTP 200/429 + ``{"ok":false,"error":"ip_rate_limited","message":"...请在 56 秒后重试"}``
* teajoin  —— HTTP 200 + ``{"code":-1,"msg":"rate limit exceeded"}``
* quantdata—— ``{"code":50302,"msg":"service temporarily unavailable"}``（并发打满，非故障）
"""
import time

import app.core.shared_breaker as sb
from app.services import tushare_transport as tt


def _provider(name: str = "teajoin"):
    return tt.TushareHttpProvider(
        tt.ProviderConfig(
            name=name,
            base_url="http://x",
            credential="secret-token-xyz",
            auth_mode=tt.AuthMode.JSON_TOKEN,
            rate_limit_per_minute=0,
        )
    )


# ── 1. 三类限频形态都要被判成 rate_limit（→ 暂停），不能落到 provider_error ──────

def test_promax_ip_rate_limited_is_rate_limit_with_upstream_wait():
    p = _provider("promax")
    f = p._business_failure(
        "rt_k",
        {
            "ok": False,
            "error": "ip_rate_limited",
            "message": "当前IP请求频率超过限制，请在 56 秒后重试",
        },
    )
    assert f.category is tt.FailureCategory.BUSINESS_RATE_LIMIT
    assert f.retry_after_seconds == 56.0      # 文案里的「56 秒后重试」被采纳
    assert tt._breaker_kind(f.category) == "rate_limit"


def test_teajoin_rate_limit_exceeded_is_rate_limit():
    p = _provider("teajoin")
    f = p._business_failure("daily", {"code": -1, "msg": "rate limit exceeded"})
    assert f.category is tt.FailureCategory.BUSINESS_RATE_LIMIT
    assert tt._breaker_kind(f.category) == "rate_limit"


def test_quantdata_50302_is_capacity_busy_not_fault():
    p = _provider("quantdata")
    f = p._business_failure("rt_k", {"code": 50302, "msg": "service temporarily unavailable"})
    assert f.category is tt.FailureCategory.HTTP_RATE_LIMIT
    assert f.capacity_busy is True
    assert f.retry_after_seconds and f.retry_after_seconds < 300   # 短暂停，不是长时间冷却
    assert tt._breaker_kind(f.category) == "rate_limit"


def test_quantdata_50302_on_5xx_body_is_capacity_not_server_error():
    haystack = "50302: service temporarily unavailable"
    assert any(term in haystack for term in tt._CAPACITY_EXHAUSTED_TERMS)


def test_daily_quota_still_maps_to_quota_exhausted():
    p = _provider("teajoin")
    f = p._business_failure("daily", {"code": 42901, "msg": "token 当日配额耗尽"})
    assert f.category is tt.FailureCategory.QUOTA_EXHAUSTED
    assert tt._breaker_kind(f.category) == "quota_exhausted"


# ── 2. 真熔断语义：限频只留暂停标记，熔断状态保持 closed ─────────────────────

def _isolate(monkeypatch, tmp_path):
    import app.core.metrics as m

    monkeypatch.setattr(m, "_process_role", lambda: "backend")
    monkeypatch.setattr(m, "_store_path", lambda: tmp_path / "rt.sqlite")
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "live")
    sb._cache.clear()
    sb._pause_cache.clear()


def test_rate_limit_pauses_but_never_opens_breaker(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    breaker = sb.SharedCircuitBreaker("gw:promax:rt_k", 3, 300.0)

    for _ in range(4):
        breaker.record_failure(kind="rate_limit", recovery_seconds=56.0)
        sb._cache.clear()
        sb._pause_cache.clear()

    sb._cache.clear()
    sb._pause_cache.clear()
    assert breaker.state == "closed"
    assert sb.shared_open_until(breaker.key) == 0.0, "限频不得写熔断记录"
    assert sb.shared_paused_until(breaker.key) > time.time(), "只留暂停标记"
    assert breaker.allow_request() is False, "暂停期间跳过本源，让网关换下一个源"


def test_pause_expires_without_half_open_probe(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    breaker = sb.SharedCircuitBreaker("gw:quantdata:rt_k", 3, 300.0)
    breaker.record_failure(kind="rate_limit", recovery_seconds=30.0)
    sb._pause_cache.clear()

    with sb._conn() as conn:
        conn.execute(
            "UPDATE breaker_state SET open_until=? WHERE key=?",
            (time.time() - 1, f"{breaker.key}{sb.PAUSE_KEY_SUFFIX}"),
        )
    sb._pause_cache.clear()

    assert breaker.allow_request() is True          # 暂停到期直接放行
    assert sb.shared_open_until(breaker.key) == 0.0  # 不进入半开探测
    breaker.record_success()
    sb._pause_cache.clear()
    assert sb.shared_paused_until(breaker.key) == 0.0


def test_state_listing_separates_pause_from_breaker(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    paused = sb.SharedCircuitBreaker("gw:teajoin:daily", 3, 300.0)
    opened = sb.SharedCircuitBreaker("gw:quantdata:daily", 3, 300.0)
    paused.record_failure(kind="rate_limit")
    opened.record_failure(kind="quota_exhausted")
    sb._cache.clear()

    rows = {row["key"]: row for row in sb.shared_breaker_states()}
    assert rows[paused.key]["kind"] == "pause"        # |pause 后缀已剥离
    assert rows[opened.key]["kind"] == "breaker"
    assert rows[opened.key]["reason"].startswith("quota_exhausted")


# ── 3. 展示层：暂停不能被算成「熔断打开」 ─────────────────────────────────

def test_pause_rows_hidden_from_breaker_views(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    paused = sb.SharedCircuitBreaker("gw:teajoin:daily", 3, 300.0)
    paused.record_failure(kind="rate_limit")

    from app.services.datasource import topology

    keys = [row[0] for row in topology._breaker_rows()]
    assert paused.key not in keys, "限频暂停不应出现在熔断视图"

    from app.core import metrics

    snap = metrics.snapshot(include_processes=True)
    assert all(b["key"] != paused.key for b in snap["open_breakers"])
    assert any(p["key"] == paused.key for p in snap["rate_limit_pauses"])


def test_limits_view_splits_pauses_from_breakers(monkeypatch, tmp_path):
    """`/api/data-sources/limits` 口径：暂停进 rate_limit_pauses，不进 breakers、不进 warnings。"""
    _isolate(monkeypatch, tmp_path)
    paused = sb.SharedCircuitBreaker("gw:teajoin:daily", 3, 300.0)
    opened = sb.SharedCircuitBreaker("gw:quantdata:daily", 3, 300.0)
    paused.record_failure(kind="rate_limit", recovery_seconds=42.0)
    opened.record_failure(kind="quota_exhausted")

    from app.services.datasource import quota_view

    view = quota_view.collect(None, hours=1)
    breaker_keys = {b["key"] for b in view["breakers"]}
    pause_keys = {p["key"] for p in view["rate_limit_pauses"]}

    assert paused.key in pause_keys and paused.key not in breaker_keys
    assert opened.key in breaker_keys and opened.key not in pause_keys
    assert not any("熔断开启" in w and paused.key in w for w in view["warnings"])
    assert any("熔断开启" in w and opened.key in w for w in view["warnings"])

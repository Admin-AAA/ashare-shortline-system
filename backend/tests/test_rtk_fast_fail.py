"""rt_k fast-fail + research promax intraday throttle (owner, 2026-10-19)."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app.services.datasource.quote_realtime as qr
import app.services.tushare_transport as tt
from app.services.datasource.gateway import DataSourceError, ErrorKind


class _SlowPro:
    def rt_k(self, **kw):
        time.sleep(5)


def _sina_like():
    class P:
        name = "sina"
        config_keys = ()

        def supports(self, d, q):
            return True

        def fetch(self, d, q):
            from app.services.datasource.gateway import SCHEMAS

            return {k: {**{f: 0.0 for f in SCHEMAS[qr.DATASET]}, "code": k, "ts_code": k, "name": "",
                        "trade_date": "", "quote_time": "", "source": "sina", "price": 10.0} for k in q["keys"]}
    return P()


def test_rtk_deadline_raises_timeout(monkeypatch):
    import app.services.tushare_client as tc

    monkeypatch.setattr(tc, "get_pro", lambda: _SlowPro())
    t0 = time.perf_counter()
    with pytest.raises(DataSourceError) as ei:
        qr.RtkProvider().fetch(qr.DATASET, {"keys": ["600000"]})
    assert ei.value.kind is ErrorKind.TIMEOUT
    assert time.perf_counter() - t0 < 2.0


def test_rtk_upstream_breaker_open_skips_without_calling(monkeypatch):
    import app.core.shared_breaker as sb
    import app.services.tushare_client as tc
    from app.services.datasource.gateway import DatasetGateway

    def boom():
        raise AssertionError("promax must not be called while breaker open")

    monkeypatch.setattr(tc, "get_pro", boom)
    monkeypatch.setattr(sb, "is_open", lambda k: k == "tushare:promax:rt_k")
    gw = DatasetGateway([qr.RtkProvider(), _sina_like()], enabled=lambda p: True)
    res = gw.fetch_fill(qr.DATASET, {}, ["600000"])
    assert res.provider == "sina"
    assert res.attempts[0] == {"provider": "tushare_rt_k", "outcome": "circuit_open"}


def test_gateway_falls_to_sina_under_2s(monkeypatch):
    import app.services.tushare_client as tc
    from app.services.datasource.gateway import DatasetGateway

    monkeypatch.setattr(tc, "get_pro", lambda: _SlowPro())
    gw = DatasetGateway([qr.RtkProvider(), _sina_like()], enabled=lambda p: True)
    t0 = time.perf_counter()
    res = gw.fetch_fill(qr.DATASET, {}, ["600000"])
    assert res.provider == "sina" and "600000" in res.rows, res.attempts
    assert time.perf_counter() - t0 < 2.0


def test_is_open_uses_namespace(monkeypatch):
    import app.core.shared_breaker as sb

    seen = {}
    monkeypatch.setattr(sb, "shared_open_until", lambda k: seen.setdefault("k", k) and time.time() + 30)
    monkeypatch.setenv(sb.BREAKER_NAMESPACE_ENV, "live")
    assert sb.is_open("tushare:promax:rt_k") is True
    assert seen["k"] == "live:tushare:promax:rt_k"


SH = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize("dt,ns,expected", [
    (datetime(2026, 10, 19, 10, 0, tzinfo=SH), "research", True),
    (datetime(2026, 10, 19, 9, 14, tzinfo=SH), "research", False),
    (datetime(2026, 10, 19, 15, 5, tzinfo=SH), "research", False),
    (datetime(2026, 10, 17, 10, 0, tzinfo=SH), "research", False),  # Saturday
    (datetime(2026, 10, 19, 10, 0, tzinfo=SH), "live", False),
])
def test_research_intraday_window(monkeypatch, dt, ns, expected):
    monkeypatch.setenv("ASHARE_BREAKER_NAMESPACE", ns)
    assert tt.research_intraday_throttled(dt) is expected


def test_promax_research_limiter_applied(monkeypatch):
    calls = []

    class L:
        def acquire(self):
            calls.append(1)

        def headroom(self):
            return 1.0

    monkeypatch.setattr(tt, "research_intraday_throttled", lambda now=None: True)
    monkeypatch.setattr(tt, "_research_promax_limiter", lambda: L())
    for name, n in (("promax", 1), ("quantdata", 0)):
        calls.clear()
        p = tt.TushareHttpProvider(tt.ProviderConfig(name=name, base_url="http://x", credential="k", auth_mode=tt.AuthMode.JSON_TOKEN, rate_limit_per_minute=0))
        p._wait_for_capacity("daily")
        assert len(calls) == n

"""quantdata 42901（token 当日配额耗尽）→ quota_exhausted，当天其余请求不再打该源、直接换下一家。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.core import provider_quota
from app.services import tushare_transport as tt
from app.services.tushare_transport import (
    AuthMode, FailureCategory, HttpMethod, ProviderConfig, TushareHttpProvider, TushareProviderRouter,
)


class _Resp:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, payload):
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload

    def close(self):
        return None


def _provider(name: str, auth=AuthMode.JSON_TOKEN, method=HttpMethod.POST) -> TushareHttpProvider:
    return TushareHttpProvider(ProviderConfig(
        name=name, base_url=f"https://{name}.example/api", credential="k", auth_mode=auth,
        http_method=method, rate_limit_per_minute=0, max_attempts=1, recovery_seconds=0.1,
        append_api_path=method is HttpMethod.GET))


@pytest.fixture
def quota_env(monkeypatch):
    monkeypatch.setenv("ASHARE_QUOTA_SKIP_IN_TESTS", "1")
    monkeypatch.setattr(provider_quota, "_local_exhausted", set())
    monkeypatch.setattr(provider_quota, "_conn", lambda: None)
    tt._exhausted_cache.clear()
    yield
    tt._exhausted_cache.clear()


def test_daily_limit_text_detection():
    assert tt._is_daily_limit_text("42901: token daily limit exceeded")
    assert tt._is_daily_limit_text("今日调用次数已达上限")
    assert not tt._is_daily_limit_text("今日数据尚未更新")
    assert not tt._is_daily_limit_text("抱歉，您每分钟最多访问该接口的频次为 500 次")


def test_quota_exhausted_skips_provider_rest_of_day(quota_env, monkeypatch):
    qd = _provider("quantdata")
    pm = _provider("promax", AuthMode.API_KEY_HEADER, HttpMethod.GET)
    calls = {"quantdata": 0, "promax": 0}

    def qd_post(url, *, json, timeout):
        calls["quantdata"] += 1
        return _Resp({"code": 42901, "msg": "token daily limit exceeded"})

    def pm_get(url, *, params, timeout):
        calls["promax"] += 1
        return _Resp({"code": 0, "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]}})

    monkeypatch.setattr(qd._session, "post", qd_post)
    monkeypatch.setattr(pm._session, "get", pm_get)
    router = TushareProviderRouter([qd, pm], api_routes={"repurchase": ("quantdata", "promax")})

    assert router.request("repurchase", params={"start_date": "20261016"})["code"] == 0
    assert calls == {"quantdata": 1, "promax": 1}
    assert provider_quota.usage("quantdata")[1] is True

    # 断路器早已过期（recovery 0.1s），但当天不再碰 quantdata
    import time as _t
    _t.sleep(0.15)
    for _ in range(3):
        router.request("repurchase", params={"start_date": "20261016"})
    assert calls["quantdata"] == 1
    assert calls["promax"] == 4

    with pytest.raises(tt.ProviderFailure) as exc:
        qd.request("repurchase", params={})
    assert exc.value.category is FailureCategory.QUOTA_EXHAUSTED
    assert exc.value.upstream_detail == tt.QUOTA_SKIP_DETAIL
    assert exc.value.fallback_allowed is True


def test_quota_flag_is_per_day(quota_env, monkeypatch):
    provider_quota.mark_exhausted("quantdata")
    assert tt.provider_exhausted_today("quantdata") is True
    tt._exhausted_cache.clear()
    monkeypatch.setattr(provider_quota, "_today", lambda: "2099-01-01")
    assert tt.provider_exhausted_today("quantdata") is False


def test_gateway_classifies_quota_as_rate_limit():
    from app.services.datasource.gateway import ErrorKind, classify_exception

    f = tt.ProviderFailure(provider="quantdata", api_name="x", category=FailureCategory.QUOTA_EXHAUSTED,
                           retryable=False, fallback_allowed=True)
    assert classify_exception(f) is ErrorKind.RATE_LIMIT


def test_em_repurchase_mapping_and_window():
    from app.services.datasource import akshare_backup as ab

    rows = [
        {"DIM_SCODE": "300498", "DIM_DATE": "2026-10-16 00:00:00", "REPURPROGRESS": "001",
         "REPURAMOUNTLIMIT": 100000000, "JESX": 100000000, "REPURPRICECAP": 22.21, "REPURENDDATE": None},
        {"DIM_SCODE": "300408", "DIM_DATE": "2026-10-15 00:00:00", "REPURPROGRESS": "004",
         "REPURAMOUNTLIMIT": 500000000, "JESX": 12497767, "REPURPRICECAP": 120.0},
        {"DIM_SCODE": "688728", "DIM_DATE": "2026-10-19 00:00:00", "REPURPROGRESS": "007",
         "REPURAMOUNTLIMIT": None, "JESX": 300000000},
        {"DIM_SCODE": "600000", "DIM_DATE": "2026-09-01 00:00:00", "REPURPROGRESS": "001", "JESX": 1},
    ]
    out = ab.map_repurchase(rows, "20261015", "20261019")
    by = {r["ts_code"]: r for r in out}
    assert set(by) == {"300498.SZ", "300408.SZ", "688728.SH"}
    assert by["300498.SZ"]["proc"] == "预案" and by["300498.SZ"]["amount"] == 1e8
    assert by["300408.SZ"]["proc"] == "实施" and by["300408.SZ"]["amount"] == 5e8  # 方案上限，不是已实施金额
    assert by["688728.SH"]["proc"] == "提议" and by["688728.SH"]["amount"] == 3e8
    assert all(r["source"] == "em_hglist" for r in out)
    # explicit only: 不进 tushare_client 的自动兜底
    assert "repurchase" not in ab.AKSHARE_BACKUP_APIS
    assert ab.AkshareProvider().supports("tushare.repurchase", {})


def test_midterm_repurchase_waits_then_degrades(monkeypatch):
    from app.services import midterm_service as ms
    from app.services.tushare_transport import ProviderFailure, TushareProviderError

    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    class _Pro:
        def repurchase(self, **kw):
            attempts["n"] += 1
            raise TushareProviderError("repurchase", [
                ProviderFailure(provider="quantdata", api_name="repurchase", category=FailureCategory.QUOTA_EXHAUSTED,
                                retryable=False, fallback_allowed=True),
                ProviderFailure(provider="promax", api_name="repurchase", category=FailureCategory.HTTP_RATE_LIMIT,
                                retryable=False, fallback_allowed=True, upstream_detail="upstream_pool_exhausted"),
            ])

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: _Pro())
    backup = pd.DataFrame([{"ts_code": "300498.SZ", "ann_date": "20261016", "proc": "预案", "amount": 1e8}])
    backup.attrs["degraded_source"] = "em_hglist"
    monkeypatch.setattr(ms, "_fetch_repurchase_backup", lambda s, e: backup)
    df = ms._fetch_repurchase_window("20261016", "20261016")
    assert attempts["n"] == 1 + len(ms.PRO_RETRY_WAITS)
    assert sleeps == list(ms.PRO_RETRY_WAITS)
    assert df.attrs["degraded_source"] == "em_hglist"


def test_midterm_fetch_no_wait_when_only_quota_left(monkeypatch):
    from app.services import midterm_service as ms
    from app.services.tushare_transport import ProviderFailure, TushareProviderError

    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    class _Pro:
        def forecast(self, **kw):
            raise TushareProviderError("forecast", [ProviderFailure(
                provider="quantdata", api_name="forecast", category=FailureCategory.QUOTA_EXHAUSTED,
                retryable=False, fallback_allowed=True)])

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: _Pro())
    assert ms._pro_fetch_with_wait("forecast", "x", start_date="20261016") is None
    assert sleeps == []


def test_forecast_day_uses_ann_date_first(monkeypatch):
    """promax forecast 需要 ann_date 或 ts_code；只给 start/end 会回 50101（10-19 日跑 mid-forecast-beat no_data）。"""
    from app.services import midterm_forecast as mf

    calls = []

    class _Pro:
        def forecast(self, **kw):
            calls.append(kw)
            return pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20261017"}])

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: _Pro())
    df = mf._fetch_forecast_day("20261017")
    assert len(df) == 1
    assert len(calls) == 1
    assert calls[0]["ann_date"] == "20261017"
    assert "start_date" not in calls[0] and "end_date" not in calls[0]


def test_forecast_day_falls_back_to_start_end_on_invalid_params(monkeypatch):
    from app.services import midterm_forecast as mf
    from app.services.tushare_transport import ProviderFailure, TushareProviderError

    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    calls = []

    class _Pro:
        def forecast(self, **kw):
            calls.append(kw)
            if "ann_date" in kw:
                raise TushareProviderError("forecast", [ProviderFailure(
                    provider="promax", api_name="forecast", category=FailureCategory.INVALID_REQUEST,
                    retryable=False, fallback_allowed=True, upstream_detail="50101")])
            return pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20261017"}])

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: _Pro())
    df = mf._fetch_forecast_day("20261017")
    assert len(df) == 1
    assert [sorted(k for k in c if k != "fields") for c in calls] == [["ann_date"], ["end_date", "start_date"]]
    assert all("fields" in c for c in calls)
    assert sleeps == []


def test_forecast_day_invalid_params_twice_returns_none(monkeypatch):
    from app.services import midterm_forecast as mf
    from app.services.tushare_transport import ProviderFailure, TushareProviderError

    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    class _Pro:
        def forecast(self, **kw):
            calls.append(kw)
            raise TushareProviderError("forecast", [ProviderFailure(
                provider="promax", api_name="forecast", category=FailureCategory.INVALID_REQUEST,
                retryable=False, fallback_allowed=True)])

    monkeypatch.setattr("app.services.tushare_client.get_pro", lambda: _Pro())
    assert mf._fetch_forecast_day("20261017") is None
    assert len(calls) == 2

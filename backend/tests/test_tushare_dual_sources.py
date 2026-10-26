from __future__ import annotations

from types import SimpleNamespace

from app.services.tushare_transport import (
    AuthMode,
    FailureCategory,
    HttpMethod,
    ProviderConfig,
    ProviderFailure,
    TushareHttpProvider,
    TushareProviderError,
    TushareProviderRouter,
)


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def json(self):
        return {"code": 0, "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]}}

    def close(self) -> None:
        return None


def test_get_provider_sends_params_and_keeps_api_key_in_header(monkeypatch) -> None:
    provider = TushareHttpProvider(
        ProviderConfig(
            name="promax",
            base_url="https://promax.example/tushare/pro",
            credential="secret-key",
            auth_mode=AuthMode.API_KEY_HEADER,
            http_method=HttpMethod.GET,
            append_api_path=True,
            rate_limit_per_minute=180,
        )
    )
    captured = {}

    def fake_get(url, *, params, timeout):
        captured.update(url=url, params=params, timeout=timeout)
        return _FakeResponse()

    monkeypatch.setattr(provider._session, "get", fake_get)
    try:
        result = provider.request(
            "daily",
            params={"ts_code": "000001.SZ"},
            fields="trade_date,close",
        )
        assert result["code"] == 0
        assert captured["url"] == "https://promax.example/tushare/pro/daily"
        assert captured["params"] == {
            "ts_code": "000001.SZ",
            "fields": "trade_date,close",
        }
        assert provider._session.headers["X-API-Key"] == "secret-key"
        assert "secret-key" not in str(captured)
    finally:
        provider.close()


def test_get_provider_treats_http_202_as_data_pending(monkeypatch) -> None:
    provider = TushareHttpProvider(
        ProviderConfig(
            name="promax",
            base_url="https://promax.example/tushare/pro",
            credential="secret-key",
            auth_mode=AuthMode.API_KEY_HEADER,
            http_method=HttpMethod.GET,
            append_api_path=True,
            rate_limit_per_minute=180,
        )
    )

    class _PendingResponse:
        status_code = 202
        headers: dict[str, str] = {}

        def close(self) -> None:
            return None

    monkeypatch.setattr(provider._session, "get", lambda *args, **kwargs: _PendingResponse())
    try:
        try:
            provider.request("stk_mins", params={"ts_code": "000001.SZ"})
        except ProviderFailure as exc:
            assert exc.category is FailureCategory.DATA_PENDING
            assert exc.retryable is False
            assert exc.fallback_allowed is True
        else:
            raise AssertionError("HTTP 202 must not be accepted as completed minute data")
        assert provider.health_snapshot()["breaker_state"] == "closed"
    finally:
        provider.close()


class _FakeProvider:
    def __init__(
        self,
        name: str,
        priority: int,
        result: str,
        *,
        excluded_apis: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.config = SimpleNamespace(priority=priority)
        self.result = result
        self.excluded_apis = excluded_apis

    def supports(self, api_name: str) -> bool:
        return api_name not in self.excluded_apis

    def request(self, api_name: str, *, params=None, fields=""):
        if self.result == "failure":
            raise ProviderFailure(
                provider=self.name,
                api_name=api_name,
                category=FailureCategory.PERMISSION,
                retryable=False,
                fallback_allowed=True,
            )
        return {"code": 0, "data": {"fields": ["ts_code"], "items": [[self.name]]}}


def test_realtime_route_prefers_promax() -> None:
    router = TushareProviderRouter(
        [
            _FakeProvider("promax", -2, "success"),
            _FakeProvider("gw_c", 2, "success"),
        ]
    )

    result = router.request("rt_min", params={"ts_code": "000001.SZ"})

    assert result["data"]["items"] == [["promax"]]


def test_promax_is_only_excluded_from_incompatible_announcement_contract(monkeypatch) -> None:
    # 本用例验证路由表语义（promax 仅被排除出 anns_d）；promax 额度专留 rt_k 的开关另有 test_promax_reserve_20261026
    from app.core.config import get_settings

    monkeypatch.setenv("ASHARE_TUSHARE_PROMAX_RTK_RESERVED", "false")
    monkeypatch.setenv("ASHARE_TUSHARE_PROMAX_RESEARCH_ENABLED", "true")  # pytest 进程属 research 命名空间
    get_settings.cache_clear()
    router = TushareProviderRouter(
        [
            _FakeProvider(
                "promax",
                -2,
                "success",
                excluded_apis=frozenset({"anns_d"}),
            ),
            _FakeProvider("gw_c", 2, "success"),
        ]
    )

    assert router.request("anns_d")["data"]["items"] == [["gw_c"]]
    assert router.request("stk_mins")["data"]["items"] == [["promax"]]


def test_default_realtime_routes_have_promax_first() -> None:
    assert TushareProviderRouter.DEFAULT_API_ROUTES["rt_k"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["rt_min"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["fina_indicator"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["ths_member"][0] == "promax"
    assert TushareProviderRouter.DEFAULT_API_ROUTES["stk_mins"][0] == "promax"
    assert "promax" not in TushareProviderRouter.DEFAULT_API_ROUTES["anns_d"]


def test_promax_minute_contract_accepts_canonical_bars() -> None:
    from app.services.tushare_client import _promax_minute_contract_issue

    envelope = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"],
            "items": [
                ["000001.SZ", "2026-08-31 09:30:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
                ["000001.SZ", "2026-08-31 09:31:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
                ["000001.SZ", "2026-08-31 13:00:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
            ],
        },
    }

    assert _promax_minute_contract_issue(envelope) is None


def test_promax_minute_contract_rejects_snapshot_rows() -> None:
    from app.services.tushare_client import _promax_minute_contract_issue

    envelope = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"],
            "items": [
                ["000001.SZ", "2026-09-01 13:41:00", "1min", 10, 10.1, 9.9, 10, 100, 1000],
                ["000001.SZ", "2026-09-01 13:41:59", "1min", 10, 10.1, 9.9, 10, 90, 900],
                ["000001.SZ", "2026-09-01 15:01:58", "1min", 10, 10.1, 9.9, 10, 90, 900],
            ],
        },
    }

    assert _promax_minute_contract_issue(envelope) == "non_minute_timestamp"


def test_promax_minute_contract_rejection_falls_back_to_next_provider(monkeypatch) -> None:
    from app.services.tushare_client import _PromaxHttpProvider

    promax = _PromaxHttpProvider(
        ProviderConfig(
            name="promax",
            base_url="https://promax.example/tushare/pro",
            credential="secret-key",
            auth_mode=AuthMode.API_KEY_HEADER,
            http_method=HttpMethod.GET,
            append_api_path=True,
            rate_limit_per_minute=180,
        )
    )

    class _SnapshotResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self):
            return {
                "code": 0,
                "data": {
                    "fields": ["ts_code", "trade_time"],
                    "items": [["000001.SZ", "2026-09-01 14:59:31"]],
                },
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr(promax._session, "get", lambda *args, **kwargs: _SnapshotResponse())
    router = TushareProviderRouter([promax, _FakeProvider("gw_c", 2, "success")])
    try:
        result = router.request("stk_mins", params={"ts_code": "000001.SZ"})
        assert result["data"]["items"] == [["gw_c"]]
    finally:
        promax.close()


def _business_envelope_category(code: str, msg: str) -> FailureCategory:
    provider = TushareHttpProvider(
        ProviderConfig(
            name="quantdata",
            base_url="https://api.tushare.pro",
            credential="token",
            auth_mode=AuthMode.JSON_TOKEN,
            http_method=HttpMethod.POST,
            rate_limit_per_minute=120,
        )
    )
    failure = provider._business_failure("limit_list_d", {"code": code, "msg": msg})
    return failure.category


def test_business_code_40203_is_permission_not_rate_limit() -> None:
    """40203 是账号无接口访问权限，等待重试永远不会成功。

    上游原文为「抱歉，您没有接口(limit_list_d)访问权限」。把它归成限流会附带
    retry_after_seconds 的无谓等待，并把排查引向「限流/代理故障」这个错误方向。
    """
    category = _business_envelope_category(
        "40203",
        "抱歉，您没有接口(limit_list_d)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。",
    )
    assert category is FailureCategory.PERMISSION


def test_permission_verdict_does_not_depend_on_message_wording() -> None:
    """错误码本身就足以定性，不依赖消息里出现「权限」二字。"""
    assert _business_envelope_category("40203", "") is FailureCategory.PERMISSION


def test_token_quota_exhausted_is_quota_exhausted() -> None:
    """42901 token 当日配额耗尽：单独归类 quota_exhausted（当天跳过该源），既不是权限不足也不是分钟级限流。"""
    assert (
        _business_envelope_category("42901", "token daily limit exceeded")
        is FailureCategory.QUOTA_EXHAUSTED
    )


def test_request_frequency_message_remains_rate_limit() -> None:
    assert (
        _business_envelope_category("40004", "抱歉，您每分钟最多访问该接口的频次为 500 次")
        is FailureCategory.BUSINESS_RATE_LIMIT
    )


def _provider_for_status_case(**overrides) -> TushareHttpProvider:
    config_kwargs = {
        "name": "promax",
        "base_url": "https://promax.example/tushare/pro",
        "credential": "key",
        "auth_mode": AuthMode.API_KEY_HEADER,
        "http_method": HttpMethod.GET,
        "append_api_path": True,
        "rate_limit_per_minute": 180,
        "recovery_seconds": 60.0,
        "max_attempts": 1,
    }
    config_kwargs.update(overrides)
    return TushareHttpProvider(ProviderConfig(**config_kwargs))


class _StatusResponse:
    headers: dict[str, str] = {}

    def __init__(self, status_code: int, payload: object | None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("response body is not json")
        return self._payload

    def close(self) -> None:
        return None


def _failure_for(status: int, payload: object | None, text: str = "") -> ProviderFailure:
    provider = _provider_for_status_case()
    response = _StatusResponse(status, payload, text)
    provider._session.get = lambda *_args, **_kwargs: response
    try:
        provider.request("margin_detail", params={"trade_date": "20260928"}, fields="")
    except ProviderFailure as failure:
        return failure
    raise AssertionError("expected the provider to fail")


def test_proxy_capacity_exhaustion_is_rate_limit_with_cooldown() -> None:
    """代理容量耗尽的 5xx 要让本源冷却并换源，不能按瞬时错误短退避重试。

    实测 promax 在容量耗尽时返回 `HTTP 503 {"ok":false,"error":"upstream_pool_exhausted"}`。
    按通用 5xx 处理会在几百毫秒后重试，容量尚未恢复就继续施压，把「等一会儿就好」
    变成「这个源彻底不可用」——连续打多个接口时会观察到该源全线失败。
    """
    failure = _failure_for(503, {"ok": False, "error": "upstream_pool_exhausted"})

    assert failure.category is FailureCategory.HTTP_RATE_LIMIT
    assert failure.retryable is False
    assert failure.retry_after_seconds == 60.0
    assert failure.fallback_allowed is True
    assert failure.upstream_detail == "upstream_pool_exhausted"


def test_generic_server_error_stays_retryable_server_failure() -> None:
    """真正的网关错误仍按瞬时服务器错误重试，不被容量语义吞掉。"""
    failure = _failure_for(502, {"error": "bad gateway"})

    assert failure.category is FailureCategory.HTTP_SERVER
    assert failure.retryable is True
    assert failure.retry_after_seconds is None


def test_failure_carries_upstream_detail_for_every_http_class() -> None:
    """上游给出的错误标识必须保留，否则日志里只剩 category 这个粗标签。

    5xx 的响应体曾被整个丢弃，`http_server` 无法区分「代理宕机」与「代理容量耗尽」，
    排查只能靠绕过 router 直连去猜。
    """
    assert _failure_for(500, None, text="Internal Server Error").upstream_detail == "Internal Server Error"
    assert _failure_for(403, {"detail": "forbidden"}).upstream_detail == "forbidden"
    assert _failure_for(502, {"message": "upstream closed"}).upstream_detail == "upstream closed"


def test_unreadable_failure_body_does_not_break_failure_handling() -> None:
    """响应体读不出来时也要正常归类：失败处理本身不能再失败。"""

    class _Unreadable(_StatusResponse):
        def json(self):
            raise ValueError("boom")

        @property
        def text(self):  # type: ignore[override]
            raise RuntimeError("body unavailable")

        @text.setter
        def text(self, _value):
            return None

    provider = _provider_for_status_case()
    provider._session.get = lambda *_args, **_kwargs: _Unreadable(503, None)
    try:
        provider.request("margin_detail", params={"trade_date": "20260928"}, fields="")
    except ProviderFailure as failure:
        assert failure.category is FailureCategory.HTTP_SERVER
        assert failure.upstream_detail is None
    else:
        raise AssertionError("expected the provider to fail")


def test_business_failure_keeps_upstream_code_and_message() -> None:
    """HTTP 200 但 code≠0 的业务错误也要保留上游原文，否则 40203 的「没有接口访问权限」只剩 permission 标签。"""
    provider = _provider_for_status_case(name="quantdata")
    failure = provider._business_failure(
        "cyq_perf",
        {"code": 40203, "msg": "抱歉，您没有接口(cyq_perf)访问权限"},
    )

    assert failure.category is FailureCategory.PERMISSION
    assert failure.upstream_detail == "40203: 抱歉，您没有接口(cyq_perf)访问权限"


def test_all_providers_failed_message_carries_each_upstream_detail() -> None:
    """作业结果与健康报告读到的是汇总消息，必须能直接看出每个 provider 各自失败的真实原因。"""
    error = TushareProviderError(
        "cyq_perf",
        [
            ProviderFailure(
                provider="quantdata",
                api_name="cyq_perf",
                category=FailureCategory.PERMISSION,
                retryable=False,
                fallback_allowed=True,
                upstream_detail="40203: 抱歉，您没有接口(cyq_perf)访问权限",
            ),
            ProviderFailure(
                provider="promax",
                api_name="cyq_perf",
                category=FailureCategory.HTTP_RATE_LIMIT,
                retryable=False,
                fallback_allowed=True,
                retry_after_seconds=60.0,
                upstream_detail="upstream_pool_exhausted",
            ),
        ],
    )

    message = str(error)
    assert "quantdata:permission(40203: 抱歉，您没有接口(cyq_perf)访问权限)" in message
    assert "promax:http_rate_limit(upstream_pool_exhausted)" in message


def test_business_code_40203_rate_limit_text_is_not_permission() -> None:
    """40203 + 「频率超限(1次/小时)」是限流（等窗口恢复），不是无权限；真无权限原文含「没有接口…访问权限」。"""
    rate = _business_envelope_category("40203", "抱歉，您访问接口(index_daily)频率超限(1次/小时)，具体频次详情：https://tushare.pro/document/1?doc_id=108。")
    perm = _business_envelope_category("40203", "抱歉，您没有接口(hsgt_top10)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。")
    assert rate is FailureCategory.BUSINESS_RATE_LIMIT
    assert perm is FailureCategory.PERMISSION

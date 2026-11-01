"""通达信公开 HQ（tdx_quote）接入 quote.realtime：映射、分批、超时/错误、开关、熔断。不拨真网。"""
from __future__ import annotations

import socket
import time

import pytest

from app.services.datasource import quote_realtime as qr
from app.services.datasource import tdx_quote as tdx
from app.services.datasource.gateway import SCHEMAS, DataSourceError, DatasetGateway, ErrorKind

DATASET = tdx.DATASET
REQUIRED = SCHEMAS[DATASET]


class _Api:
    def __init__(self, quotes=None, connect_error=None, fail_hosts=(), sleep=0.0):
        self.quotes = list(quotes or [])
        self.connect_error = connect_error
        self.fail_hosts = set(fail_hosts)
        self.sleep = sleep
        self.connects: list[tuple] = []
        self.batches: list[list] = []
        self.disconnected = 0

    def connect(self, ip, port, time_out=None):
        if self.sleep:
            time.sleep(self.sleep)
        self.connects.append((ip, port, time_out))
        if self.connect_error is not None and (not self.fail_hosts or ip in self.fail_hosts):
            raise self.connect_error
        return self

    def get_security_quotes(self, pairs):
        self.batches.append(list(pairs))
        wanted = {code for _market, code in pairs}
        return [row for row in self.quotes if str(row.get("code")) in wanted]

    def disconnect(self):
        self.disconnected += 1


def _factory(api: _Api):
    return lambda: api


def _quote(code, market, price=10.5, **over):
    row = {
        "market": market, "code": code, "price": price, "open": 10.0, "last_close": 9.8,
        "high": 10.8, "low": 9.7, "vol": 111185079, "amount": 1281599063.32,
        "servertime": "15:00:03.600", "name": "",
    }
    row.update(over)
    return row


def _next():
    class P:
        name = "next"
        config_keys = ()

        def supports(self, dataset, query):
            return dataset == DATASET

        def fetch(self, dataset, query):
            return {k: {**{f: 0.0 for f in REQUIRED}, "code": k, "ts_code": k, "name": "",
                        "trade_date": "2026-10-16", "quote_time": "", "source": "next", "price": 1.0}
                    for k in query["keys"]}

    return P()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    import app.services.datasource.gateway as gw
    from app.services.datasource import cache as ds_cache

    ds_cache._fresh.clear()
    monkeypatch.setattr(gw, "_db_disabled_items", lambda: set())
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    monkeypatch.setattr(tdx, "_acquire", lambda: None)
    monkeypatch.setattr(tdx, "_HOST_CURSOR", 0)
    monkeypatch.setattr(
        "app.engine.trading_calendar.latest_allowed_quote_date", lambda today=None: "2026-10-16",
    )
    qr._GATEWAY = None


def test_market_prefixes():
    assert tdx.code6_and_market("600519") == ("600519", tdx.MARKET_SH)
    assert tdx.code6_and_market("688981") == ("688981", tdx.MARKET_SH)
    assert tdx.code6_and_market("510300") == ("510300", tdx.MARKET_SH)
    assert tdx.code6_and_market("900901") == ("900901", tdx.MARKET_SH)
    assert tdx.code6_and_market("000001") == ("000001", tdx.MARKET_SZ)
    assert tdx.code6_and_market("300750") == ("300750", tdx.MARKET_SZ)
    assert tdx.code6_and_market("830799") == ("830799", tdx.MARKET_BJ)
    assert tdx.code6_and_market("430047") == ("430047", tdx.MARKET_BJ)
    assert tdx.code6_and_market("920001") == ("920001", tdx.MARKET_BJ)
    # 后缀优先：920 不会因为调用方写了 .SH 就被改去上海，反过来显式后缀覆盖前缀。
    assert tdx.code6_and_market("920001.BJ") == ("920001", tdx.MARKET_BJ)
    assert tdx.code6_and_market("000001.SH") == ("000001", tdx.MARKET_SH)
    assert tdx.code6_and_market("000001.XX") is None
    assert tdx.code6_and_market("60051") is None


def test_normalize_maps_schema_and_drops_nonpositive_price():
    requested = {
        "600519": (tdx.MARKET_SH, ["600519"]),
        "000001": (tdx.MARKET_SZ, ["000001"]),
        "920001": (tdx.MARKET_BJ, ["920001"]),
    }
    rows = tdx.normalize_quotes(
        [
            _quote("600519", tdx.MARKET_SH, servertime="9:25:01.200", name="茅台"),
            _quote("000001", tdx.MARKET_SZ, price=0),
            _quote("920001", tdx.MARKET_BJ, price=8.2, servertime="not-a-time"),
            _quote("999999", tdx.MARKET_SZ, price=3),
        ],
        quote_date="2026-10-16",
        requested=requested,
    )
    assert set(rows) == {"600519", "920001"}
    sh = rows["600519"]
    assert set(REQUIRED) <= set(sh)
    assert sh["ts_code"] == "600519.SH"
    assert sh["name"] == "茅台"
    assert sh["trade_date"] == "2026-10-16"
    assert sh["quote_time"] == "09:25:01"
    assert sh["pre_close"] == 9.8
    assert sh["volume"] == 111185079
    assert sh["amount"] == 1281599063.32
    assert sh["source"] == "tdx_quote"
    assert rows["920001"]["ts_code"] == "920001.BJ"
    assert rows["920001"]["quote_time"] == ""


def test_fetch_maps_partial_batch_and_keeps_request_market(monkeypatch):
    monkeypatch.setattr(tdx, "_setting", lambda name, default: "10.0.0.8:7709" if name == "tdx_quote_hosts" else default)
    api = _Api(quotes=[
        _quote("600519", 0, price=11),  # 服务器回显市场号故意写错，后缀仍按请求
        _quote("000001", tdx.MARKET_SZ, price=0),
    ])
    rows = tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": ["600519", "000001", "920001", "bad"]})
    assert set(rows) == {"600519"}
    assert rows["600519"]["ts_code"] == "600519.SH"
    assert rows["600519"]["trade_date"] == "2026-10-16"
    assert api.batches == [[(tdx.MARKET_SH, "600519"), (tdx.MARKET_SZ, "000001"), (tdx.MARKET_BJ, "920001")]]
    assert api.connects == [("10.0.0.8", 7709, tdx.CONNECT_TIMEOUT)]
    assert api.disconnected == 1


def test_empty_batch_returns_empty_without_filling(monkeypatch):
    monkeypatch.setattr(tdx, "_setting", lambda name, default: "10.0.0.8:7709" if name == "tdx_quote_hosts" else default)
    api = _Api(quotes=[])
    assert tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": ["000001"]}) == {}
    assert api.batches == [[(tdx.MARKET_SZ, "000001")]]


def test_empty_host_falls_through_to_next(monkeypatch):
    monkeypatch.setattr(
        tdx, "_setting",
        lambda name, default: "10.0.0.1:7709,10.0.0.2:7709" if name == "tdx_quote_hosts" else default,
    )

    class _Switch(_Api):
        def get_security_quotes(self, pairs):
            ip = self.connects[-1][0]
            self.batches.append((ip, list(pairs)))
            if ip == "10.0.0.1":
                return []
            return [_quote("000001", tdx.MARKET_SZ)]

    api = _Switch()
    rows = tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": ["000001"]})
    assert rows["000001"]["price"] == 10.5
    assert [item[0] for item in api.connects] == ["10.0.0.1", "10.0.0.2"]


def test_no_codes_does_not_connect():
    api = _Api()
    assert tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": []}) == {}
    assert api.connects == []


def test_batches_of_80(monkeypatch):
    monkeypatch.setattr(tdx, "_setting", lambda name, default: "10.0.0.8:7709" if name == "tdx_quote_hosts" else default)
    # 每只都回一行但价格为 0：这是停牌，不是空响应，所以两包都要发出去。
    api = _Api(quotes=[_quote(f"{i:06d}", tdx.MARKET_SZ, price=0) for i in range(81)])
    keys = [f"{i:06d}" for i in range(81)]
    assert tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": keys}) == {}
    assert [len(batch) for batch in api.batches] == [80, 1]


def test_second_host_used_when_first_fails(monkeypatch):
    monkeypatch.setattr(
        tdx, "_setting",
        lambda name, default: "10.0.0.1:7709,10.0.0.2:7710" if name == "tdx_quote_hosts" else default,
    )
    api = _Api(quotes=[_quote("000001", tdx.MARKET_SZ)], connect_error=socket.timeout("slow"), fail_hosts={"10.0.0.1"})
    rows = tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": ["000001"]})
    assert rows["000001"]["price"] == 10.5
    assert [item[0] for item in api.connects] == ["10.0.0.1", "10.0.0.2"]


def test_timeout_when_every_host_times_out():
    api = _Api(connect_error=socket.timeout("timed out"))
    with pytest.raises(DataSourceError) as ei:
        tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": ["000001"]})
    assert ei.value.kind is ErrorKind.TIMEOUT


def test_connect_error_is_datasource_error():
    api = _Api(connect_error=ConnectionError("connection refused"))
    with pytest.raises(DataSourceError) as ei:
        tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": ["000001"]})
    assert ei.value.kind is ErrorKind.UPSTREAM


def test_deadline_when_connect_hangs():
    api = _Api(sleep=5)
    t0 = time.perf_counter()
    with pytest.raises(DataSourceError) as ei:
        tdx.TdxQuoteProvider(_factory(api)).fetch(DATASET, {"keys": ["000001"]})
    assert ei.value.kind is ErrorKind.TIMEOUT
    assert time.perf_counter() - t0 < 2.0


def test_disabled_flag_skips_without_calling(monkeypatch):
    calls = []

    def factory():
        calls.append(1)
        raise AssertionError("disabled provider must not build a client")

    monkeypatch.setattr(tdx, "_setting", lambda name, default: False if name == "tdx_quote_enabled" else default)
    provider = tdx.TdxQuoteProvider(factory)
    assert provider.supports(DATASET, {}) is False
    res = DatasetGateway([provider, _next()], enabled=lambda p: True).fetch_fill(DATASET, {}, ["600519"])
    assert calls == []
    assert res.rows["600519"]["source"] == "next"
    assert all(a["provider"] != "tdx_quote" for a in res.attempts)


def test_breaker_open_skips_without_calling():
    calls = []
    api = _Api(quotes=[_quote("600519", tdx.MARKET_SH)])

    def factory():
        calls.append(1)
        return api

    provider = tdx.TdxQuoteProvider(factory)

    class _Open:
        def allow_request(self):
            return False

        def record_success(self):
            return None

        def record_failure(self, **kwargs):
            return None

    gw = DatasetGateway([provider, _next()], enabled=lambda p: True)
    gw._breakers[(tdx.SOURCE, DATASET)] = _Open()
    res = gw.fetch_fill(DATASET, {}, ["600519"])
    assert calls == []
    assert res.attempts[0] == {"provider": "tdx_quote", "outcome": "circuit_open"}
    assert res.rows["600519"]["source"] == "next"


def test_pytest_default_client_does_not_dial():
    with pytest.raises(DataSourceError) as ei:
        tdx.TdxQuoteProvider().fetch(DATASET, {"keys": ["000001"]})
    assert ei.value.kind is ErrorKind.DISABLED


def test_parse_hosts():
    assert tdx.parse_hosts("") == list(tdx.DEFAULT_HOSTS)
    assert tdx.parse_hosts("10.1.2.3") == [("10.1.2.3", 7709)]
    assert tdx.parse_hosts("10.1.2.3:7711, 10.4.5.6:7709") == [("10.1.2.3", 7711), ("10.4.5.6", 7709)]
    with pytest.raises(DataSourceError) as ei:
        tdx.parse_hosts("10.1.2.3:nope")
    assert ei.value.kind is ErrorKind.CONFIGURATION


def test_chain_is_after_sina_before_tencent():
    from app.core.config import get_settings
    from app.services.datasource.topology import LABELS

    names = [p.name for p in qr.gateway()._providers]
    assert names == [
        "tushare_rt_k", "sina_realtime", "tdx_quote", "tencent_qt",
        "akshare_snapshot", "tushare_rt_min", "xiaoshi_snapshot",
    ]
    provider = next(p for p in qr.gateway()._providers if p.name == "tdx_quote")
    assert provider.config_keys == (("tdx", "quote"),)
    assert provider.supports(DATASET, {}) is True
    assert get_settings().tdx_quote_enabled is True
    assert get_settings().tdx_quote_hosts == ""
    assert LABELS["tdx_quote"] == "通达信"


def test_gateway_accepts_rows():
    api = _Api(quotes=[_quote("600519", tdx.MARKET_SH, name="贵州茅台")])
    gw = DatasetGateway([tdx.TdxQuoteProvider(_factory(api))], enabled=lambda p: True)
    res = gw.fetch_fill(DATASET, {}, ["600519"])
    assert res.provider == "tdx_quote"
    assert res.rows["600519"]["name"] == "贵州茅台"
    assert set(REQUIRED) <= set(res.rows["600519"])

"""akshare backup provider: mapping, gateway semantics, Tushare-first fallback. akshare is fully mocked."""
import types

import pandas as pd
import pytest
import requests

from app.services.datasource import akshare_backup as ab
from app.services.datasource import gateway as gw
from app.services import tushare_client as tc


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    calls = []
    fake = types.SimpleNamespace()

    def reg(name, fn):
        def wrapped(**kw):
            calls.append((name, kw))
            return fn(**kw)
        setattr(fake, name, wrapped)

    monkeypatch.setattr(ab, "_ak", lambda: fake)
    monkeypatch.setattr(ab, "_limit", lambda: None)
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    monkeypatch.setattr(ab, "_GATEWAY", None)
    ab.clear_cache()
    gw.reset_all_breakers()
    yield types.SimpleNamespace(reg=reg, calls=calls)
    ab.clear_cache()
    gw.reset_all_breakers()


SSE = pd.DataFrame([{"信用交易日期": "20261016", "标的证券代码": "600000", "标的证券简称": "浦发银行", "融资余额": 1_000_000_000,
                     "融资买入额": 5e7, "融资偿还额": 4e7, "融券余量": 1000, "融券卖出量": 10, "融券偿还量": 5}])
SZSE = pd.DataFrame([{"证券代码": "000001", "证券简称": "平安银行", "融资买入额": "1,000", "融资余额": "2,000,000",
                      "融券卖出量": "3", "融券余量": "4", "融券余额": "50", "融资融券余额": "2,000,050"}])


def test_margin_mapping_both_exchanges(_iso):
    _iso.reg("stock_margin_detail_sse", lambda date: SSE)
    _iso.reg("stock_margin_detail_szse", lambda date: SZSE)
    rows = ab.AkshareProvider().fetch("tushare.margin_detail", {"params": {"trade_date": "20261016"}})
    by = {r["ts_code"]: r for r in rows}
    assert by["600000.SH"]["rzye"] == 1e9 and by["600000.SH"]["rqye"] is None and by["600000.SH"]["source"] == "akshare_sse"
    assert by["000001.SZ"]["rzye"] == 2e6 and by["000001.SZ"]["rzrqye"] == 2000050 and by["000001.SZ"]["rzche"] is None
    assert by["000001.SZ"]["trade_date"] == "20261016" and by["000001.SZ"]["source"] == "akshare_szse"
    assert [c[0] for c in _iso.calls] == ["stock_margin_detail_sse", "stock_margin_detail_szse"]


def test_margin_never_half_market(_iso):
    _iso.reg("stock_margin_detail_sse", lambda date: SSE)
    _iso.reg("stock_margin_detail_szse", lambda date: pd.DataFrame())
    assert ab.AkshareProvider().fetch("tushare.margin_detail", {"params": {"trade_date": "20261016"}}) == []


def test_pledge_mapping_and_ts_code_filter(_iso):
    df = pd.DataFrame([
        {"股票代码": "000408", "交易日期": "2018-03-02", "质押比例": 79.35, "质押笔数": 73, "无限售股质押数": 4705.7, "限售股质押数": 153501.43},
        {"股票代码": "830799", "交易日期": "2018-03-02", "质押比例": 1.0, "质押笔数": 1, "无限售股质押数": 1, "限售股质押数": 0},
    ])
    _iso.reg("stock_gpzy_pledge_ratio_em", lambda date: df)
    rows = ab.AkshareProvider().fetch("tushare.pledge_stat", {"params": {"end_date": "20180302", "ts_code": "000408.SZ"}})
    assert rows == [{"ts_code": "000408.SZ", "end_date": "20180302", "pledge_count": 73, "unrest_pledge": 4705.7,
                     "rest_pledge": 153501.43, "total_share": None, "rest_total": None, "pledge_ratio": 79.35,
                     "source": "akshare_gpzy_em"}]
    assert ab.ts_code("830799") == "830799.BJ" and ab.ts_code("920001") == "920001.BJ"


def test_pledge_non_friday_falls_back_to_previous_friday(_iso):
    seen = []
    _iso.reg("stock_gpzy_pledge_ratio_em", lambda date: (seen.append(date), pd.DataFrame(
        [{"股票代码": "600000", "交易日期": "2026-09-25", "质押比例": 1, "质押笔数": 1, "无限售股质押数": 1, "限售股质押数": 0}]
        if date == "20260925" else []))[1])
    rows = ab.AkshareProvider().fetch("tushare.pledge_stat", {"params": {"end_date": "20260930"}})
    assert seen == ["20260930", "20260925"] and rows[0]["end_date"] == "20260925"


def test_surv_and_anns_mapping(_iso, monkeypatch):
    import datetime as _dt
    today = _dt.date.today().strftime("%Y%m%d")
    _iso.reg("stock_jgdy_tj_em", lambda date: pd.DataFrame([
        {"代码": "300750", "名称": "宁德时代", "接待机构数量": 12, "接待方式": "特定对象调研", "接待人员": "张三",
         "接待地点": "公司", "接待日期": today, "公告日期": today},
        {"代码": "300750", "名称": "宁德时代", "接待机构数量": 1, "接待日期": "20200101", "公告日期": today}]))
    rows = ab.AkshareProvider().fetch("tushare.stk_surv", {"params": {"trade_date": today}})
    assert len(rows) == 1 and rows[0]["ts_code"] == "300750.SZ" and rows[0]["org_count"] == 12 and rows[0]["surv_date"] == today
    _iso.reg("stock_notice_report", lambda symbol, date: pd.DataFrame([
        {"代码": "600519", "名称": "贵州茅台", "公告标题": "关于xx的公告", "公告类型": "重大事项", "公告日期": "2026-10-16",
         "网址": "https://data.eastmoney.com/notices/detail/600519/AN1.html"}]))
    rows = ab.AkshareProvider().fetch("tushare.anns_d", {"params": {"ann_date": "20261016", "ts_code": "600519.SH"}})
    assert rows[0]["ann_date"] == "20261016" and rows[0]["source"] == "akshare_notice_em" and rows[0]["symbol"] == "600519"
    with pytest.raises(gw.DataSourceError):
        ab.AkshareProvider().fetch("tushare.anns_d", {"params": {"start_date": "20261001", "end_date": "20261016"}})


def test_proxy_error_is_network():
    assert gw.classify_exception(requests.exceptions.ProxyError("x")) == gw.ErrorKind.NETWORK


def test_disabled_switch_skips_provider(_iso, monkeypatch):
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: {ab.CONFIG_KEY})
    _iso.reg("stock_margin_detail_sse", lambda date: SSE)
    _iso.reg("stock_margin_detail_szse", lambda date: SZSE)
    res = ab.backup_gateway().fetch_first("tushare.margin_detail", {"params": {"trade_date": "20261013"}}, allow_stale=False)
    assert res.provider is None and _iso.calls == []


class _Router:
    def __init__(self, exc=None, envelope=None):
        self.exc, self.envelope, self.calls = exc, envelope, 0

    def request(self, api, params, fields):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.envelope


def test_session_tushare_first_then_akshare(_iso):
    _iso.reg("stock_margin_detail_sse", lambda date: SSE)
    _iso.reg("stock_margin_detail_szse", lambda date: SZSE)
    ok = {"code": 0, "data": {"fields": ["ts_code", "trade_date", "rzye"], "items": [["600000.SH", "20261014", 1.0]]}}
    df = tc._TushareSession(_Router(envelope=ok)).margin_detail(trade_date="20261014")
    assert list(df["rzye"]) == [1.0] and _iso.calls == []  # tushare served -> akshare untouched
    df = tc._TushareSession(_Router(exc=RuntimeError("quota"))).margin_detail(trade_date="20261015")
    assert set(df["source"]) == {"akshare_sse", "akshare_szse"} and len(df) == 2


def test_session_reraises_primary_when_backup_fails(_iso):
    def boom(date):
        raise requests.exceptions.ProxyError("proxy down")
    _iso.reg("stock_margin_detail_sse", boom)
    _iso.reg("stock_margin_detail_szse", boom)
    with pytest.raises(RuntimeError, match="quota"):
        tc._TushareSession(_Router(exc=RuntimeError("quota"))).margin_detail(trade_date="20261012")


def test_non_backup_api_unchanged(_iso):
    r = _Router(exc=RuntimeError("x"))
    with pytest.raises(RuntimeError):
        tc._TushareSession(r).daily(trade_date="20261016")
    assert _iso.calls == []

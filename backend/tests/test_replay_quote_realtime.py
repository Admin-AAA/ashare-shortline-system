"""Replay: legacy vs gateway quote.realtime outputs must be byte-identical (canonical JSON)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.datasource import gateway as gw
from app.services.datasource import quote_realtime as qr

FIX = Path(__file__).parent / "fixtures" / "datasource_replay"
CODES = ["000001", "600519", "300750"]


def _rtk_df():
    d = json.loads((FIX / "rt_k_3codes.json").read_text())
    return pd.DataFrame(d["rows"], columns=d["columns"])


def _stockai_df():
    return pd.DataFrame([
        {"TS_CODE": "000001.SZ", "NAME": "平安银行", "DATE": "20261016", "TIME": "15:00:03", "PRICE": 11.2,
         "OPEN": 11.0, "PRE_CLOSE": 11.1, "HIGH": 11.3, "LOW": 10.9, "VOLUME": 1000, "AMOUNT": 11200},
        {"TS_CODE": "600519.SH", "NAME": "贵州茅台", "DATE": "20261016", "TIME": "15:00:03", "PRICE": 1500.0,
         "OPEN": 1490, "PRE_CLOSE": 1495, "HIGH": 1510, "LOW": 1488, "VOLUME": 10, "AMOUNT": 15000},
        {"TS_CODE": "300750.SZ", "NAME": "宁德时代", "DATE": "20261016", "TIME": "15:00:03", "PRICE": 250.0,
         "OPEN": 249, "PRE_CLOSE": 248, "HIGH": 252, "LOW": 247, "VOLUME": 20, "AMOUNT": 5000},
    ])


@pytest.fixture
def env(monkeypatch):
    import tushare as ts

    from app.services import realtime_quote_client as rq
    from app.services import tushare_client as tc

    state = {"stockai": True, "cutover": ""}

    class Pro:
        def rt_k(self, ts_code):
            return _rtk_df()

    monkeypatch.setattr(tc, "get_pro", lambda: Pro())
    monkeypatch.setattr(ts, "realtime_quote", lambda ts_code: _stockai_df(), raising=False)

    def configure():
        if not state["stockai"]:
            raise RuntimeError("no token")

    monkeypatch.setattr(rq, "_configure_realtime_session", configure)
    monkeypatch.setattr(rq, "_enabled_override", lambda: True)
    monkeypatch.setattr(rq, "validate_market_frame", lambda *a, **k: None)
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    monkeypatch.setattr(gw, "_flag_set", lambda attr: {"quote.realtime"} if attr == "datasource_cutover" and state["cutover"] else set())
    monkeypatch.setattr(qr, "_GATEWAY", None)
    rq._cache.update(fetched_at=0.0, rows={})
    return state


def _run(state, fn, cutover):
    from app.services import realtime_quote_client as rq

    state["cutover"] = cutover
    rq._cache.update(fetched_at=0.0, rows={})
    return gw.canonical(fn())


def test_fetch_realtime_quotes_identical_when_stockai888_serves(env):
    from app.services.realtime_quote_client import fetch_realtime_quotes

    fn = lambda: fetch_realtime_quotes(CODES, use_cache=False)  # noqa: E731
    assert _run(env, fn, "") == _run(env, fn, "1")


def test_pnl_quotes_identical_when_stockai888_unconfigured(env):
    from app.services.realtime_pnl_service import _fetch_rtk_quotes

    env["stockai"] = False
    fn = lambda: _fetch_rtk_quotes(CODES)  # noqa: E731
    legacy, new = _run(env, fn, ""), _run(env, fn, "1")
    assert legacy == new and json.loads(new)  # real recorded rt_k rows


def test_gateway_fills_codes_stockai888_missed(env, monkeypatch):
    import tushare as ts

    from app.services.realtime_quote_client import fetch_realtime_quotes

    monkeypatch.setattr(ts, "realtime_quote", lambda ts_code: _stockai_df().iloc[:1], raising=False)
    env["cutover"] = "1"
    rows = fetch_realtime_quotes(CODES, use_cache=False)
    assert rows["000001"]["source"] == "realtime_quote"
    assert rows["600519"]["source"] == "tushare_rt_k" and rows["600519"]["price"] > 0


def test_disabled_xiaoshi_never_called(monkeypatch):
    called = []
    monkeypatch.setattr(qr.XiaoshiSnapshotProvider, "fetch", lambda self, d, q: called.append(1) or {})
    g = gw.DatasetGateway([qr.XiaoshiSnapshotProvider()],
                          enabled=lambda p: gw.provider_enabled(p, {("xiaoshi", "quote")}))
    assert g.fetch_fill(qr.DATASET, {}, ["000001"]).rows == {} and not called

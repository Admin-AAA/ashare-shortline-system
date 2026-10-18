"""Replay: gateway quote.realtime vs the pre-migration rt_k path, byte-identical.

``pnl_quotes_golden.json`` was produced by the legacy ``_fetch_rtk_quotes`` (rt_k branch)
on the recorded real rt_k response ``rt_k_3codes.json`` before the legacy code was removed.
"""
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


@pytest.fixture
def env(monkeypatch):
    from app.services import realtime_quote_client as rq
    from app.services import tushare_client as tc

    class Pro:
        def rt_k(self, ts_code):
            return _rtk_df()

    monkeypatch.setattr(tc, "get_pro", lambda: Pro())
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    monkeypatch.setattr(qr, "_GATEWAY", None)
    monkeypatch.setattr("app.engine.trading_calendar.latest_allowed_quote_date", lambda today=None: "2026-10-16")
    rq._cache.update(fetched_at=0.0, rows={})


def test_pnl_quotes_byte_identical_to_legacy_golden(env):
    from app.services.realtime_pnl_service import _fetch_rtk_quotes

    golden = (FIX / "pnl_quotes_golden.json").read_text()
    assert gw.canonical(_fetch_rtk_quotes(CODES)) == golden


def test_rt_min_fills_codes_rt_k_missed(env, monkeypatch):
    from app.services import tushare_client as tc
    from app.services.realtime_quote_client import fetch_realtime_quotes

    class Pro:
        def rt_k(self, ts_code):
            return _rtk_df().iloc[:1]

    monkeypatch.setattr(tc, "get_pro", lambda: Pro())
    monkeypatch.setattr(tc, "fetch_realtime_minute_kbar", lambda code: {"close": 9.9, "trade_date": "2026-10-16", "trade_time": "14:59", "ts_code": code})
    rows = fetch_realtime_quotes(CODES, use_cache=False)
    first = str(_rtk_df().iloc[0]["ts_code"]).split(".")[0]
    others = [c for c in CODES if c != first]
    assert rows[first]["source"] == "tushare_rt_k"
    assert all(rows[c]["source"] == "tushare_rt_min" and rows[c]["price"] == 9.9 for c in others)


def test_disabled_xiaoshi_never_called(monkeypatch):
    called = []
    monkeypatch.setattr(qr.XiaoshiSnapshotProvider, "fetch", lambda self, d, q: called.append(1) or {})
    g = gw.DatasetGateway([qr.XiaoshiSnapshotProvider()],
                          enabled=lambda p: gw.provider_enabled(p, {("xiaoshi", "quote")}))
    assert g.fetch_fill(qr.DATASET, {}, ["000001"]).rows == {} and not called

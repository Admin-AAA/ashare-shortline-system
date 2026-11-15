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
    # 固定为「没开任何可选 flag」：本机 .env 开了腾讯行情，否则链上会多出 tencent_qt，
    # 断言随机器漂移（这是本测试长期在本地失败、CI 通过的原因）。
    monkeypatch.setattr(gw, "_flag_set", lambda attr: set())
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
    # sina / akshare 快照 off -> rt_min fills（akshare 快照是 2026-10-26 新增的第二备份，会真去翻新浪页，测试里关掉）
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: {("sina", "quote"), ("akshare", "quote")})
    # ths 实时行情（2026-11-15 接入，紧跟 rt_k）会去连同花顺，这里关掉以固定本测试的 rt_min 填坑路径
    from app.services.datasource import ths_client

    monkeypatch.setattr(ths_client, "is_enabled", lambda: False)
    monkeypatch.setattr(qr, "_GATEWAY", None)
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

"""Replay: sina realtime_quote via gateway is byte-identical to the legacy paths.

``sina_frame_3codes.json`` is a real sina response recorded 2026-10-18;
``sina_quote_rows_golden.json`` is the pre-gateway ``realtime_quote_client._normalize_rows``
output on it (commit 39dcfb3), recorded before that code was removed.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.datasource import gateway as gw
from app.services.datasource import quote_realtime as qr
from app.services.datasource import sina_realtime as sr

FIX = Path(__file__).parent / "fixtures" / "datasource_replay"
TS = ["000001.SZ", "600519.SH", "300750.SZ"]


def _frame():
    d = json.loads((FIX / "sina_frame_3codes.json").read_text())
    return pd.DataFrame(d["rows"], columns=d["columns"])


@pytest.fixture
def env(monkeypatch):
    import tushare as ts

    monkeypatch.setattr(ts, "realtime_quote", lambda ts_code: _frame()[_frame()["TS_CODE"].isin(ts_code.split(","))].reset_index(drop=True), raising=False)
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    monkeypatch.setattr(sr, "_GATEWAY", None)
    monkeypatch.setattr(qr, "_GATEWAY", None)


def test_snapshot_frame_identical_legacy_vs_gateway(env, monkeypatch):
    from app.core.config import get_settings
    from app.services import market_data as md

    out = []
    for cut in (set(), {"quote.snapshot"}):
        monkeypatch.setattr(gw, "_flag_set", lambda attr, cut=cut: cut if attr == "datasource_cutover" else set())
        f = md._fetch_tushare_realtime_frame(TS, get_settings())
        out.append(gw.canonical({"cols": list(f.columns), "rows": f.astype(object).where(f.notna(), None).values.tolist()}))
    assert out[0] == out[1]


def test_sina_quote_rows_match_pre_gateway_golden(env):
    rows = sr.SinaProvider().fetch("quote.realtime", {"keys": ["000001", "600519", "300750"]})
    assert gw.canonical(rows) == (FIX / "sina_quote_rows_golden.json").read_text()


def test_order_rtk_then_sina_then_rtmin(env, monkeypatch):
    from app.services import tushare_client as tc

    class Pro:
        def rt_k(self, ts_code):
            return pd.DataFrame()  # rt_k empty -> sina fills

    monkeypatch.setattr(tc, "get_pro", lambda: Pro())
    monkeypatch.setattr(tc, "fetch_realtime_minute_kbar", lambda code: (_ for _ in ()).throw(AssertionError("rt_min not needed")))
    rows = qr.fetch(["000001", "600519"])
    assert {r["source"] for r in rows.values()} == {"realtime_quote"}
    # 顺序见 quote_realtime 模块说明：rt_k → sina → tencent（默认关）→ akshare 快照 → rt_min → 小石。
    assert [p.name for p in qr.gateway()._providers] == [
        "tushare_rt_k", "sina_realtime", "tencent_qt", "akshare_snapshot", "tushare_rt_min", "xiaoshi_snapshot",
    ]


def test_gateway_failure_raises_like_legacy(monkeypatch):
    import tushare as ts

    monkeypatch.setattr(ts, "realtime_quote", lambda ts_code: (_ for _ in ()).throw(ConnectionError("x")), raising=False)
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    monkeypatch.setattr(sr, "_GATEWAY", None)
    with pytest.raises(RuntimeError):
        sr.fetch_frame(TS)

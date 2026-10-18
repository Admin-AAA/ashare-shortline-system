"""Replay: bar.minute legacy vs gateway outputs must be byte-identical (canonical JSON).

Raw provider rows are real responses recorded 2026-10-18 (Baostock 5min, Tushare
stk_mins 1min for 000001 on 2026-10-16); xiaoshi rows reuse the recorded stk_mins
rows relabelled (xiaoshi had not published that day yet).  Both paths call the very
same stubbed fetchers, so any difference comes from routing/post-processing.
"""
import copy
import itertools
import json
from datetime import date
from pathlib import Path

import pytest

from app.services import tushare_client as tc
from app.services.datasource import bar_minute
from app.services.datasource import gateway as gw

RAW = json.loads((Path(__file__).parent / "fixtures" / "datasource_replay" / "minute_raw.json").read_text())


def _xiaoshi_rows(freq):
    rows = RAW["stk_mins_1min"] if freq == "1min" else RAW["baostock_5min"]
    return [dict(r, source="xiaoshi_min1") for r in rows]


class _D(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 17)


@pytest.fixture
def stub(monkeypatch):
    state = {"xiaoshi": True, "baostock": True, "stk": True, "calls": []}

    def xs(code, *, start_date, end_date, freq, trace=None):
        state["calls"].append(("xiaoshi", start_date, end_date, freq))
        return copy.deepcopy(_xiaoshi_rows(freq)) if state["xiaoshi"] else []

    def bs(code, freq, start, end, *, is_index=False, trace=None):
        state["calls"].append(("baostock", start, end, freq))
        return copy.deepcopy(RAW["baostock_5min"]) if state["baostock"] else []

    def stk(code, *, start_time, end_time, trace=None, is_index=False):
        state["calls"].append(("stk", start_time, end_time))
        return copy.deepcopy(RAW["stk_mins_1min"]) if state["stk"] else []

    monkeypatch.setattr(tc, "_fetch_xiaoshi_minute_kbars", xs)
    monkeypatch.setattr(tc, "_fetch_baostock_minute_kbars", bs)
    monkeypatch.setattr(tc, "_fetch_tushare_minute_kbars", stk)
    monkeypatch.setattr(tc, "_attach_pre_close", lambda rows: [dict(r, pre_close=1.23) for r in rows])
    monkeypatch.setattr(tc, "date", _D)
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: set())
    monkeypatch.setattr(bar_minute, "_GATEWAY", None)
    return state


def _both(stub, monkeypatch, fn):
    out = []
    for cut in (set(), {"bar.minute"}):
        monkeypatch.setattr(gw, "_flag_set", lambda attr, cut=cut: cut if attr == "datasource_cutover" else set())
        stub["calls"].clear()
        out.append((gw.canonical(fn()), list(stub["calls"])))
    return out


@pytest.mark.parametrize("freq,trade_date,xiaoshi,fallback", list(itertools.product(
    ["1min", "5min"], ["2026-10-16", None], [True, False], [True, False])))
def test_fetch_minute_kbars_byte_identical(stub, monkeypatch, freq, trade_date, xiaoshi, fallback):
    stub["xiaoshi"], stub["baostock"], stub["stk"] = xiaoshi, fallback, fallback
    (legacy, lcalls), (new, ncalls) = _both(stub, monkeypatch, lambda: tc.fetch_minute_kbars("000001", trade_date, freq=freq))
    assert legacy == new
    assert lcalls == ncalls  # same upstream calls, same order


@pytest.mark.parametrize("xiaoshi", [True, False])
def test_fetch_recent_minute_kbars_byte_identical(stub, monkeypatch, xiaoshi):
    stub["xiaoshi"] = xiaoshi
    (legacy, _), (new, _) = _both(stub, monkeypatch, lambda: tc.fetch_recent_minute_kbars("000001", ndays=3))
    assert legacy == new and json.loads(new)


def test_disabled_xiaoshi_is_skipped(stub, monkeypatch):
    monkeypatch.setattr(gw, "_db_disabled_keys", lambda: {("xiaoshi", "kline")})
    res = bar_minute.fetch("000001", "2026-10-16", "2026-10-16", "5min")
    assert res.provider == "baostock" and all(c[0] != "xiaoshi" for c in stub["calls"])


def test_metrics_recorded(stub):
    from app.core import metrics

    metrics.reset()
    bar_minute.fetch("000001", "2026-10-16", "2026-10-16", "1min")
    names = {(c["name"], c["labels"]["provider"]) for c in metrics.snapshot()["counters"]}
    assert ("dataset_requests", "xiaoshi_min1") in names

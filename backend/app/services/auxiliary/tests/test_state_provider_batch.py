"""Verify the slow-moving auxiliary state providers fetch the whole market in
one batched Tushare call instead of per-stock loops (the per-stock path deadlocked
formal runs with 429 rate-limiting — see docs/bugs/auxiliary-per-stock-tushare-deadlock.md).

These tests do not hit the network. They inject a recording fake client and assert:
  - exactly one provider call is made (batch), regardless of requested_codes size;
  - requested_codes filter is applied when non-empty;
  - empty requested_codes loads the whole market (scheduler batch-sync path).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from app.services.auxiliary.contracts import AuxiliaryDatasetRequest, AuxiliaryInstrument
from app.services.auxiliary.state_adapters import TushareStateProvider


class _RecordingClient:
    """Records every attribute access as a call with its kwargs, returns a frame."""

    def __init__(self, responses: dict[str, pd.DataFrame]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, api_name: str):
        def _call(**kwargs: Any) -> pd.DataFrame | None:
            self.calls.append((api_name, dict(kwargs)))
            return self._responses.get(api_name)
        return _call


def _request(codes: tuple[str, ...], trade_date: date) -> AuxiliaryDatasetRequest:
    return AuxiliaryDatasetRequest(
        dataset_key="_test",
        instruments=tuple(AuxiliaryInstrument(c) for c in codes),
        trade_date=trade_date,
        refresh_enabled=True,
        requested_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )


def test_pledge_stat_single_batch_call_filters_to_requested_codes():
    frame = pd.DataFrame([
        {"ts_code": "000001.SZ", "end_date": "20260630", "pledge_count": 2, "unrest_pledge": 0.1, "rest_pledge": 0.2, "pledge_ratio": 5},
        {"ts_code": "600000.SH", "end_date": "20260630", "pledge_count": 1, "unrest_pledge": 0.3, "rest_pledge": 0.4, "pledge_ratio": 3},
        {"ts_code": "300001.SZ", "end_date": "20260630", "pledge_count": 0, "unrest_pledge": 0.0, "rest_pledge": 0.0, "pledge_ratio": 0},
    ])
    client = _RecordingClient({"pledge_stat": frame})
    provider = TushareStateProvider(client, session=None)  # session unused by pledge_stat
    rows = provider.pledge_stat(_request(("000001", "600000"), date(2026, 7, 16)))
    assert client.calls == [("pledge_stat", {"end_date": "20260630"})]
    codes = {row["code"] for row in rows}
    assert codes == {"000001", "600000"}


def test_holder_number_single_batch_call_empty_codes_no_filter():
    frame = pd.DataFrame([
        {"ts_code": "000001.SZ", "end_date": "20260630", "holder_num": 10},
        {"ts_code": "600000.SH", "end_date": "20260630", "holder_num": 20},
    ])
    client = _RecordingClient({"stk_holdernumber": frame})
    provider = TushareStateProvider(client, session=None)
    rows = provider.holder_number(_request((), date(2026, 7, 16)))
    # end_date <= 2026-07-16 keeps both quarterly rows; no codes → no filter
    assert client.calls == [("stk_holdernumber", {"end_date": "20260630"})]
    assert {row["code"] for row in rows} == {"000001", "600000"}


def test_fina_indicator_period_batch_single_call():
    frame = pd.DataFrame([
        {"ts_code": "000001.SZ", "end_date": "20260630", "ann_date": "20260720", "roe": 12.0, "eps": 1.5, "bps": 9.0},
        {"ts_code": "600000.SH", "end_date": "20260630", "ann_date": "20260721", "roe": 8.0, "eps": 0.7, "bps": 5.0},
    ])
    client = _RecordingClient({"fina_indicator": frame})
    provider = TushareStateProvider(client, session=None)
    rows = provider.fina_indicator(_request(("000001",), date(2026, 7, 16)))
    assert client.calls == [("fina_indicator", {"period": "20260630"})]
    # ann_date 20260720 / 20260721 都晚于 trade_date 20260716 → 全部被剔除 → 空
    assert rows == []


def test_fina_indicator_keeps_pre_trade_date_announcements():
    frame = pd.DataFrame([
        {"ts_code": "000001.SZ", "end_date": "20260630", "ann_date": "20260710", "roe": 12.0, "eps": 1.5, "bps": 9.0},
        {"ts_code": "600000.SH", "end_date": "20260630", "ann_date": "20260721", "roe": 8.0, "eps": 0.7, "bps": 5.0},
    ])
    client = _RecordingClient({"fina_indicator": frame})
    provider = TushareStateProvider(client, session=None)
    rows = provider.fina_indicator(_request(("000001", "600000"), date(2026, 7, 16)))
    # 只保留 ann_date <= 20260716 的（即 000001）
    assert {row["code"] for row in rows} == {"000001"}
    assert rows[0]["ann_date"] == "20260710"


def test_capital_flow_single_batch_call_filters_by_requested_codes():
    frame = pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": "20260716", "net_mf_amount": 100_000_000, "net_mf_vol": 1.0,
         "buy_elg_amount": 50, "sell_elg_amount": 10, "buy_lg_amount": 40, "sell_lg_amount": 5,
         "buy_md_amount": 3, "sell_md_amount": 1, "buy_sm_amount": 2, "sell_sm_amount": 1},
        {"ts_code": "600000.SH", "trade_date": "20260716", "net_mf_amount": 200_000_000, "net_mf_vol": 2.0,
         "buy_elg_amount": 0, "sell_elg_amount": 0, "buy_lg_amount": 0, "sell_lg_amount": 0,
         "buy_md_amount": 0, "sell_md_amount": 0, "buy_sm_amount": 0, "sell_sm_amount": 0},
    ])
    client = _RecordingClient({"moneyflow": frame})
    provider = TushareStateProvider(client, session=None)
    rows = provider.capital_flow(_request(("000001",), date(2026, 7, 16)))
    assert client.calls == [("moneyflow", {"trade_date": "20260716"})]
    assert {row["code"] for row in rows} == {"000001"}


def test_auction_unavailable_returns_empty_without_per_stock_loop():
    # stk_auction raises on no-permission accounts; auction must short-circuit.
    class _FailingClient:
        def __init__(self):
            self.calls = 0
        def __getattr__(self, name):
            def _call(**kwargs):
                self.calls += 1
                raise RuntimeError("no permission")
            return _call
    client = _FailingClient()
    provider = TushareStateProvider(client, session=None)
    rows = provider.auction(_request(("000001", "600000", "300001"), date(2026, 7, 16)))
    assert rows == ()
    assert client.calls == 1  # single batch attempt, NOT 3 per-stock calls


def test_pledge_stat_quarter_end_rollback_into_prior_year():
    # 2026-01-15 → no quarter-end yet in 2026 → rolls back to 2025-12-31
    client = _RecordingClient({"pledge_stat": pd.DataFrame()})
    provider = TushareStateProvider(client, session=None)
    provider.pledge_stat(_request(("000001",), date(2026, 1, 15)))
    assert client.calls == [("pledge_stat", {"end_date": "20251231"})]
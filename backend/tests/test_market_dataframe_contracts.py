from __future__ import annotations

import pandas as pd
import pytest

from app.adapters.market_source.dataframe_contracts import MarketFrameContractError, validate_market_frame


def test_daily_contract_accepts_a_canonical_tushare_batch() -> None:
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260730",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "vol": 1000.0,
                "amount": 10200.0,
            }
        ]
    )

    validated = validate_market_frame("daily", frame)

    assert validated is frame


def test_daily_contract_rejects_missing_columns_at_the_provider_seam() -> None:
    frame = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260730"}])

    with pytest.raises(MarketFrameContractError, match="daily"):
        validate_market_frame("daily", frame)


def test_factor_contract_rejects_duplicate_stock_dates() -> None:
    frame = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "trade_date": "20260730", "macd": 1.0},
            {"ts_code": "000001.SZ", "trade_date": "20260730", "macd": 1.1},
        ]
    )

    with pytest.raises(MarketFrameContractError, match="stk_factor"):
        validate_market_frame("stk_factor", frame)


def test_empty_provider_result_keeps_its_declared_schema() -> None:
    frame = pd.DataFrame(columns=["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"])

    assert validate_market_frame("daily", frame) is frame


@pytest.mark.parametrize(
    ("kind", "required"),
    [
        ("daily_basic", {"ts_code", "trade_date", "turnover_rate"}),
        ("stk_factor_pro", {"ts_code", "trade_date"}),
        ("cyq_perf", {"ts_code", "trade_date", "cost_50pct"}),
        ("moneyflow", {"ts_code", "trade_date"}),
        ("stk_limit", {"ts_code", "trade_date", "up_limit", "down_limit"}),
        ("index_daily", {"ts_code", "trade_date", "close"}),
        ("realtime_quote", {"TS_CODE", "PRICE"}),
        ("minute_kbar", {"ts_code", "trade_time", "close"}),
        ("holder_number", {"ts_code", "end_date"}),
        ("pledge_stat", {"ts_code", "end_date"}),
        ("institutional_survey", {"ts_code", "surv_date"}),
        ("block_trade", {"ts_code", "trade_date", "price"}),
    ],
)
def test_empty_frames_are_normalized_to_declared_columns(kind: str, required: set[str]) -> None:
    frame = validate_market_frame(kind, pd.DataFrame())

    assert required <= set(frame.columns)


def test_contract_error_identifies_provider_kind_and_column() -> None:
    frame = pd.DataFrame([{"ts_code": "bad", "trade_date": "2026-07-30"}])

    with pytest.raises(MarketFrameContractError) as caught:
        validate_market_frame("moneyflow", frame, provider="auxiliary-tushare")

    assert caught.value.provider == "auxiliary-tushare"
    assert caught.value.kind == "moneyflow"
    assert {"ts_code", "trade_date"} <= set(caught.value.failure_columns)


def test_price_contract_rejects_negative_values() -> None:
    frame = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260730", "price": -1}])

    with pytest.raises(MarketFrameContractError, match="block_trade"):
        validate_market_frame("block_trade", frame)

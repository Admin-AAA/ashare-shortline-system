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

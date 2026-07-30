"""Pandera contracts at the upstream market-data seam."""
from __future__ import annotations

from typing import Literal

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors


MarketFrameKind = Literal["daily", "stk_factor"]


class MarketFrameContractError(ValueError):
    def __init__(self, kind: MarketFrameKind, cause: Exception) -> None:
        self.kind = kind
        super().__init__(f"{kind} DataFrame contract violation: {cause}")


_TS_CODE = pa.Column(str, pa.Check.str_matches(r"^\d{6}\.(SZ|SH|BJ)$"), nullable=False)
_TRADE_DATE = pa.Column(str, pa.Check.str_matches(r"^\d{8}$"), nullable=False)

DAILY_SCHEMA = pa.DataFrameSchema(
    {
        "ts_code": _TS_CODE,
        "trade_date": _TRADE_DATE,
        "open": pa.Column(float, nullable=False, coerce=True),
        "high": pa.Column(float, nullable=False, coerce=True),
        "low": pa.Column(float, nullable=False, coerce=True),
        "close": pa.Column(float, nullable=False, coerce=True),
        "vol": pa.Column(float, nullable=False, coerce=True),
        "amount": pa.Column(float, nullable=False, coerce=True),
    },
    strict=False,
    unique=["ts_code", "trade_date"],
)

STK_FACTOR_SCHEMA = pa.DataFrameSchema(
    {"ts_code": _TS_CODE, "trade_date": _TRADE_DATE},
    strict=False,
    unique=["ts_code", "trade_date"],
)

_SCHEMAS = {"daily": DAILY_SCHEMA, "stk_factor": STK_FACTOR_SCHEMA}


def validate_market_frame(kind: MarketFrameKind, frame: pd.DataFrame) -> pd.DataFrame:
    """Validate one provider frame before callers normalize or merge it."""
    try:
        _SCHEMAS[kind].validate(frame, lazy=True, inplace=True)
    except (SchemaError, SchemaErrors) as error:
        raise MarketFrameContractError(kind, error) from error
    return frame

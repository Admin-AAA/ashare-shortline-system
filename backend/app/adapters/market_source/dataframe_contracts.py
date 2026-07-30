"""Pandera contracts at the upstream market-data seam."""
from __future__ import annotations

from typing import Literal, cast

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors


MarketFrameKind = Literal[
    "daily", "daily_basic", "stk_factor", "stk_factor_pro", "cyq_perf",
    "moneyflow", "stk_limit", "index_daily", "realtime_quote", "minute_kbar",
    "holder_number", "pledge_stat", "institutional_survey", "block_trade",
]


class MarketFrameContractError(ValueError):
    def __init__(self, kind: MarketFrameKind, cause: Exception, *, provider: str) -> None:
        self.kind = kind
        self.provider = provider
        failure_columns = sorted(
            {str(value) for value in getattr(cause, "failure_cases", pd.DataFrame()).get("column", [])}
        )
        self.failure_columns = tuple(failure_columns)
        columns = ",".join(failure_columns) or "unknown"
        super().__init__(
            f"{provider}:{kind} DataFrame contract violation; columns={columns}: {cause}"
        )


_TS_CODE = pa.Column(str, pa.Check.str_matches(r"^\d{6}\.(SZ|SH|BJ)$"), nullable=False)
_TRADE_DATE = pa.Column(str, pa.Check.str_matches(r"^\d{8}$"), nullable=False)
_NON_NEGATIVE = pa.Column(float, pa.Check.ge(0), nullable=True, coerce=True)

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

_SCHEMAS: dict[MarketFrameKind, pa.DataFrameSchema] = {
    "daily": DAILY_SCHEMA,
    "daily_basic": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "trade_date": _TRADE_DATE, "turnover_rate": _NON_NEGATIVE},
        strict=False, unique=["ts_code", "trade_date"],
    ),
    "stk_factor": STK_FACTOR_SCHEMA,
    "stk_factor_pro": STK_FACTOR_SCHEMA,
    "cyq_perf": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "trade_date": _TRADE_DATE, "cost_50pct": _NON_NEGATIVE},
        strict=False, unique=["ts_code", "trade_date"],
    ),
    "moneyflow": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "trade_date": _TRADE_DATE}, strict=False,
        unique=["ts_code", "trade_date"],
    ),
    "stk_limit": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "trade_date": _TRADE_DATE, "up_limit": _NON_NEGATIVE, "down_limit": _NON_NEGATIVE},
        strict=False, unique=["ts_code", "trade_date"],
    ),
    "index_daily": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "trade_date": _TRADE_DATE, "close": _NON_NEGATIVE},
        strict=False, unique=["ts_code", "trade_date"],
    ),
    "realtime_quote": pa.DataFrameSchema(
        {
            "TS_CODE": pa.Column(str, pa.Check.str_matches(r"^\d{6}\.(SZ|SH|BJ)$"), nullable=False),
            "PRICE": _NON_NEGATIVE,
        },
        strict=False, unique=["TS_CODE"],
    ),
    "minute_kbar": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "trade_time": pa.Column(str, nullable=False), "close": _NON_NEGATIVE},
        strict=False, unique=["ts_code", "trade_time"],
    ),
    "holder_number": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "end_date": _TRADE_DATE}, strict=False,
    ),
    "pledge_stat": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "end_date": _TRADE_DATE}, strict=False,
        unique=["ts_code", "end_date"],
    ),
    "institutional_survey": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "surv_date": _TRADE_DATE}, strict=False,
    ),
    "block_trade": pa.DataFrameSchema(
        {"ts_code": _TS_CODE, "trade_date": _TRADE_DATE, "price": _NON_NEGATIVE}, strict=False,
    ),
}


def validate_market_frame(
    kind: MarketFrameKind | str,
    frame: pd.DataFrame,
    *,
    provider: str = "tushare",
) -> pd.DataFrame:
    """Validate one provider frame before callers normalize or merge it."""
    if kind not in _SCHEMAS:
        raise ValueError(f"unknown market frame kind: {kind}")
    typed_kind = cast(MarketFrameKind, kind)
    schema = _SCHEMAS[typed_kind]
    if frame.empty:
        for column in schema.columns:
            if column not in frame.columns:
                frame[column] = pd.Series(dtype="object")
    try:
        schema.validate(frame, lazy=True, inplace=False)
    except (SchemaError, SchemaErrors) as error:
        raise MarketFrameContractError(typed_kind, error, provider=provider) from error
    return frame

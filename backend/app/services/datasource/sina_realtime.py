"""Sina realtime snapshot (tushare SDK ``realtime_quote`` crawler, no token).

Two datasets:
* ``quote.realtime_frame`` — raw sina DataFrame per ≤50-code chunk, byte-identical to the
  legacy ``market_data._fetch_tushare_realtime_frame`` (used by market_data/market_pool
  intraday snapshots; cutover flag ``quote.snapshot``).
* ``quote.realtime`` — normalized ``{code6: row}`` (source tag ``realtime_quote``).
"""
from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from app.services.datasource.gateway import DatasetGateway

FRAME_DATASET = "quote.realtime_frame"
CHUNK = 50  # sina 单次最多 50 只
SINA_RPM = 600
SINA_TIMEOUT_SECONDS = 2.0  # tushare.realtime_quote has no timeout knob: bound each chunk ourselves


class FrameRows(list):
    """List of records (gateway-friendly truthiness/len) carrying the original DataFrame."""

    def __init__(self, frame: pd.DataFrame) -> None:
        super().__init__(frame.to_dict("records") if frame is not None else [])
        self.frame = frame


def _raw_frame_unbounded(ts_codes: list[str]) -> pd.DataFrame:
    import tushare as ts

    from app.services import market_data  # noqa: F401 - import applies the verify_token short-circuit

    return ts.realtime_quote(ts_code=",".join(ts_codes))


def _raw_frame(ts_codes: list[str]) -> pd.DataFrame:
    from app.services.datasource.gateway import _call_with_timeout

    return _call_with_timeout(lambda: _raw_frame_unbounded(ts_codes), SINA_TIMEOUT_SECONDS)


def _f(v: Any) -> float:
    try:
        return float(v) if v is not None and v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


def normalize_rows(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Sina frame -> {code6: quote} (identical to the pre-gateway realtime_quote normalizer)."""
    rows: dict[str, dict[str, Any]] = {}
    if df is None or df.empty:
        return rows
    for _, r in df.iterrows():
        ts_code_str = str(r.get("TS_CODE") or "").strip()
        if not ts_code_str:
            continue
        code6 = ts_code_str.split(".")[0]
        trade_date_raw = str(r.get("DATE") or "").strip()
        trade_date = (
            f"{trade_date_raw[0:4]}-{trade_date_raw[4:6]}-{trade_date_raw[6:8]}"
            if len(trade_date_raw) == 8 and trade_date_raw.isdigit()
            else trade_date_raw
        )
        rows[code6] = {
            "code": code6, "ts_code": ts_code_str, "name": str(r.get("NAME") or "").strip(),
            "trade_date": trade_date, "quote_time": str(r.get("TIME") or "").strip(),
            "price": _f(r.get("PRICE")), "open": _f(r.get("OPEN")), "pre_close": _f(r.get("PRE_CLOSE")),
            "high": _f(r.get("HIGH")), "low": _f(r.get("LOW")), "volume": _f(r.get("VOLUME")),
            "amount": _f(r.get("AMOUNT")), "source": "realtime_quote",
        }
    return rows


class SinaProvider:
    name = "sina_realtime"
    config_keys = (("sina", "quote"),)

    def __init__(self) -> None:
        from app.services.tushare_transport import _make_limiter

        self._lim = _make_limiter("gateway:sina_realtime", SINA_RPM)

    def supports(self, dataset: str, query: Mapping[str, Any]) -> bool:
        return dataset in (FRAME_DATASET, "quote.realtime")

    def fetch(self, dataset: str, query: Mapping[str, Any]) -> Any:
        if dataset == FRAME_DATASET:
            self._lim.acquire()
            return FrameRows(_raw_frame(list(query["ts_codes"])))
        from app.services.tushare_client import ts_code

        out: dict[str, dict[str, Any]] = {}
        codes = [ts_code(c) for c in query["keys"]]
        for i in range(0, len(codes), CHUNK):
            self._lim.acquire()
            out.update(normalize_rows(_raw_frame(codes[i:i + CHUNK])))
        return out


_GATEWAY: DatasetGateway | None = None


def gateway() -> DatasetGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = DatasetGateway([SinaProvider()])
    return _GATEWAY


def fetch_frame(ts_codes: list[str]) -> pd.DataFrame:
    """Gateway path for one ≤50-code chunk; raises like legacy when every provider failed."""
    res = gateway().fetch_first(FRAME_DATASET, {"ts_codes": ts_codes})
    if res.provider is None:
        errors = [a for a in res.attempts if a["outcome"] not in ("empty",)]
        if errors:
            raise RuntimeError(f"realtime_quote gateway failed: {errors[-1]}")
        return pd.DataFrame()
    return res.rows.frame

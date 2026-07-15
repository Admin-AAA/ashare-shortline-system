"""Tushare-backed implementation of the typed market-source contract.

The legacy ``market_data`` data loaders are intentionally contained here until
callers outside this boundary are migrated.  Typed callers use only
``MarketSourcePort`` and never need provider helpers or dynamic capability
checks.
"""
from __future__ import annotations

from copy import copy
from types import MappingProxyType
from typing import Mapping, Sequence, cast

from app.services import market_data
from app.services.market_source.port import (
    BatchStatus,
    DailyBar,
    DailyBarBatch,
    DailyBarRequest,
    DateRange,
    FailurePolicy,
    ItemAudit,
    MarketSourceAudit,
    MarketSourceError,
    MarketSourceErrorCode,
    MarketSourceOperation,
    MarketSourceRequestFailed,
    Quote,
    QuoteBatch,
    QuoteRef,
    QuoteRequest,
    QuoteScope,
    classify_status,
    infer_exchange,
)


class TushareMarketSourceAdapter:
    """Production adapter with a typed Port surface and a temporary legacy shim.

    Passing ``QuoteRequest`` or ``DailyBarRequest`` selects the public typed
    contract.  The no-request/list forms are retained only for unmodified
    market-pool callers and must not be used by new code.
    """

    def __init__(self, settings: object | None = None) -> None:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        self.settings = settings

    def fetch_quotes(
        self, request: QuoteRequest | QuoteScope | None = None
    ) -> QuoteBatch | list[dict[str, object]]:
        if not isinstance(request, QuoteRequest):
            scope = request if isinstance(request, QuoteScope) else None
            settings = self.settings
            if scope is not None and scope.max_quote_count is not None:
                settings = copy(self.settings)
                settings.market_scan_size = scope.max_quote_count
            return cast(list[dict[str, object]], market_data._fetch_quotes("tushare", settings))

        try:
            rows = cast(list[Mapping[str, object]], market_data._fetch_quotes("tushare", self.settings))
        except Exception:
            error = MarketSourceError(
                operation=MarketSourceOperation.QUOTES,
                code=MarketSourceErrorCode.SOURCE_UNAVAILABLE,
                message="tushare quote retrieval failed",
            )
            if request.failure_policy is FailurePolicy.RAISE:
                raise MarketSourceRequestFailed(error) from None
            audit = MarketSourceAudit(
                source="tushare",
                operation=MarketSourceOperation.QUOTES,
                failure_policy=request.failure_policy,
                status=BatchStatus.FAILURE,
                requested_count=1,
                returned_count=0,
                empty_count=0,
                failed_count=1,
            )
            return QuoteBatch(quotes=(), errors=(error,), audit=audit)

        if request.max_quote_count is not None:
            rows = rows[: request.max_quote_count]
        quotes = tuple(Quote.from_row(row) for row in rows)
        status = classify_status(len(quotes), 0)
        audit = MarketSourceAudit(
            source="tushare",
            operation=MarketSourceOperation.QUOTES,
            failure_policy=request.failure_policy,
            status=status,
            requested_count=1,
            returned_count=len(quotes),
            empty_count=1 if not quotes else 0,
            failed_count=0,
        )
        return QuoteBatch(quotes=quotes, errors=(), audit=audit)

    def fetch_daily_bars(
        self, request: DailyBarRequest | Sequence[str], date_range: DateRange | None = None
    ) -> DailyBarBatch | dict[str, list[dict[str, object]]]:
        if not isinstance(request, DailyBarRequest):
            result: dict[str, list[dict[str, object]]] = {}
            start_date = date_range.start_date if date_range is not None else None
            end_date = date_range.end_date if date_range is not None else None
            for code in request:
                quote = {"code": code, "exchange": infer_exchange(code)}
                try:
                    result[code] = cast(
                        list[dict[str, object]],
                        market_data._fetch_kline(
                            "tushare", quote, self.settings, start_date=start_date, end_date=end_date
                        ),
                    )
                except Exception:
                    result[code] = []
            return result

        rows_by_instrument: dict[QuoteRef, tuple[DailyBar, ...]] = {}
        audits: dict[QuoteRef, ItemAudit] = {}
        errors: list[MarketSourceError] = []
        for reference in request.instruments:
            quote = {
                "code": reference.code,
                "exchange": reference.exchange or infer_exchange(reference.code),
            }
            try:
                rows = cast(
                    list[Mapping[str, object]],
                    market_data._fetch_kline(
                        "tushare",
                        quote,
                        self.settings,
                        start_date=request.start_date,
                        end_date=request.end_date,
                    ),
                )
            except Exception:
                error = MarketSourceError(
                    operation=MarketSourceOperation.DAILY_BARS,
                    code=MarketSourceErrorCode.INSTRUMENT_UNAVAILABLE,
                    message="tushare daily-bar retrieval failed",
                    reference=reference,
                )
                if request.failure_policy is FailurePolicy.RAISE:
                    raise MarketSourceRequestFailed(error) from None
                errors.append(error)
                rows_by_instrument[reference] = ()
                audits[reference] = ItemAudit(reference, BatchStatus.FAILURE, 0, error)
                continue

            bars = tuple(DailyBar.from_row(reference, row) for row in rows)
            rows_by_instrument[reference] = bars
            status = BatchStatus.SUCCESS if bars else BatchStatus.EMPTY
            audits[reference] = ItemAudit(reference, status, len(bars))

        returned_count = sum(len(bars) for bars in rows_by_instrument.values())
        failed_count = len(errors)
        empty_count = sum(1 for audit in audits.values() if audit.status is BatchStatus.EMPTY)
        audit = MarketSourceAudit(
            source="tushare",
            operation=MarketSourceOperation.DAILY_BARS,
            failure_policy=request.failure_policy,
            status=classify_status(returned_count, failed_count),
            requested_count=len(request.instruments),
            returned_count=returned_count,
            empty_count=empty_count,
            failed_count=failed_count,
        )
        return DailyBarBatch(
            bars_by_instrument=MappingProxyType(rows_by_instrument),
            item_audits=MappingProxyType(audits),
            errors=tuple(errors),
            audit=audit,
        )

    def fetch_bars_for_quote(
        self,
        quote: Mapping[str, object],
        settings: object | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, object]]:
        """Deprecated market-pool compatibility shim; not part of MarketSourcePort."""

        return cast(
            list[dict[str, object]],
            market_data._fetch_kline(
                "tushare", dict(quote), settings if settings is not None else self.settings,
                start_date=start_date, end_date=end_date
            ),
        )

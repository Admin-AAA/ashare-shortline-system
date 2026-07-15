"""Deterministic in-memory implementation of the public market-source Port."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Sequence

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
)


class InMemoryMarketSourceAdapter:
    """Fixture-backed adapter that performs no network or database access.

    Typed request objects return typed batches.  The list/no-request forms and
    ``fetch_bars_for_quote`` exist only so the unmodified market-pool seam can
    keep using this fake until its own migration is permitted.
    """

    def __init__(
        self,
        quotes: Sequence[Mapping[str, object]] | None = None,
        bars_by_code: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
        failing_codes: Sequence[str] | None = None,
    ) -> None:
        self.quotes = tuple(dict(quote) for quote in (quotes or ()))
        self.bars_by_code = {
            str(code): tuple(dict(bar) for bar in bars)
            for code, bars in (bars_by_code or {}).items()
        }
        self.failing_codes = frozenset(str(code) for code in (failing_codes or ()))

    def fetch_quotes(
        self, request: QuoteRequest | QuoteScope | None = None
    ) -> QuoteBatch | list[dict[str, object]]:
        if not isinstance(request, QuoteRequest):
            limit = request.max_quote_count if isinstance(request, QuoteScope) else None
            rows = self.quotes if limit is None else self.quotes[:limit]
            return [dict(row) for row in rows]

        rows = self.quotes
        if request.max_quote_count is not None:
            rows = rows[: request.max_quote_count]
        quotes = tuple(Quote.from_row(row) for row in rows)
        audit = MarketSourceAudit(
            source="in_memory",
            operation=MarketSourceOperation.QUOTES,
            failure_policy=request.failure_policy,
            status=classify_status(len(quotes), 0),
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
            return {
                str(code): [] if str(code) in self.failing_codes else [dict(row) for row in self.bars_by_code.get(str(code), ())]
                for code in request
            }

        rows_by_instrument: dict[QuoteRef, tuple[DailyBar, ...]] = {}
        audits: dict[QuoteRef, ItemAudit] = {}
        errors: list[MarketSourceError] = []
        for reference in request.instruments:
            if reference.code in self.failing_codes:
                error = MarketSourceError(
                    operation=MarketSourceOperation.DAILY_BARS,
                    code=MarketSourceErrorCode.INSTRUMENT_UNAVAILABLE,
                    message="in-memory daily-bar fixture configured to fail",
                    reference=reference,
                    retryable=False,
                )
                if request.failure_policy is FailurePolicy.RAISE:
                    raise MarketSourceRequestFailed(error)
                errors.append(error)
                rows_by_instrument[reference] = ()
                audits[reference] = ItemAudit(reference, BatchStatus.FAILURE, 0, error)
                continue

            bars = tuple(
                DailyBar.from_row(reference, row)
                for row in self.bars_by_code.get(reference.code, ())
            )
            rows_by_instrument[reference] = bars
            status = BatchStatus.SUCCESS if bars else BatchStatus.EMPTY
            audits[reference] = ItemAudit(reference, status, len(bars))

        returned_count = sum(len(bars) for bars in rows_by_instrument.values())
        failed_count = len(errors)
        empty_count = sum(1 for audit in audits.values() if audit.status is BatchStatus.EMPTY)
        audit = MarketSourceAudit(
            source="in_memory",
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

        code = str(quote.get("code") or "")
        if not code or code in self.failing_codes:
            return []
        return [dict(row) for row in self.bars_by_code.get(code, ())]

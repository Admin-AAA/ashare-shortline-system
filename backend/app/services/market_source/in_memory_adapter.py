"""Deterministic in-memory implementation of the closed typed market-source Port."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Sequence

from app.services.market_source.port import (
    BatchStatus,
    DailyBar,
    DailyBarBatch,
    DailyBarRequest,
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
    classify_status,
)


class InMemoryMarketSourceAdapter:
    """Fixture-backed adapter with only the two public Port operations.

    It performs no network or database access.  ``failing_codes`` models
    per-instrument retrieval failures so tests can exercise empty, partial,
    fatal, and ``FailurePolicy.RAISE`` semantics through the real DTOs.
    """

    source_name = "in_memory"

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

    def fetch_quotes(self, request: QuoteRequest) -> QuoteBatch:
        rows = self.quotes
        if request.max_quote_count is not None:
            rows = rows[: request.max_quote_count]
        quotes = tuple(Quote.from_row(row) for row in rows)
        return QuoteBatch(
            quotes=quotes,
            errors=(),
            audit=MarketSourceAudit(
                source=self.source_name,
                operation=MarketSourceOperation.QUOTES,
                failure_policy=request.failure_policy,
                status=classify_status(len(quotes), 0),
                requested_count=1,
                returned_count=len(quotes),
                empty_count=1 if not quotes else 0,
                failed_count=0,
            ),
        )

    def fetch_daily_bars(self, request: DailyBarRequest) -> DailyBarBatch:
        requested_references = tuple(request.instruments)
        references = requested_references
        if request.is_trade_date_batch:
            extra_references = tuple(
                QuoteRef(code)
                for code in self.bars_by_code
                if code not in {reference.code for reference in requested_references}
            )
            references = requested_references + extra_references

        rows_by_instrument: dict[QuoteRef, tuple[DailyBar, ...]] = {}
        all_audits: dict[QuoteRef, ItemAudit] = {}
        errors: list[MarketSourceError] = []
        for reference in references:
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
                all_audits[reference] = ItemAudit(reference, BatchStatus.FAILURE, 0, error)
                continue

            bars = tuple(
                DailyBar.from_row(reference, row)
                for row in self.bars_by_code.get(reference.code, ())
            )
            rows_by_instrument[reference] = bars
            all_audits[reference] = ItemAudit(
                reference,
                BatchStatus.SUCCESS if bars else BatchStatus.EMPTY,
                len(bars),
            )

        item_audits = {
            reference: all_audits[reference]
            for reference in requested_references
        }
        returned_count = sum(len(bars) for bars in rows_by_instrument.values())
        failed_count = len(errors)
        return DailyBarBatch(
            bars_by_instrument=MappingProxyType(rows_by_instrument),
            item_audits=MappingProxyType(item_audits),
            errors=tuple(errors),
            audit=MarketSourceAudit(
                source=self.source_name,
                operation=MarketSourceOperation.DAILY_BARS,
                failure_policy=request.failure_policy,
                status=classify_status(returned_count, failed_count),
                requested_count=len(requested_references),
                returned_count=returned_count,
                empty_count=sum(
                    1 for item_audit in item_audits.values() if item_audit.status is BatchStatus.EMPTY
                ),
                failed_count=failed_count,
            ),
        )

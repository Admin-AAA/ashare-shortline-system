"""Tushare-backed implementation of the closed typed market-source contract."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, cast

from app.services import market_data
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
    infer_exchange,
)


class TushareMarketSourceAdapter:
    """Production implementation of the two-method :class:`MarketSourcePort`.

    Provider loader details remain inside this adapter.  Callers only receive
    typed request/result DTOs and must inspect returned audit evidence rather
    than relying on provider exceptions for normal partial/failure outcomes.
    """

    source_name = "tushare"

    def __init__(self, settings: object | None = None) -> None:
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        self.settings = settings

    def fetch_quotes(self, request: QuoteRequest) -> QuoteBatch:
        try:
            rows = cast(
                list[Mapping[str, object]],
                market_data._fetch_quotes(self.source_name, self.settings),
            )
        except Exception:  # noqa: BLE001 - re-raised/wrapped
            error = MarketSourceError(
                operation=MarketSourceOperation.QUOTES,
                code=MarketSourceErrorCode.SOURCE_UNAVAILABLE,
                message="tushare quote retrieval failed",
            )
            if request.failure_policy is FailurePolicy.RAISE:
                raise MarketSourceRequestFailed(error) from None
            return QuoteBatch(
                quotes=(),
                errors=(error,),
                audit=MarketSourceAudit(
                    source=self.source_name,
                    operation=MarketSourceOperation.QUOTES,
                    failure_policy=request.failure_policy,
                    status=BatchStatus.FAILURE,
                    requested_count=1,
                    returned_count=0,
                    empty_count=0,
                    failed_count=1,
                ),
            )

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
        if request.is_trade_date_batch:
            return self._fetch_trade_date_batch(request)

        rows_by_instrument: dict[QuoteRef, tuple[DailyBar, ...]] = {}
        audits: dict[QuoteRef, ItemAudit] = {}
        errors: list[MarketSourceError] = []
        for reference in request.instruments:
            provider_quote = {
                "code": reference.code,
                "exchange": reference.exchange or infer_exchange(reference.code),
            }
            try:
                rows = cast(
                    list[Mapping[str, object]],
                    market_data._fetch_kline(
                        self.source_name,
                        provider_quote,
                        self.settings,
                        start_date=request.start_date,
                        end_date=request.end_date,
                    ),
                )
            except Exception:  # noqa: BLE001 - re-raised/wrapped
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
            audits[reference] = ItemAudit(
                reference,
                BatchStatus.SUCCESS if bars else BatchStatus.EMPTY,
                len(bars),
            )

        returned_count = sum(len(bars) for bars in rows_by_instrument.values())
        failed_count = len(errors)
        return DailyBarBatch(
            bars_by_instrument=MappingProxyType(rows_by_instrument),
            item_audits=MappingProxyType(audits),
            errors=tuple(errors),
            audit=MarketSourceAudit(
                source=self.source_name,
                operation=MarketSourceOperation.DAILY_BARS,
                failure_policy=request.failure_policy,
                status=classify_status(returned_count, failed_count),
                requested_count=len(request.instruments),
                returned_count=returned_count,
                empty_count=sum(
                    1 for item_audit in audits.values() if item_audit.status is BatchStatus.EMPTY
                ),
                failed_count=failed_count,
            ),
        )

    def _fetch_trade_date_batch(self, request: DailyBarRequest) -> DailyBarBatch:
        """Fetch a market-day batch once while retaining typed target audits."""
        from app.services.tushare_client import fetch_daily_kbars_for_trade_date

        requested_by_code = {reference.code: reference for reference in request.instruments}
        try:
            source_rows = cast(
                list[Mapping[str, object]],
                fetch_daily_kbars_for_trade_date(str(request.trade_date)),
            )
        except Exception:  # noqa: BLE001 - re-raised/wrapped
            error = MarketSourceError(
                operation=MarketSourceOperation.DAILY_BARS,
                code=MarketSourceErrorCode.SOURCE_UNAVAILABLE,
                message="tushare trade-date daily-bar retrieval failed",
            )
            if request.failure_policy is FailurePolicy.RAISE:
                raise MarketSourceRequestFailed(error) from None
            failures = tuple(
                MarketSourceError(
                    operation=error.operation,
                    code=error.code,
                    message=error.message,
                    reference=reference,
                )
                for reference in request.instruments
            ) or (error,)
            audits = {
                reference: ItemAudit(reference, BatchStatus.FAILURE, 0, item_error)
                for reference, item_error in zip(request.instruments, failures)
            }
            return DailyBarBatch(
                bars_by_instrument=MappingProxyType({reference: () for reference in request.instruments}),
                item_audits=MappingProxyType(audits),
                errors=failures,
                audit=MarketSourceAudit(
                    source=self.source_name,
                    operation=MarketSourceOperation.DAILY_BARS,
                    failure_policy=request.failure_policy,
                    status=BatchStatus.FAILURE,
                    requested_count=len(request.instruments),
                    returned_count=0,
                    empty_count=0,
                    failed_count=len(failures),
                ),
            )

        grouped: dict[QuoteRef, list[DailyBar]] = {}
        for row in source_rows:
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            reference = requested_by_code.get(code) or QuoteRef(code, infer_exchange(code))
            grouped.setdefault(reference, []).append(DailyBar.from_row(reference, row))
        for reference in request.instruments:
            grouped.setdefault(reference, [])

        frozen_groups = {reference: tuple(bars) for reference, bars in grouped.items()}
        audits = {
            reference: ItemAudit(
                reference=reference,
                status=BatchStatus.SUCCESS if frozen_groups.get(reference) else BatchStatus.EMPTY,
                returned_count=len(frozen_groups.get(reference, ())),
            )
            for reference in request.instruments
        }
        returned_count = sum(len(bars) for bars in frozen_groups.values())
        return DailyBarBatch(
            bars_by_instrument=MappingProxyType(frozen_groups),
            item_audits=MappingProxyType(audits),
            errors=(),
            audit=MarketSourceAudit(
                source=self.source_name,
                operation=MarketSourceOperation.DAILY_BARS,
                failure_policy=request.failure_policy,
                status=classify_status(returned_count, 0),
                requested_count=len(request.instruments),
                returned_count=returned_count,
                empty_count=sum(
                    1 for item_audit in audits.values() if item_audit.status is BatchStatus.EMPTY
                ),
                failed_count=0,
            ),
        )

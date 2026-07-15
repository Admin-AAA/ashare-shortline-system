"""Production adapters over the existing local market-pool read path.

The adapter deliberately delegates refresh, cache preference and realtime merge to
existing workbench functions.  It adds typed request/result boundaries and audit
records; it does not change provider choice, row shaping or persistence behaviour.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import DailyKbar
from app.services.registry.data_source import (
    DATA_SOURCE_LOCAL_POOL,
    DATA_SOURCE_LOCAL_POOL_PREFERRED,
    DATA_SOURCE_LOCAL_POOL_REALTIME,
    DATA_SOURCE_REALTIME,
)

from .port import (
    BatchStatus,
    DailyBar,
    DailyBarBatch,
    DailyBarRequest,
    FailurePolicy,
    ItemAudit,
    MarketSourceAudit,
    MarketSourceOperation,
    Quote,
    QuoteBatch,
    QuoteRef,
    QuoteRequest,
    classify_status,
)


class LocalPoolMarketSourceAdapter:
    """``MarketSourcePort`` adapter for local-pool quote/bar reads.

    ``last_snapshot`` is intentionally adapter-specific: the legacy workbench needs
    its rich snapshot unchanged, while the narrow public port carries only quote
    rows and transport audit data.
    """

    source_name = "local_pool"

    def __init__(
        self,
        db: Session,
        *,
        refresh: bool = False,
        as_of_date: str | None = None,
        include_realtime: bool = False,
    ) -> None:
        self._db = db
        self._refresh = refresh
        self._as_of_date = as_of_date
        self._include_realtime = include_realtime
        self.last_snapshot: dict[str, Any] = {}

    def fetch_quotes(self, request: QuoteRequest) -> QuoteBatch:
        # Delayed imports avoid a legacy import cycle:
        # market_data -> candidates -> market_source.snapshot -> this package.
        from app.services.market_pool import (
            get_market_universe_from_pool,
            get_market_universe_from_pool_with_realtime,
        )

        if self._include_realtime:
            rows, snapshot = get_market_universe_from_pool_with_realtime(self._db)
            source = "local_pool_realtime"
        else:
            rows, snapshot = get_market_universe_from_pool(
                self._db,
                refresh=self._refresh,
                as_of_date=self._as_of_date,
            )
            source = "local_pool_preferred" if self._refresh else self.source_name
        if request.max_quote_count is not None:
            rows = rows[: request.max_quote_count]
        self.last_snapshot = dict(snapshot)
        quotes = tuple(Quote.from_row(row) for row in rows)
        audit = MarketSourceAudit(
            source=source,
            operation=MarketSourceOperation.QUOTES,
            failure_policy=request.failure_policy,
            status=classify_status(len(quotes), 0),
            requested_count=request.max_quote_count or len(quotes),
            returned_count=len(quotes),
            empty_count=0 if quotes else 1,
            failed_count=0,
        )
        return QuoteBatch(quotes=quotes, errors=(), audit=audit)

    def fetch_daily_bars(self, request: DailyBarRequest) -> DailyBarBatch:
        references = tuple(request.instruments)
        by_code = {reference.code: reference for reference in references}
        statement = select(DailyKbar).where(DailyKbar.code.in_(tuple(by_code)))
        if request.start_date:
            statement = statement.where(DailyKbar.trade_date >= request.start_date)
        if request.end_date:
            statement = statement.where(DailyKbar.trade_date <= request.end_date)
        statement = statement.order_by(DailyKbar.code, DailyKbar.trade_date)
        grouped: dict[QuoteRef, list[DailyBar]] = defaultdict(list)
        for row in self._db.scalars(statement).all():
            reference = by_code[str(row.code)]
            grouped[reference].append(
                DailyBar.from_row(
                    reference,
                    {
                        "code": row.code,
                        "trade_date": row.trade_date,
                        "open": row.open,
                        "high": row.high,
                        "low": row.low,
                        "close": row.close,
                        "volume": row.volume,
                        "amount": row.amount,
                        "pct_chg": row.pct_chg,
                        "turnover_rate": row.turnover_rate,
                        "source": row.source,
                    },
                )
            )
        frozen_groups = {reference: tuple(grouped.get(reference, ())) for reference in references}
        item_audits = {
            reference: ItemAudit(
                reference=reference,
                status=BatchStatus.SUCCESS if frozen_groups[reference] else BatchStatus.EMPTY,
                returned_count=len(frozen_groups[reference]),
            )
            for reference in references
        }
        returned = sum(len(items) for items in frozen_groups.values())
        audit = MarketSourceAudit(
            source=self.source_name,
            operation=MarketSourceOperation.DAILY_BARS,
            failure_policy=request.failure_policy,
            status=classify_status(returned, 0),
            requested_count=len(references),
            returned_count=returned,
            empty_count=sum(1 for items in frozen_groups.values() if not items),
            failed_count=0,
        )
        return DailyBarBatch(
            bars_by_instrument=frozen_groups,
            item_audits=item_audits,
            errors=(),
            audit=audit,
        )


def load_legacy_market_universe(
    db: Session,
    *,
    data_source: str,
    as_of_date: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Load legacy workbench rows through a typed audit facade.

    Returns the existing row/snapshot shapes exactly, followed by a JSON-safe typed
    audit projection.  Realtime remains delegated to its established implementation.
    """
    if data_source == DATA_SOURCE_REALTIME:
        from app.services.market_data import get_market_universe

        rows, snapshot = get_market_universe()
        source = "realtime"
        quotes = tuple(Quote.from_row(row) for row in rows)
        audit = MarketSourceAudit(
            source=source,
            operation=MarketSourceOperation.QUOTES,
            failure_policy=FailurePolicy.RETURN_PARTIAL,
            status=classify_status(len(quotes), 0),
            requested_count=len(quotes),
            returned_count=len(quotes),
            empty_count=0 if quotes else 1,
            failed_count=0,
        )
    else:
        adapter = LocalPoolMarketSourceAdapter(
            db,
            refresh=data_source == DATA_SOURCE_LOCAL_POOL_PREFERRED,
            as_of_date=as_of_date,
            include_realtime=data_source == DATA_SOURCE_LOCAL_POOL_REALTIME,
        )
        batch = adapter.fetch_quotes(QuoteRequest())
        rows = batch.as_legacy_rows()
        snapshot = adapter.last_snapshot
        audit = batch.audit
    return rows, snapshot, {
        "source": audit.source,
        "operation": audit.operation.value,
        "status": audit.status.value,
        "requested_count": audit.requested_count,
        "returned_count": audit.returned_count,
        "empty_count": audit.empty_count,
        "failed_count": audit.failed_count,
        "failure_policy": audit.failure_policy.value,
    }


__all__ = ["LocalPoolMarketSourceAdapter", "load_legacy_market_universe"]

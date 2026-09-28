"""Xiaoshi normalization into the local market-data row contract."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
)
from app.services.xiaoshi_client import XiaoshiClient


def _code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return raw.split(".", 1)[0]


def _exchange(code: str, value: Any = None) -> str:
    explicit = str(value or "").strip().upper()
    if explicit in {"SZ", "SH", "BJ"}:
        return explicit
    if code.startswith(("4", "8")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_only(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw[:10]


def _is_st(name: str) -> bool:
    return "ST" in name.upper() or "退" in name


def _snapshot_rows(payload: Mapping[str, Any], stocks: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    default_date = _date_only(payload.get("updated_at"))
    for raw in payload.get("items") or []:
        if not isinstance(raw, Mapping):
            continue
        code = _code(raw.get("code") or raw.get("symbol"))
        if not code:
            continue
        stock = stocks.get(code, {})
        name = str(raw.get("name") or stock.get("name") or code).strip() or code
        trade_date = _date_only(raw.get("quote_time") or payload.get("updated_at")) or default_date
        price = _number(raw.get("price"))
        previous_close = _number(raw.get("last_close") or raw.get("previous_close"))
        pct = _number(raw.get("change_pct"), _number(raw.get("pct")))
        rows.append(
            {
                "code": code,
                "name": name,
                "exchange": _exchange(code, raw.get("exchange") or stock.get("exchange")),
                "industry": str(stock.get("industry") or "未分类").strip() or "未分类",
                "trade_date": trade_date,
                "price": price,
                "pct": pct,
                "volume": _number(raw.get("volume")),
                "amount_yi": _number(raw.get("amount")) / 100_000_000,
                "turnover_rate": _number(raw.get("turnover_pct") or raw.get("turnover_rate")),
                "pe_ttm": _number(raw.get("pe_ttm")),
                "total_mv_yi": _number(raw.get("mcap_yi")),
                "free_mv_yi": _number(raw.get("float_mcap_yi")),
                "high": _number(raw.get("high")),
                "low": _number(raw.get("low")),
                "open": _number(raw.get("open")),
                "preclose": previous_close,
                "chg60": 0.0,
                "ytd_chg": 0.0,
                "main_net_inflow_yi": 0.0,
                "is_st": _is_st(name),
                "source": "xiaoshi",
                "quote_time": raw.get("quote_time") or raw.get("time") or payload.get("updated_at"),
            }
        )
    return rows


def _stock_map(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in payload.get("items") or []:
        if not isinstance(raw, Mapping):
            continue
        code = _code(raw.get("code") or raw.get("symbol"))
        if code:
            result[code] = dict(raw)
    return result


class XiaoshiMarketSourceAdapter:
    """Typed market-source adapter backed by Xiaoshi's v3 API."""

    source_name = "xiaoshi"

    def __init__(
        self,
        settings: object | None = None,
        *,
        client: XiaoshiClient | None = None,
    ) -> None:
        if client is None:
            if settings is None:
                from app.services.xiaoshi_client import get_xiaoshi_client

                client = get_xiaoshi_client()
            else:
                client = XiaoshiClient(
                    base_url=str(getattr(settings, "xiaoshi_api_base_url", "https://api.shizixi.com")),
                    api_key=str(getattr(settings, "xiaoshi_api_key", "")),
                    max_qps=int(getattr(settings, "xiaoshi_max_qps", 30)),
                    timeout_seconds=float(getattr(settings, "xiaoshi_timeout_seconds", 30.0)),
                    max_attempts=int(getattr(settings, "xiaoshi_max_attempts", 2)),
                )
        self.client = client

    def fetch_quotes(self, request: Any) -> QuoteBatch:
        try:
            stock_map = _stock_map(self.client.get_stocks(market="CN", limit=6000))
            payload = self.client.get_market_snapshot(market="CN", limit=6000)
            coverage = _number(payload.get("coverage"), 1.0)
            if payload.get("partial") is True or coverage < 0.95:
                raise ValueError("xiaoshi full-market snapshot is partial")
            rows = _snapshot_rows(payload, stock_map)
        except Exception:
            error = MarketSourceError(
                operation=MarketSourceOperation.QUOTES,
                code=MarketSourceErrorCode.SOURCE_UNAVAILABLE,
                message="xiaoshi quote retrieval failed",
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

        max_count = getattr(request, "max_quote_count", None)
        if max_count is not None:
            rows = rows[:max_count]
        quotes = tuple(Quote.from_row(row) for row in rows)
        return QuoteBatch(
            quotes=quotes,
            errors=(),
            audit=MarketSourceAudit(
                source=self.source_name,
                operation=MarketSourceOperation.QUOTES,
                failure_policy=request.failure_policy,
                status=BatchStatus.SUCCESS if quotes else BatchStatus.EMPTY,
                requested_count=1,
                returned_count=len(quotes),
                empty_count=1 if not quotes else 0,
                failed_count=0,
            ),
        )

    def fetch_daily_bars(self, request: DailyBarRequest) -> DailyBarBatch:
        """小石 v3 不提供逐标的日线；历史只按 manifest + R2 下载契约批量交付。

        平台自述 `/api/v3/meta/data-contract` 的 `history` 段只给 manifest 与
        download-session 两个入口，`stability.legacy_aliases = false` 明确不保留旧
        别名，原先调用的 `/api/v3/data/kline/{code}` 已随 `/api/v3/data/*` 整族退役，
        逐股票请求恒 404。与其对每个标的重试一个不存在的地址，这里如实声明能力缺口
        并指明替代路径，让调用方回落到其他日线源（L-149）。
        """
        references = request.instruments
        error = MarketSourceError(
            operation=MarketSourceOperation.DAILY_BARS,
            code=MarketSourceErrorCode.SOURCE_UNAVAILABLE,
            message=(
                "xiaoshi v3 does not serve per-instrument daily bars; "
                "use the history manifest and download-session contract for bulk history"
            ),
        )
        if request.failure_policy is FailurePolicy.RAISE:
            raise MarketSourceRequestFailed(error)
        item_errors = tuple(
            MarketSourceError(
                operation=error.operation,
                code=error.code,
                message=error.message,
                reference=reference,
            )
            for reference in references
        )
        return DailyBarBatch(
            bars_by_instrument={reference: () for reference in references},
            item_audits={
                reference: ItemAudit(reference, BatchStatus.FAILURE, 0, item_error)
                for reference, item_error in zip(references, item_errors)
            },
            errors=item_errors or (error,),
            audit=MarketSourceAudit(
                source=self.source_name,
                operation=MarketSourceOperation.DAILY_BARS,
                failure_policy=request.failure_policy,
                status=BatchStatus.FAILURE,
                requested_count=len(references),
                returned_count=0,
                empty_count=0,
                failed_count=max(1, len(item_errors)),
            ),
        )

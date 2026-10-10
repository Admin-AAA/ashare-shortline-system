"""Xiaoshi normalization into the local market-data row contract."""
from __future__ import annotations

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
    QuoteBatch,
    QuoteRef,
)
from app.services.xiaoshi_client import XiaoshiClient


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
        # QuoteRequest 要的是完整市场快照，xiaoshi v3 公开操作清单里没有能满足它的端点：
        # `POST /api/v3/market/quotes` 只用于小批量行情，按全市场代码逐批枚举会被平台判为
        # `bulk_download_required` 并暂停该 key 的实时报价 24 小时；平台在该错误里指引的
        # `/api/v3/data/market-snapshot` 又返回 `operation_not_in_public_contract`（404）。
        # 因此这里如实声明能力缺失，让调用方切换到 tushare，不去尝试会触发封禁的枚举。
        error = MarketSourceError(
            operation=MarketSourceOperation.QUOTES,
            code=MarketSourceErrorCode.SOURCE_UNAVAILABLE,
            message=(
                "xiaoshi v3 公开接口没有全市场报价端点（market/quotes 仅限小批量，全市场枚举会被暂停 24 小时）；"
                "全市场报价请走 tushare"
            ),
        )
        if request.failure_policy is FailurePolicy.RAISE:
            raise MarketSourceRequestFailed(error)
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

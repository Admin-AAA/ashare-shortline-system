"""测试用行情采集 adapter：持有预置夹具数据，实现 `MarketSourcePort`（任务 4.2）。

不发起任何网络/Tushare 调用——`fetch_quotes`/`fetch_daily_bars` 只从构造时
传入的夹具字典里取数据，供单测替换生产 `TushareMarketSourceAdapter` 使用。
"""
from __future__ import annotations

from typing import Any, Sequence

from app.services.market_source.port import DateRange, QuoteScope


class InMemoryMarketSourceAdapter:
    """夹具驱动的 `MarketSourcePort` 实现，用于测试。

    构造参数：
        quotes: 预置的报价快照列表（`fetch_quotes` 的返回值来源）。
        bars_by_code: `{code: bars}`，`fetch_daily_bars` 按 code 查表返回。
        failing_codes: 可选，模拟"单只股票拉取失败"——`fetch_daily_bars`
            对这些 code 返回空列表 `[]`（对齐 `MarketSourcePort.fetch_daily_bars`
            docstring 里"某只股票取不到数据时对应 value 应为空列表"的降级
            语义），而不是从 `bars_by_code` 里查表。
    """

    def __init__(
        self,
        quotes: list[dict[str, Any]] | None = None,
        bars_by_code: dict[str, list[dict[str, Any]]] | None = None,
        failing_codes: Sequence[str] | None = None,
    ) -> None:
        self._quotes = list(quotes or [])
        self._bars_by_code = dict(bars_by_code or {})
        self._failing_codes = set(failing_codes or ())

    def fetch_quotes(self, scope: QuoteScope | None = None) -> list[dict[str, Any]]:
        """返回预置报价快照；`scope.max_quote_count` 时截断前 N 条。

        对齐生产 adapter 与现状 `_fetch_tushare_quotes` 的切片语义
        （`realtime_quotes[:settings.market_scan_size]`），`scope=None`
        或 `max_quote_count=None` 时不截断，返回全部预置数据。
        """
        if scope is not None and scope.max_quote_count is not None:
            return list(self._quotes[: scope.max_quote_count])
        return list(self._quotes)

    def fetch_daily_bars(
        self, codes: Sequence[str], date_range: DateRange | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """按 code 查表返回预置日K；`failing_codes` 命中时返回空列表。

        `date_range` 当前不参与夹具过滤——夹具数据本身就是测试用例按需
        准备好的固定区间，没有必要在内存实现里重新实现一遍按日期裁剪的逻辑；
        需要验证"调用方传了什么 date_range"的用例，应直接对 adapter 实例
        的调用记录做断言，而不是依赖本方法按 date_range 过滤返回值。
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for code in codes:
            if code in self._failing_codes:
                result[code] = []
                continue
            result[code] = list(self._bars_by_code.get(code, []))
        return result

    def fetch_bars_for_quote(
        self,
        quote: dict[str, Any],
        settings: Any = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """兼容出口：按完整 quote 字典查表返回预置日K（任务 4.4）。

        不是 `MarketSourcePort` 协议方法——`market_pool_adapters.fetch_kline_rows`
        现状收到的是调用方（`market_pool._refresh_bars_for_quote`）手上
        **完整**的 quote 字典（不只是 code），生产 `TushareMarketSourceAdapter`
        为此单独暴露了同名兼容方法（用于兜底 `turnover_rate` 等真实字段，见
        `multi_source_adapter.py` 的详细说明）。本内存实现的测试夹具数据
        本身就是按 `quote["code"]` 准备好的固定结果，不需要重现那层
        `turnover_rate` 兜底细节，所以直接按 `quote["code"]` 查表，忽略
        `settings`/`start_date`/`end_date`——这几个参数只是为了与生产
        adapter 保持同一签名，方便两者互换。

        对齐 `fetch_daily_bars` 的降级语义：`failing_codes` 命中或查表未命中
        时返回空列表 `[]`，不抛异常。
        """
        code = str(quote.get("code") or "")
        if not code or code in self._failing_codes:
            return []
        return list(self._bars_by_code.get(code, []))

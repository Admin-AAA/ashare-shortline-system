"""生产行情采集 adapter：包装 `market_data.py` 现有的 tushare 采集实现为
`MarketSourcePort`（任务 4.2）。

## 现状定位（与 port.py 保持一致，不重新核实）
`market_data.py` 目前只有 tushare 一个源：`_quote_sources`/`_kline_sources`
硬编码返回 `["tushare"]`，`_fetch_quotes`/`_fetch_kline` 内部
`if source == "tushare": ... else: raise MarketDataError(...)`（见
`port.py` docstring 的详细核实笔记）。本文件类名用
`TushareMarketSourceAdapter` 而不是 `MultiSourceAdapter`，是为了不用一个
暗示"已经多源"的名字掩盖这一事实——它当前就是单源实现，只是包装成 port
形状；`multi_source_adapter` 是本任务在**模块文件名**上沿用设计文档 4.2
既定叫法（给未来接入第二个源留位置），模块内部的类名如实反映现状。如果
未来真的要接第二个源，回退顺序应该在这个 adapter 内部实现（比如加一个
`EastmoneyMarketSourceAdapter` 兄弟类，再在一个组合 adapter 里做顺序尝试），
port 协议与 `market_pool_adapters.py` 的调用方都不需要跟着变。

## 实现方式：委托，不复制
`fetch_quotes`/`fetch_daily_bars` 都是对 `market_data.py` 现有函数
（`_fetch_quotes` → `_fetch_tushare_quotes`；`_fetch_kline` →
`_fetch_tushare_kline`）的委托调用，没有复制/重写任何采集逻辑——行为与
迁移前 `market_pool_adapters.py` 对 `market_data._fetch_quotes`/`_fetch_kline`
的直转发完全一致，只是调用入口从"裸函数"换成了"port 方法"。
"""
from __future__ import annotations

from typing import Any, Sequence

from app.services import market_data
from app.services.market_source.port import DateRange, QuoteScope


class _ScopedScanSizeSettings:
    """把 `QuoteScope.max_quote_count` 映射为一次性覆盖的 `market_scan_size`。

    现状 `_fetch_tushare_quotes` 只在盘中 realtime 报价路径末尾读取
    `settings.market_scan_size` 做切片（`return realtime_quotes[:
    settings.market_scan_size]`）；收盘后走批量日线的分支完全不做这个截断
    （见 `port.py` `QuoteScope` docstring）。这里如实保留这一局限——不去
    伪造一个"批量路径也生效"的截断，只是把 `settings.market_scan_size`
    换成调用方传入的 `scope.max_quote_count`，其余属性透明代理到原始
    settings，不改变除这一个字段外的任何配置。
    """

    def __init__(self, base_settings: Any, market_scan_size: int) -> None:
        self._base = base_settings
        self.market_scan_size = market_scan_size

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class TushareMarketSourceAdapter:
    """生产行情采集 adapter：委托 `market_data.py` 的 tushare 单源实现。

    结构上满足 `MarketSourcePort`（`runtime_checkable` Protocol，鸭子类型
    即可，不需要显式继承）。构造时注入配置（`settings`），符合 ports &
    adapters 的目标形态——调用方每次调用只传领域参数
    （`scope`/`codes`/`date_range`），不需要每次都带 `settings`
    （对齐 `port.py` docstring 里"配置收进 adapter 构造函数"的设计理由）。
    `settings=None` 时，每次调用前惰性调用 `app.core.config.get_settings()`
    取全局配置，而不是在构造时就固化一份（避免持有一个过期的 settings 快照）。
    """

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings

    def _resolve_settings(self) -> Any:
        if self._settings is not None:
            return self._settings
        from app.core.config import get_settings

        return get_settings()

    def fetch_quotes(self, scope: QuoteScope | None = None) -> list[dict[str, Any]]:
        """委托 `market_data._fetch_quotes("tushare", settings)`。

        `scope.max_quote_count` 通过 `_ScopedScanSizeSettings` 映射为一次性
        覆盖的 `market_scan_size`——如实保留现状"只在盘中 realtime 报价路径
        生效"的局限，不在这里额外补一层"批量路径也截断"的行为（那不是
        现状，port.py `QuoteScope` docstring 已说明）。`scope=None` 时直接
        使用构造时注入（或惰性解析）的 settings，行为与迁移前
        `market_pool_adapters.fetch_quote_rows("tushare", settings)` 完全
        一致。
        """
        settings = self._resolve_settings()
        if scope is not None and scope.max_quote_count is not None:
            settings = _ScopedScanSizeSettings(settings, scope.max_quote_count)
        return market_data._fetch_quotes("tushare", settings)

    def fetch_daily_bars(
        self, codes: Sequence[str], date_range: DateRange | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """批量拉取一组股票代码各自的日K序列（port 协议方法）。

        逐 code 现造一条只有 `code`/`exchange` 两个字段的最小 quote 字典
        （`exchange` 用 `market_data._exchange_from_code` 从 code 推出，
        对齐 `port.py` docstring 的理由：现状 `_fetch_tushare_kline` 内部
        只用得到这两个字段拼 `ts_code`），再委托 `fetch_bars_for_quote`。
        单只失败（抛异常）时该 value 为空列表 `[]`，不整体抛异常中断其它
        股票——对齐现状 `_fetch_first_kline` 逐股票独立降级的语义。
        """
        settings = self._resolve_settings()
        start_date = date_range.start_date if date_range is not None else None
        end_date = date_range.end_date if date_range is not None else None
        result: dict[str, list[dict[str, Any]]] = {}
        for code in codes:
            quote = {"code": code, "exchange": market_data._exchange_from_code(code)}
            try:
                result[code] = self.fetch_bars_for_quote(
                    quote, settings=settings, start_date=start_date, end_date=end_date
                )
            except Exception:  # noqa: BLE001 - 对齐现状逐股票独立降级，不中断其它 code
                result[code] = []
        return result

    def fetch_bars_for_quote(
        self,
        quote: dict[str, Any],
        settings: Any = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """按完整 quote 字典拉取单只股票日K。

        不是 `MarketSourcePort` 协议方法，是给
        `market_pool_adapters.fetch_kline_rows` 用的兼容出口，理由见下：

        `fetch_daily_bars`（port 协议方法）只有 `codes`，quote 字典是内部
        现造的（只有 code/exchange 两个字段）；而
        `market_pool_adapters.fetch_kline_rows` 现状签名收到的是调用方
        （`market_pool._refresh_bars_for_quote`）手上**完整**的 quote
        （带 `turnover_rate` 等字段）——`market_data._fetch_tushare_kline`
        末尾 `bars[-1]["turnover_rate"] = bars[-1]["turnover_rate"] or
        float(quote.get("turnover_rate") or 0)` 会用这个完整 quote 的
        `turnover_rate` 兜底最后一条 bar。如果把这个完整 quote 降级成只有
        code/exchange 的临时 quote 再走 `fetch_daily_bars`，就会丢失这个
        兜底信息，改变现状行为。所以单独保留这个"透传完整 quote"的委托出口；
        两个方法内部最终都落到同一处 `market_data._fetch_kline`，没有实现
        分叉，只是入参粒度不同。

        本方法不捕获异常——原样向上抛出，对齐迁移前
        `market_pool_adapters.fetch_kline_rows` 直转发时"异常不被吞掉，由
        `market_pool._refresh_bars_for_quote` 的 try/except 记录真实错误"
        的行为；`fetch_daily_bars` 的"单只失败降级为空列表"是在它自己的
        循环里做的，不是这里。
        """
        resolved_settings = settings if settings is not None else self._resolve_settings()
        return market_data._fetch_kline(
            "tushare", quote, resolved_settings, start_date=start_date, end_date=end_date
        )

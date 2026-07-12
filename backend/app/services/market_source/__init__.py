"""行情采集 port（ports & adapters）。

本包目前只放采集 seam 的**接口定义**（任务 4.1）。生产实现
（迁移自 `market_data.py` 的多源/回退逻辑）与测试用的 in-memory 实现
留给任务 4.2，`market_pool_adapters.py` 改为指向本 port 也是 4.2 的范围——
本任务不做任何迁移，只把接口面立好。

对外只暴露 `MarketSourcePort` 协议与它的两个方法用到的载体类型；
生产/测试实现类的名字（`multi_source_adapter` / `in_memory_adapter`）
不在本文件出现，避免过早耦合。
"""
from __future__ import annotations

from .port import DateRange, MarketSourcePort, QuoteScope

__all__ = ["MarketSourcePort", "DateRange", "QuoteScope"]

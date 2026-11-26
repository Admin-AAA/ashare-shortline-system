"""state adapters 绑定表里的每个数据集，TushareStateProvider 都必须有同名取数方法。

2026-11-26 事故：daily_basic 接入辅助数据管道时只加了读侧 `_StateViews.daily_basic`，
没加写侧 `TushareStateProvider.daily_basic`；`build_standard_state_adapters` 在组装阶段
`getattr(provider, "daily_basic")` 抛 AttributeError，21:00 晚间补数与 21:30 正式出票整条失败。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.services.auxiliary import state_adapters


def _binding_dataset_keys() -> list[str]:
    """从 build_standard_state_adapters 源码里解析 bindings 元组的数据集键。"""
    source = inspect.getsource(state_adapters.build_standard_state_adapters)
    tree = ast.parse(source)
    keys: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "fetch" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.append(arg.value)
    assert keys, "未解析到任何 fetch(<dataset>) 绑定"
    return keys


def test_every_bound_dataset_has_provider_fetcher() -> None:
    missing = [k for k in _binding_dataset_keys() if not callable(getattr(state_adapters.TushareStateProvider, k, None))]
    assert missing == [], f"TushareStateProvider 缺少取数方法: {missing}"


def test_every_bound_dataset_has_state_view_loader() -> None:
    missing = [k for k in _binding_dataset_keys() if not callable(getattr(state_adapters._StateViews, k, None))]
    assert missing == [], f"_StateViews 缺少读侧方法: {missing}"


def test_daily_basic_fetcher_writes_null_not_zero() -> None:
    import pandas as pd
    from datetime import date, datetime, timezone

    from app.services.auxiliary.contracts import AuxiliaryDatasetRequest, AuxiliaryInstrument

    class _Client:
        def daily_basic(self, **kwargs):
            return pd.DataFrame([
                {"ts_code": "000001.SZ", "trade_date": "20261126", "turnover_rate": 0.8, "pe_ttm": None, "total_mv": 2.5e7, "circ_mv": 2.4e7},
                {"ts_code": "T00001.BJ", "trade_date": "20261126", "turnover_rate": 1.0, "pe_ttm": 10.0, "total_mv": 1.0, "circ_mv": 1.0},
            ])

    provider = state_adapters.TushareStateProvider(_Client(), session=None)  # type: ignore[arg-type]
    rows = provider.daily_basic(AuxiliaryDatasetRequest(
        dataset_key="daily_basic",
        instruments=(AuxiliaryInstrument(code="000001"),),
        trade_date=date(2026, 11, 26),
        refresh_enabled=True,
        requested_at=datetime(2026, 11, 26, 13, 0, tzinfo=timezone.utc),
    ))
    assert [r["code"] for r in rows] == ["000001"]
    assert rows[0]["trade_date"] == "2026-11-26" and rows[0]["pe_ttm"] is None and rows[0]["total_mv"] == 2.5e7

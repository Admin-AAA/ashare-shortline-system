"""注册表取值错误（策略注册表唯一对外错误类型）。

预设（preset）管理层已于 2026-11-15 退役：正式推荐改为单策略独立 run
（standalone_strategies），注册表只校验策略本身。
"""
from __future__ import annotations

from collections.abc import Iterable


def _format_values(values: Iterable[str]) -> str:
    normalized = tuple(sorted({str(value) for value in values if str(value)}))
    return ", ".join(normalized) if normalized else "（无）"


class UnknownRegistryValueError(ValueError):
    """An explicitly requested strategy is not registered."""

    def __init__(self, kind: str, value: str, available_values: Iterable[str]) -> None:
        self.kind = str(kind)
        self.value = str(value)
        self.available_values = tuple(sorted({str(item) for item in available_values}))
        label = {"strategy": "策略"}.get(self.kind, self.kind)
        super().__init__(
            f"未知{label} '{self.value}'；可用值：{_format_values(self.available_values)}"
        )


__all__ = ["UnknownRegistryValueError"]
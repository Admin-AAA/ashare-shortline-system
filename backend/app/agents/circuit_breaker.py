"""
LLM API 调用断路器。

简单的滑动窗口断路器：
- 若某个 model 连续失败达到阈值，则阻断该 model 的后续调用一段时间。
- 一次成功即可重置计数器，解除断路。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    blocked_until: float = 0.0


@dataclass
class CircuitBreaker:
    """按 model_id 分区的断路器。"""

    failure_threshold: int = 5
    cooldown_seconds: float = 60.0

    _states: dict[str, _CircuitState] = field(default_factory=lambda: defaultdict(_CircuitState))

    def check_circuit(self, model_id: str) -> None:
        """
        检查指定 model 的电路状态。若处于熔断状态则抛出 RuntimeError。

        Args:
            model_id: LLM 模型标识（如 "deepseek-chat"）

        Raises:
            RuntimeError: 当电路熔断时
        """
        state = self._states[model_id]
        if state.blocked_until > 0 and time.monotonic() < state.blocked_until:
            remaining = state.blocked_until - time.monotonic()
            raise RuntimeError(
                f"断路器熔断 (模型: {model_id}) — 连续失败阈值 ({self.failure_threshold}) "
                f"已触发，{remaining:.0f}s 内禁止调用"
            )
        # 冷却时间已过，重置封锁（但保留计数器以便继续跟踪）
        if state.blocked_until > 0 and time.monotonic() >= state.blocked_until:
            state.blocked_until = 0.0

    def record_result(self, model_id: str, success: bool) -> None:
        """
        记录一次调用结果。

        Args:
            model_id: LLM 模型标识
            success: 调用是否成功
        """
        state = self._states[model_id]
        if success:
            state.consecutive_failures = 0
            state.blocked_until = 0.0
        else:
            state.consecutive_failures += 1
            if state.consecutive_failures >= self.failure_threshold:
                state.blocked_until = time.monotonic() + self.cooldown_seconds

    def status(self, model_id: str) -> dict:
        """返回当前 circuit 状态（供观测/调试）。"""
        state = self._states[model_id]
        blocked = state.blocked_until > 0 and time.monotonic() < state.blocked_until
        return {
            "model_id": model_id,
            "consecutive_failures": state.consecutive_failures,
            "blocked": blocked,
            "blocked_until": state.blocked_until if blocked else None,
        }


# 全局单例
_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    """获取全局断路器单例。"""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


def check_circuit(model_id: str) -> None:
    """便捷函数：检查 model 电路是否熔断。"""
    get_circuit_breaker().check_circuit(model_id)


def record_result(model_id: str, success: bool) -> None:
    """便捷函数：记录 model 的一次调用结果。"""
    get_circuit_breaker().record_result(model_id, success)

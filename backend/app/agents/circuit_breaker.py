"""
LLM API 调用断路器。

按错误类型差异化冷却：
- 永久性错误（402 余额不足 / 401 鉴权 / 404 模型不存在）不会自行恢复，
  用长冷却（默认 30 分钟）避免熔断-重试-再熔断的无效循环。
  07-09 deepseek 402 余额耗尽：340 calls 320 failed，B 项 LLM delta 全缺失，
  根因之一是 60s 短冷却导致每分钟解封后试 5 次又 402。
- 暂时性错误（429 限流 / 超时 / 网络抖动）保持短冷却（默认 60s）快速恢复。
- 一次成功即可重置计数器，解除断路（余额充值后立即恢复）。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


# 永久性错误：不会自行恢复，长冷却避免无效重试
# 402 余额不足 / 401-403 鉴权 / 404 模型不存在 / model_unavailable
PERMANENT_ERROR_TYPES = frozenset({
    "insufficient_balance",
    "auth_error",
    "model_unavailable",
})

# 永久性错误的长冷却（秒）。余额/鉴权/模型配置问题不会 60s 后自行解决，
# 30 分钟冷却既避免无效重试，又能在用户充值/修配置后 reasonably 恢复。
PERMANENT_COOLDOWN_SECONDS: float = 1800.0


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

    def record_result(self, model_id: str, success: bool, error_type: str | None = None) -> None:
        """
        记录一次调用结果。

        Args:
            model_id: LLM 模型标识
            success: 调用是否成功
            error_type: 失败时的错误类型（来自 _route_error_status）。
                永久性错误（insufficient_balance/auth_error/model_unavailable）
                用长冷却；其他保持默认短冷却。成功时忽略。
        """
        state = self._states[model_id]
        if success:
            state.consecutive_failures = 0
            state.blocked_until = 0.0
        else:
            state.consecutive_failures += 1
            if state.consecutive_failures >= self.failure_threshold:
                if error_type in PERMANENT_ERROR_TYPES:
                    state.blocked_until = time.monotonic() + PERMANENT_COOLDOWN_SECONDS
                else:
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


def record_result(model_id: str, success: bool, error_type: str | None = None) -> None:
    """便捷函数：记录 model 的一次调用结果。"""
    get_circuit_breaker().record_result(model_id, success, error_type=error_type)

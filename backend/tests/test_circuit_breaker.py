"""断路器对不同错误类型的差异化冷却测试。

永久性错误（402 余额不足 / 401 鉴权 / 404 模型不存在）不会自行恢复，
短冷却（60s）会导致熔断-重试-再熔断的无效循环（07-09 deepseek 402：
340 calls 320 failed，B 项 LLM delta 全缺失）。
对这类错误用长冷却（30 分钟），暂时性错误（429/超时）保持短冷却。
"""

from __future__ import annotations

import time

from app.agents.circuit_breaker import CircuitBreaker


def _trigger_failures(cb: CircuitBreaker, model_id: str, error_type: str, count: int) -> None:
    for _ in range(count):
        cb.record_result(model_id, success=False, error_type=error_type)


def test_permanent_error_uses_long_cooldown():
    """402 余额不足触发长冷却，避免短时间内无效重试。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "insufficient_balance", 5)
    # 长冷却应远大于默认 60s（永久性错误不会 60s 后自行恢复）
    state = cb._states["deepseek"]
    assert state.blocked_until > 0
    remaining = state.blocked_until - time.monotonic()
    assert remaining > 60, f"永久性错误冷却应长于 60s，实际 {remaining:.0f}s"


def test_permanent_error_blocks_on_first_failure():
    """永久性错误（401/402/404）是确定性失败，首次即熔断，不等阈值——
    重试同一坏 key/坏配置 N 次纯属浪费调用（07-23 kimi-k3 401 key 失效实证）。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    cb.record_result("kimi-k3", success=False, error_type="auth_error")  # 仅 1 次
    state = cb._states["kimi-k3"]
    assert state.blocked_until > 0, "永久性错误首次失败应立即熔断"
    remaining = state.blocked_until - time.monotonic()
    assert remaining > 60, f"永久性错误首次即长冷却，实际 {remaining:.0f}s"
    # 熔断期间 check_circuit 应拦截
    try:
        cb.check_circuit("kimi-k3")
        assert False, "永久性错误首次熔断后应被 check_circuit 拦截"
    except RuntimeError:
        pass


def test_rate_limited_uses_short_cooldown():
    """429 限流是暂时性错误，保持短冷却快速恢复。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "rate_limited", 5)
    state = cb._states["deepseek"]
    remaining = state.blocked_until - time.monotonic()
    assert 55 <= remaining <= 65, f"429 应保持 60s 短冷却，实际 {remaining:.0f}s"


def test_auth_error_uses_long_cooldown():
    """401 鉴权错误是配置问题，不会自行恢复，用长冷却。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "auth_error", 5)
    remaining = cb._states["deepseek"].blocked_until - time.monotonic()
    assert remaining > 60


def test_model_unavailable_uses_long_cooldown():
    """404 模型不存在是配置问题，用长冷却。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "model_unavailable", 5)
    remaining = cb._states["deepseek"].blocked_until - time.monotonic()
    assert remaining > 60


def test_generic_error_uses_default_cooldown():
    """未知错误类型（超时/网络）保持默认短冷却。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "error", 5)
    remaining = cb._states["deepseek"].blocked_until - time.monotonic()
    assert 55 <= remaining <= 65


def test_success_resets_permanent_cooldown():
    """一次成功即解除长冷却（余额充值后能立即恢复）。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "insufficient_balance", 5)
    assert cb._states["deepseek"].blocked_until > 0
    cb.record_result("deepseek", success=True)
    assert cb._states["deepseek"].blocked_until == 0
    assert cb._states["deepseek"].consecutive_failures == 0


def test_below_threshold_no_block():
    """暂时性错误未达阈值不熔断（给上游恢复时间）。永久性错误不在此列——见 test_permanent_error_blocks_on_first_failure。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "rate_limited", 4)
    assert cb._states["deepseek"].blocked_until == 0
    cb.check_circuit("deepseek")  # 不抛异常


def test_check_circuit_blocks_during_cooldown():
    cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=60)
    _trigger_failures(cb, "deepseek", "insufficient_balance", 2)
    try:
        cb.check_circuit("deepseek")
        assert False, "熔断期间应抛 RuntimeError"
    except RuntimeError as e:
        assert "熔断" in str(e)


def test_record_result_without_error_type_backward_compatible():
    """不传 error_type 时向后兼容（默认短冷却）。"""
    cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=60)
    for _ in range(5):
        cb.record_result("deepseek", success=False)  # 不传 error_type
    remaining = cb._states["deepseek"].blocked_until - time.monotonic()
    assert 55 <= remaining <= 65

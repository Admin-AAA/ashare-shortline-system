"""LLM 路由 failover 判定测试（_should_failover_immediately / _route_error_status）。

锁定 5xx 上游错误立即 failover 的行为（2026-07-22 T8 改进）。

背景：grok-4.5 503 upstream_unavailable 旧逻辑不在 _should_failover_immediately 的
status_code 集合 {401,402,403,404,429}，也不匹配 markers → 重试当前路由 max_retries
次（~7s）才 failover，60 候选累积 503 触发 circuit_open 21 次。修复：502/503/504
立即 failover 换路由，减少熔断触发 + 更快切到可用路由。
"""
from app.agents.agno_skill_agent import (
    _ROUTE_FAILOVER_ERROR_MARKERS,
    _route_error_status,
    _should_failover_immediately,
)


class _Exc(Exception):
    """带 status_code 的测试异常（模拟 OpenAI APIStatusError）。"""

    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message)
        self.status_code = status_code


# ── _should_failover_immediately：5xx 立即 failover ──

def test_503_upstream_unavailable_failover_immediately():
    """503 upstream_unavailable 立即换路由（07-21 grok-4.5 实证根因）。"""
    assert _should_failover_immediately(
        _Exc(503, "Error code: 503 - upstream_unavailable service temporarily unavailable")
    ) is True


def test_502_bad_gateway_failover_immediately():
    assert _should_failover_immediately(_Exc(502, "Bad Gateway")) is True


def test_504_gateway_timeout_failover_immediately():
    assert _should_failover_immediately(_Exc(504, "Gateway Timeout")) is True


def test_429_rate_limit_still_failover_immediately():
    """429 限流回归保护：仍在立即 failover 集合（换路由可能不限流）。"""
    assert _should_failover_immediately(_Exc(429, "rate limit exceeded")) is True


def test_402_insufficient_balance_failover_immediately():
    assert _should_failover_immediately(_Exc(402, "insufficient balance")) is True


def test_500_generic_server_error_not_immediate_failover():
    """500 通用服务端错误不在立即 failover（可能是瞬时，重试当前路由合理）。

    500 不等于 502/503/504——500 是通用服务端错误，可能重试即恢复；
    502/503/504 明确是上游/网关不可用，换路由更可能成功。
    """
    assert _should_failover_immediately(_Exc(500, "Internal Server Error")) is False


def test_connection_error_not_immediate_failover():
    """网络连接错误（无 status_code）不立即 failover（重试当前路由，网络瞬时）。"""
    assert _should_failover_immediately(Exception("Connection error.")) is False


def test_upstream_unavailable_marker_in_text_failover():
    """错误文本含 upstream_unavailable marker（无 status_code）也立即 failover。"""
    assert _should_failover_immediately(Exception("upstream_unavailable")) is True


# ── _route_error_status：错误归类（影响熔断冷却时长）──

def test_503_classified_as_generic_error_not_permanent():
    """503 归类为 error（非 permanent），60s 短冷却——503 是暂时的，不该长冷却。"""
    assert _route_error_status(_Exc(503, "upstream_unavailable")) == "error"


def test_402_classified_as_insufficient_balance_permanent():
    """402 归类为 insufficient_balance（permanent），30 分钟长冷却。"""
    assert _route_error_status(_Exc(402, "insufficient balance")) == "insufficient_balance"


def test_upstream_unavailable_markers_present():
    """markers 含 5xx 上游不可用标志（防止回归删除）。"""
    for marker in ("upstream_unavailable", "service temporarily unavailable", "service unavailable"):
        assert marker in _ROUTE_FAILOVER_ERROR_MARKERS, f"缺少 5xx marker: {marker}"

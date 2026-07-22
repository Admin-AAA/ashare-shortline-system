"""T8 LLM 调用健康诊断纯函数测试（_call_health_rate）。

锁定 LLM 调用成功率判定：由 skill_review 成功/失败数算成功率 + 是否低成功率（<80%）。
核心判定提取为纯函数 _call_health_rate（llm_review_cache_service），与 DB 解耦，
供 /api/llm/review-health 与 self_check._check_llm_call_health 复用（同源避免漂移）。

背景：formal run 标 agent_runtime=llm 但 skill_review 全失败时（如 2026-07-20
gpt-5.4 熔断 succ=0/170），推荐级 delta 全缺失。_call_health_rate 从 result_summary
提取成功率，让 LLM 层失效立即可见。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.llm_review_cache_service import _call_health_rate


def test_healthy_when_high_success_rate():
    """成功率 ≥80% 不标低成功率。"""
    rate, unhealthy = _call_health_rate(60, 0)
    assert rate == 100.0
    assert unhealthy is False


def test_healthy_at_80pct_boundary():
    """80% 边界（48/60）不算低成功率（<80 才算）。"""
    rate, unhealthy = _call_health_rate(48, 12)
    assert rate == 80.0
    assert unhealthy is False


def test_unhealthy_below_80pct():
    """成功率 <80% 标低成功率（如 07-21 的 58/107=54%）。"""
    rate, unhealthy = _call_health_rate(58, 49)
    assert rate == 54.2
    assert unhealthy is True


def test_unhealthy_when_all_failed():
    """0 成功（如 07-20 gpt-5.4 熔断 succ=0/170）→ 0% 低成功率。"""
    rate, unhealthy = _call_health_rate(0, 170)
    assert rate == 0.0
    assert unhealthy is True


def test_zero_total_returns_zero_rate_unhealthy():
    """total=0（无调用）→ rate=0，unhealthy=True（0% <80%）。"""
    rate, unhealthy = _call_health_rate(0, 0)
    assert rate == 0.0
    assert unhealthy is True


def test_rate_rounded_to_one_decimal():
    """成功率四舍五入到 1 位小数（避免浮点噪声）。"""
    rate, _ = _call_health_rate(58, 49)
    assert rate == 54.2  # 58/107*100 = 54.205... → 54.2

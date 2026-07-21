"""T8 LLM 调用健康诊断纯函数测试（_llm_call_health_status）。

_check_llm_call_health 从 result_summary 提取 skill_review 成功/失败数，算成功率并
判定是否低成功率（<80%）。核心判定提取为纯函数 _llm_call_health_status，与 DB 解耦。

背景：formal run 标 agent_runtime=llm 但 skill_review 全失败时（如 2026-07-20
gpt-5.4 熔断 succ=0/170），推荐级 delta 全缺失，但 B 项原只在「delta 缺失」时触发
_route_health，且不报告成功率/失败原因。本诊断无条件报告，让 LLM 层失效立即可见。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_healthy_when_high_success_rate():
    """成功率 ≥80% 不标低成功率。"""
    rate, unhealthy = self_check._llm_call_health_status(60, 0)
    assert rate == 100.0
    assert unhealthy is False


def test_healthy_at_80pct_boundary():
    """80% 边界（48/60）不算低成功率（<80 才算）。"""
    rate, unhealthy = self_check._llm_call_health_status(48, 12)
    assert rate == 80.0
    assert unhealthy is False


def test_unhealthy_below_80pct():
    """成功率 <80% 标低成功率（如 07-21 的 58/107=54%）。"""
    rate, unhealthy = self_check._llm_call_health_status(58, 49)
    assert rate == 54.2
    assert unhealthy is True


def test_unhealthy_when_all_failed():
    """0 成功（如 07-20 gpt-5.4 熔断 succ=0/170）→ 0% 低成功率。"""
    rate, unhealthy = self_check._llm_call_health_status(0, 170)
    assert rate == 0.0
    assert unhealthy is True


def test_zero_total_returns_zero_rate_not_unhealthy_flag_consistent():
    """total=0（无调用）→ rate=0，unhealthy=True（0% <80%）。

    实践中 ar=='llm' 且 inp>0 时 total≥0；total=0 表示 LLM 调用 0 次（输入候选 0），
    此场景在 _check_llm_call_health 里由 inp==0 跳过，但纯函数本身对 0/0 返回 0%+unhealthy。
    """
    rate, unhealthy = self_check._llm_call_health_status(0, 0)
    assert rate == 0.0
    assert unhealthy is True


def test_rate_rounded_to_one_decimal():
    """成功率四舍五入到 1 位小数（避免浮点噪声）。"""
    rate, _ = self_check._llm_call_health_status(58, 49)
    assert rate == 54.2  # 58/107*100 = 54.205... → 54.2

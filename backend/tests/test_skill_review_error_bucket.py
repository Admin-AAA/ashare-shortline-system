"""T8 诊断：skill_review_error_bucket 上游 5xx 归类测试。

背景：上游 503/502/504 此前无专属 bucket，被漏到 agent_review_error 兜底，
导致 self_check 误判为「解析错误 / 查 prompt 格式」，而真实根因是模型 provider
不稳定。本测试锁定 5xx → model_upstream_5xx 归类，并回归校验既有 bucket 不被破坏。
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.runs.filter_audit import skill_review_error_bucket


def test_503_upstream_unavailable_buckets_as_5xx():
    """503 + upstream_unavailable 必须归类为 model_upstream_5xx（非 agent_review_error）。"""
    err = "默认模型 gpt-5.4 (grok-4.5) 失败: Error code: 503 - {'error': {'code': 'upstream_unavailable', 'message': 'service temporarily unavailable'}}"
    assert skill_review_error_bucket(err) == "model_upstream_5xx"


def test_502_and_504_bucket_as_5xx():
    assert skill_review_error_bucket("Error code: 502 - bad gateway") == "model_upstream_5xx"
    assert skill_review_error_bucket("Error code: 504 - gateway timeout") == "model_upstream_5xx"


def test_service_unavailable_text_buckets_as_5xx():
    assert skill_review_error_bucket("service temporarily unavailable") == "model_upstream_5xx"
    assert skill_review_error_bucket("service unavailable") == "model_upstream_5xx"


def test_real_503_bare_code_buckets_as_5xx():
    """真实样本 ' 503 ' 裸码也要识别（防止漏回 agent_review_error）。"""
    assert skill_review_error_bucket("Error code: 503 - something") == "model_upstream_5xx"


def test_402_still_payment_required():
    assert skill_review_error_bucket("Error code: 402 - insufficient balance") == "model_payment_required"


def test_429_still_rate_limited():
    assert skill_review_error_bucket("Error code: 429 - rate limit exceeded") == "model_rate_limited"


def test_401_still_auth_failed():
    assert skill_review_error_bucket("401 unauthorized: invalid api key") == "model_auth_failed"


def test_timeout_still_timeout():
    assert skill_review_error_bucket("Skill 'X' 审查超时 (>300s)，已跳过") == "model_timeout"


def test_circuit_open_still_circuit():
    assert skill_review_error_bucket("gpt-5.4 (grok-4.5) 熔断: 断路器熔断") == "model_circuit_open"


def test_empty_error_falls_through():
    assert skill_review_error_bucket("") == "agent_review_failed"
    assert skill_review_error_bucket(None) == "agent_review_failed"


def test_genuine_parse_error_still_agent_review_error():
    """真正的解析错误（JSONDecodeError 文本）仍归 agent_review_error。"""
    assert skill_review_error_bucket("Expecting value: line 1 column 1 (char 0)") == "agent_review_error"

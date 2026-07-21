"""T8 — token cost / rule baseline 持久化到 llm_review_cache 的单元测试。

验证 _enrich_review_with_cost_and_rule：
  - additive：不改既有 review 字段
  - _token_cost 含真实用量 / 估算用量 / cache 命中
  - rule_score / llm_delta 仅当 rule_signal 含 dsa_signal_score 时注入
  - 不抛异常、不 drain 全局状态
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.agno_skill_agent import _enrich_review_with_cost_and_rule


def test_additive_keeps_existing_fields():
    parsed = {"stance": "support", "score": 75, "summary": "ok"}
    out = _enrich_review_with_cost_and_rule(
        parsed, 100, 50, 40, 900, 300, {"dsa_signal_score": 80}
    )
    assert out["stance"] == "support"
    assert out["score"] == 75
    assert out["summary"] == "ok"
    # 原对象不被原地修改
    assert "_token_cost" not in parsed


def test_token_cost_fields_present():
    out = _enrich_review_with_cost_and_rule(
        {"score": 60}, 120, 60, 30, 1000, 400, None
    )
    tc = out["_token_cost"]
    assert tc["prompt_tokens"] == 120
    assert tc["completion_tokens"] == 60
    assert tc["total_tokens_real"] == 180
    assert tc["total_cache_hit_tokens"] == 30
    assert tc["prompt_cache_active"] is True
    # 估算回退
    assert tc["prompt_tokens_est"] == int(1000 / 1.8)
    assert tc["completion_tokens_est"] == int(400 / 1.8)


def test_prompt_cache_inactive_when_no_hit():
    out = _enrich_review_with_cost_and_rule(
        {"score": 60}, 120, 60, 0, 1000, 400, None
    )
    assert out["_token_cost"]["prompt_cache_active"] is False
    assert out["_token_cost"]["total_cache_hit_tokens"] == 0


def test_rule_score_and_delta_injected_only_with_dsa():
    # 有 dsa_signal_score → 注入 rule_score / llm_delta
    out = _enrich_review_with_cost_and_rule(
        {"score": 70}, 10, 5, 0, 100, 50, {"dsa_signal_score": 78}
    )
    assert out["rule_score"] == 78.0
    assert out["llm_delta"] == round(70 - 78, 2)  # -8.0

    # 无 dsa_signal_score → 不注入
    out2 = _enrich_review_with_cost_and_rule(
        {"score": 70}, 10, 5, 0, 100, 50, {"other": 1}
    )
    assert "rule_score" not in out2
    assert "llm_delta" not in out2

    # rule_signal 为 None → 不注入且不抛
    out3 = _enrich_review_with_cost_and_rule({"score": 70}, 10, 5, 0, 100, 50, None)
    assert "rule_score" not in out3

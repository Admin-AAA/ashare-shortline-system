"""T8 LLM 层 token 用量与 prompt cache 核算测试。

验证点：
1. `_extract_usage_tokens` 能从 OpenAI 兼容响应抽取真实 prompt/completion tokens
   及 DeepSeek 的 prompt cache 命中量（cached_tokens / prompt_tokens_cache_hit 两种字段名）。
2. `_append_api_cost` 在传入真实用量时写入 prompt_tokens/completion_tokens/cache_hit_tokens，
   未传入时回退为 None（保留 chars/1.8 估算作为 *_est 字段）。
3. `drain_api_costs` 清空并返回累积记录。
"""
from types import SimpleNamespace

from app.agents.agno_skill_agent import _append_api_cost, _extract_usage_tokens, drain_api_costs


class _FakeRoute:
    model = "deepseek-v4-pro"
    base_url = "https://api.deepseek.com"
    name = "deepseek"
    route_id = "route_env_default"

    def __str__(self) -> str:  # pragma: no cover - 仅用于 endpoint 展示
        return "route"


def _fake_resp(prompt_tokens=None, completion_tokens=None, cached_tokens="_UNSET", cache_hit="_UNSET"):
    details = None
    if cached_tokens != "_UNSET" or cache_hit != "_UNSET":
        kwargs = {}
        if cached_tokens != "_UNSET":
            kwargs["cached_tokens"] = cached_tokens
        if cache_hit != "_UNSET":
            kwargs["prompt_tokens_cache_hit"] = cache_hit
        details = SimpleNamespace(**kwargs)
    usage = None
    if prompt_tokens is not None or completion_tokens is not None or details is not None:
        usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_tokens_details=details)
    return SimpleNamespace(usage=usage)


def test_extract_usage_tokens_full():
    pt, ct, ch = _extract_usage_tokens(_fake_resp(100, 50, cached_tokens=80))
    assert (pt, ct, ch) == (100, 50, 80)


def test_extract_usage_tokens_alt_field_name():
    pt, ct, ch = _extract_usage_tokens(_fake_resp(10, 5, cache_hit=33))
    assert (pt, ct, ch) == (10, 5, 33)


def test_extract_usage_tokens_no_details():
    pt, ct, ch = _extract_usage_tokens(_fake_resp(10, 5))
    assert (pt, ct, ch) == (10, 5, None)


def test_extract_usage_tokens_no_usage():
    assert _extract_usage_tokens(SimpleNamespace(usage=None)) == (None, None, None)
    assert _extract_usage_tokens(_fake_resp()) == (None, None, None)


def test_append_api_cost_captures_real_usage_and_cache_hit():
    drain_api_costs()
    route = _FakeRoute()
    _append_api_cost(
        route=route,
        prompt_chars=9000,
        completion_chars=800,
        latency_ms=100.0,
        status="success",
        prompt_tokens=5013,
        completion_tokens=443,
        cache_hit_tokens=4200,
        skill_id="sector-relative-strength-leader",
    )
    costs = drain_api_costs()
    assert len(costs) == 1
    c = costs[0]
    assert c["prompt_tokens"] == 5013
    assert c["completion_tokens"] == 443
    assert c["cache_hit_tokens"] == 4200
    assert c["status"] == "success"
    # skill_id 必须透传到 cost dict（T8 按 skill 归因 token 消耗的前提）
    assert c["skill_id"] == "sector-relative-strength-leader"
    # 估算字段仍保留（基于 chars/1.8），与新字段并存，向后兼容
    assert c["prompt_tokens_est"] == int(9000 / 1.8)
    assert c["completion_tokens_est"] == int(800 / 1.8)


def test_append_api_cost_falls_back_to_none_when_real_missing():
    drain_api_costs()
    route = _FakeRoute()
    _append_api_cost(route=route, prompt_chars=100, completion_chars=10, latency_ms=1.0, status="success")
    c = drain_api_costs()[0]
    assert c["prompt_tokens"] is None
    assert c["completion_tokens"] is None
    assert c["cache_hit_tokens"] is None
    # skill_id 未传时回退 None（向后兼容旧调用点）
    assert c["skill_id"] is None
    # 估算字段仍基于 chars 计算
    assert c["prompt_tokens_est"] == int(100 / 1.8)


def test_record_api_call_accepts_drained_cost_dict(db):
    """回归测试：drain_api_costs() 输出的完整 cost dict（含真实 token 字段）
    必须能直接喂给 record_api_call(db, **cost) 而不崩。

    根因（2026-07-07 T12）：_append_api_cost 输出 prompt_tokens/completion_tokens/
    cache_hit_tokens 三个键，但旧 record_api_call 只接受 *_est 后缀，**cost 解包即
    TypeError → formal run 在 skill_review 阶段整体失败 → 0 推荐（北极星 A 项中断）。
    """
    from app.db.init_db import init_db
    from app.services.cost_service import record_api_call

    init_db()  # 确保 api_call_logs 已加 prompt_tokens/completion_tokens/cache_hit_tokens 列

    drain_api_costs()
    route = _FakeRoute()
    _append_api_cost(
        route=route,
        prompt_chars=9000,
        completion_chars=800,
        latency_ms=100.0,
        status="success",
        prompt_tokens=5013,
        completion_tokens=443,
        cache_hit_tokens=4200,
        skill_id="sector-relative-strength-leader",
    )
    costs = drain_api_costs()
    assert len(costs) == 1
    cost = costs[0]

    # 这是 workbench_service.py:2088 的真实调用形态——必须不崩
    log = record_api_call(db, run_id="test_run_token_acct", **cost)

    assert log.id is not None
    assert log.run_id == "test_run_token_acct"
    assert log.prompt_chars == 9000
    assert log.completion_chars == 800
    # 真实用量入库
    assert log.prompt_tokens == 5013
    assert log.completion_tokens == 443
    assert log.cache_hit_tokens == 4200
    # skill_id 入库（T8 按 skill 归因 token 消耗的前提）
    assert log.skill_id == "sector-relative-strength-leader"

    # None 用量也必须可写入（provider 未返回真实值时的回退路径）
    _append_api_cost(route=route, prompt_chars=100, completion_chars=10, latency_ms=1.0, status="success")
    cost_none = drain_api_costs()[0]
    log_none = record_api_call(db, run_id="test_run_token_acct_none", **cost_none)
    assert log_none.prompt_tokens is None
    assert log_none.completion_tokens is None
    assert log_none.cache_hit_tokens is None
    assert log_none.skill_id is None

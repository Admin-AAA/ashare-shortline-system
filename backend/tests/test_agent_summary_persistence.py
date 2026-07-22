"""T7-B agent_summary 顶层决策字段提取测试。

回归保护：LLM skill agent 的审查结果（stance/score_delta/summary/action 等）
原本埋在 agent_summary_json.logs[0].output 里，列表视图与北极星 B 项自检
（$.delta）拿不到。_build_agent_summary 把关键字段提到顶层，同时保留审计 logs。
"""
from app.services.runs.persistence import _build_agent_summary


def _card(agent_logs: list[dict]) -> dict:
    return {"agent_logs": agent_logs, "market_snapshot": {}}


def _llm_output(**overrides) -> dict:
    base = {
        "stance": "support",
        "score_delta": 25.0,
        "score": 75,
        "summary": "主线确认，超额收益强，可轻仓试错",
        "action": "轻仓试错",
        "position_range": "5-10%",
        "entry_style": "分歧低吸",
        "evidence": ["is_main_theme=true"],
        "risks": ["板块梯队不足"],
    }
    base.update(overrides)
    return base


def test_build_agent_summary_extracts_top_level_decision_fields():
    logs = [{"agent_name": "Skill Agent · 主线相对强度龙头", "output": _llm_output()}]
    summary = _build_agent_summary(_card(logs))

    # 顶层快捷字段（B 项自检 $.delta / 前端列表直接读）
    assert summary["stance"] == "support"
    assert summary["score_delta"] == 25.0
    assert summary["score"] == 75
    assert summary["summary"] == "主线确认，超额收益强，可轻仓试错"
    assert summary["action"] == "轻仓试错"
    assert summary["position_range"] == "5-10%"
    assert summary["entry_style"] == "分歧低吸"
    # 审计 logs 仍完整保留（不丢信息）
    assert summary["logs"] == logs
    assert summary["mode"] == "auditable_skill_agents"


def test_build_agent_summary_no_logs_returns_safe_skeleton():
    """无 LLM 审查（纯规则推荐）时返回安全骨架，不崩、不产生顶层决策字段。"""
    summary = _build_agent_summary(_card([]))
    assert summary["mode"] == "auditable_skill_agents"
    assert summary["logs"] == []
    assert "stance" not in summary
    assert "score_delta" not in summary


def test_build_agent_summary_data_source_from_market_snapshot():
    logs = [{"agent_name": "x", "output": _llm_output()}]
    card = {
        "agent_logs": logs,
        "market_snapshot": {"breadth": {"data_source": "local_unified_market_pool"}},
    }
    summary = _build_agent_summary(card)
    assert summary["data_source"] == "local_unified_market_pool"


def test_build_agent_summary_partial_output_only_extracts_present_fields():
    """LLM 只返回部分字段时，不存在的字段不强塞 None。"""
    logs = [{"agent_name": "x", "output": {"stance": "neutral", "score_delta": 0.0}}]
    summary = _build_agent_summary(_card(logs))
    assert summary["stance"] == "neutral"
    assert summary["score_delta"] == 0.0
    assert "summary" not in summary
    assert "action" not in summary


def test_build_agent_summary_extracts_risks_to_top_level():
    """LLM risks 提到顶层（B 项决策链一屏可见，前端列表不必翻 logs[0].output.risks）。

    回归根因：07-22 推荐 300039 agent_summary 顶层缺 risks，只有 logs[0].output.risks。
    前端列表/自检读顶层 $.risks 拿到空，决策链断层。
    """
    logs = [{"agent_name": "x", "output": _llm_output(risks=["板块梯队不足", "资金面中性"])}]
    summary = _build_agent_summary(_card(logs))
    assert summary["risks"] == ["板块梯队不足", "资金面中性"]


def test_build_agent_summary_extracts_supports_preferring_supports_over_evidence():
    """supports 优先于 evidence（review_runner 已把 evidence 映射为 supports）。

    LLM review 同时存 evidence 与 supports，顶层 supports 取 supports 字段
    （若存在），否则兜底 evidence。
    """
    logs = [{"agent_name": "x", "output": _llm_output(
        evidence=["evidence1"],
        supports=["supports1", "supports2"],
    )}]
    summary = _build_agent_summary(_card(logs))
    assert summary["supports"] == ["supports1", "supports2"]


def test_build_agent_summary_supports_falls_back_to_evidence():
    """supports 字段缺失时用 evidence 兜底（兼容旧 review 只有 evidence）。"""
    logs = [{"agent_name": "x", "output": {"stance": "support", "evidence": ["仅evidence"]}}]
    summary = _build_agent_summary(_card(logs))
    assert summary["supports"] == ["仅evidence"]


def test_build_agent_summary_supports_absent_when_both_missing():
    """evidence 和 supports 都没有时不强塞顶层 supports（避免空列表混淆）。"""
    logs = [{"agent_name": "x", "output": {"stance": "neutral"}}]
    summary = _build_agent_summary(_card(logs))
    assert "supports" not in summary


def test_build_agent_summary_fallback_prelim_extracts_supports_risks():
    """LLM 失败走 fallback_prelim 时也提取 supports/risks（降级 review 有这俩）。"""
    card = {
        "agent_logs": [],
        "strategy_signals": [{"agent_skill_review": {
            "runtime": "agent_skill_fallback_prelim",
            "stance": "support",
            "score_delta": 8.0,
            "supports": ["fallback support"],
            "risks": ["fallback risk"],
        }}],
        "market_snapshot": {},
    }
    summary = _build_agent_summary(card)
    assert summary["supports"] == ["fallback support"]
    assert summary["risks"] == ["fallback risk"]


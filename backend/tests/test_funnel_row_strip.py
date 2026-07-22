"""_strip_funnel_row_dead_fields 纯函数单测（不连库）。

验证 /api/runs/{id}/funnel 响应剥离前端从不消费的 prefilter / trace_summary
字段后：用到的字段(rule_gate/score_line/trace/final_*)原样保留，空/缺字段安全。
"""
from app.services.workbench_service import (
    _FUNNEL_ROW_DEAD_FIELDS,
    _strip_funnel_row_dead_fields,
)


def _row() -> dict:
    return {
        "code": "000001",
        "name": "平安银行",
        "final_stage": "skill_gate",
        "final_reason": "规则门分 47.7 低于 58.0",
        "rule_gate": {"best_gate_score": 47.7, "best_skill_id": "x"},
        "score_line": {"final_score": 0, "effective_score_line": 95.55},
        "trace": [{"stage": "skill_gate", "passed": False}],
        # 以下两个字段前端从不读取，应被剥离
        "prefilter": {"market": {"include": ["a"]}, "scope": {}},
        "trace_summary": "公共预过滤:过 -> 预过滤通过:过 -> 规则门:止",
    }


def test_strips_dead_fields_but_keeps_used():
    rows = [_row()]
    out = _strip_funnel_row_dead_fields(rows)
    assert out is rows  # 原地修改
    r = out[0]
    assert "prefilter" not in r
    assert "trace_summary" not in r
    # 用到的字段必须原样保留
    assert r["rule_gate"] == {"best_gate_score": 47.7, "best_skill_id": "x"}
    assert r["score_line"] == {"final_score": 0, "effective_score_line": 95.55}
    assert r["trace"] == [{"stage": "skill_gate", "passed": False}]
    assert r["final_stage"] == "skill_gate"
    assert r["final_reason"] == "规则门分 47.7 低于 58.0"


def test_empty_rows_safe():
    assert _strip_funnel_row_dead_fields([]) == []
    assert _strip_funnel_row_dead_fields(None) is None or _strip_funnel_row_dead_fields([]) == []


def test_rows_missing_dead_fields_unchanged():
    rows = [{"code": "600000", "rule_gate": {"best_gate_score": 80}}]
    out = _strip_funnel_row_dead_fields(rows)
    assert out == [{"code": "600000", "rule_gate": {"best_gate_score": 80}}]


def test_dead_fields_constant_covers_only_unused():
    # 防御：若将来前端开始消费这两个字段，常量必须同步更新，否则会误删。
    assert set(_FUNNEL_ROW_DEAD_FIELDS) == {"prefilter", "trace_summary"}

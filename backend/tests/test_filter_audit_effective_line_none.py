"""回归测试：build_filter_audit 在 effective_score_line 为 None 时不得崩溃。

复现 2026-07-22 04:09 formal run 失败根因：
  filter_audit.py:566  effective_score_line=round(effective_line, 2)
  TypeError: type NoneType doesn't define __round__ method

该崩溃发生在「无 regime 有效线（effective_score_line=None）且仍有候选进入 score_line
阶段」的 run（非交易日/数据缺口日/范围受限 run）。修复前会让整次 formal run 以
TypeError 失败（留下 failed/zombie，污染 A 项召回）；修复后应与 score_row 字段(485 行)
一致地安全置 None，不再崩溃。
"""
import pytest

from app.services.runs.filter_audit import build_filter_audit


def _card(code: str, effective_line=None, passed: bool = False, final_score: float = 50.0):
    meta = {"passed": passed}
    if effective_line is not None:
        meta["effective_score_line"] = effective_line
    return {
        "code": code,
        "name": "测试股",
        "final_score": final_score,
        "score_line_meta": meta,
    }


def test_build_filter_audit_effective_line_none_does_not_crash():
    """effective_score_line=None 的候选不得引发 TypeError。"""
    audit = build_filter_audit(
        prefilter_audit=None,
        skill_gate_audit=None,
        scored_cards=[_card("600000", effective_line=None, passed=False, final_score=50.0)],
        recommended_cards=[],
        pipeline={},
    )
    rows = audit.get("rows", [])
    assert rows, "应至少产出一行 score_line 候选"
    sl = rows[0].get("score_line") or {}
    # 修复后 effective_score_line 在 trace 与 score_row 中均为 None，而非崩溃
    assert sl.get("effective_score_line") is None


def test_build_filter_audit_effective_line_present_still_works():
    """有有效线时行为不变（对照，防止回归把正常路径改坏）。"""
    audit = build_filter_audit(
        prefilter_audit=None,
        skill_gate_audit=None,
        scored_cards=[_card("600000", effective_line=95.55, passed=False, final_score=71.88)],
        recommended_cards=[],
        pipeline={},
    )
    rows = audit.get("rows", [])
    assert rows
    sl = rows[0].get("score_line") or {}
    assert sl.get("effective_score_line") == 95.55
    assert sl.get("final_score") == 71.88
    assert sl.get("score_gap") == round(71.88 - 95.55, 2)


def test_build_filter_audit_mixed_effective_lines():
    """同一 run 内部分候选有有效线、部分为 None，整体不崩溃。"""
    audit = build_filter_audit(
        prefilter_audit=None,
        skill_gate_audit=None,
        scored_cards=[
            _card("600001", effective_line=93.2, passed=False, final_score=83.65),
            _card("600002", effective_line=None, passed=False, final_score=60.0),
        ],
        recommended_cards=[],
        pipeline={},
    )
    rows = {r["code"]: r for r in audit.get("rows", [])}
    assert rows["600001"]["score_line"]["effective_score_line"] == 93.2
    assert rows["600002"]["score_line"]["effective_score_line"] is None

"""score_line 拒绝文案：禁止 0/0 噪声（T5 2026-07-22）。"""
from __future__ import annotations

from app.services.runs.filter_audit import score_line_reject_reason


def test_passed():
    assert score_line_reject_reason(
        final_score=80.0, effective_line=72.0, passed=True,
    ) == "达到正式分数线"


def test_below_effective_line():
    r = score_line_reject_reason(
        final_score=72.0, effective_line=95.55, passed=False,
    )
    assert "最终分 72.0 低于有效线 95.55" == r


def test_missing_meta_not_zero_zero():
    r = score_line_reject_reason(
        final_score=0.0, effective_line=None, passed=False,
    )
    assert "0.0 低于有效线 0.0" not in r
    assert "无有效线元数据" in r


def test_hard_veto():
    r = score_line_reject_reason(
        final_score=0.0, effective_line=None, passed=False,
        hard_veto=True, veto_reason="弱势市打板毒区",
    )
    assert "hard_veto" in r
    assert "弱势市打板毒区" in r
    assert "低于有效线" not in r


def test_execution_blocked():
    r = score_line_reject_reason(
        final_score=80.0, effective_line=72.0, passed=False,
        execution_blocked=True, block_reason="涨停无法买入",
    )
    assert "涨停无法买入" in r

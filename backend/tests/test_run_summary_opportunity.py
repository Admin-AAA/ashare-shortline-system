"""测试 run summary 的空仓/无机会语义字段（P3.3）。"""
from app.services.workbench_service import _build_run_summary, _no_opportunity_reason


def test_build_run_summary_marks_empty_opportunity():
    summary = _build_run_summary(cards=[], saved=[], snapshot={})
    assert summary["opportunity"] == "empty"
    assert "未通过" in summary["no_opportunity_reason"] or summary["no_opportunity_reason"]


def test_build_run_summary_marks_has_recommendations():
    class FakeRec:
        code = "300001"
        final_score = 80.0

    summary = _build_run_summary(cards=[], saved=[FakeRec()], snapshot={})
    assert summary["opportunity"] == "has_recommendations"


def test_no_opportunity_reason_contains_scores():
    cards = [{"final_score": 56.0}, {"final_score": 54.0}]
    reason = _no_opportunity_reason(cards, {"effective_score_line": 60.0, "market_base": "弱势"})
    assert "56.0" in reason and "60.0" in reason and "弱势" in reason


def test_no_opportunity_reason_no_cards():
    reason = _no_opportunity_reason([], {})
    assert "无候选" in reason

from __future__ import annotations

from app.services.failure_attribution import classify_false_positive


def test_classify_entry_execution_blocked_reason():
    result = classify_false_positive(reason="入场日一字涨停，按真实成交约束视为买不进", executed=False)

    assert result["category"] == "entry_execution_blocked"
    assert result["category_label"] == "入场不可成交"
    assert result["stage"] == "entry"


def test_classify_cost_drag_reason():
    result = classify_false_positive(reason="成本后净收益不为正", executed=True, net_return_pct=-0.2)

    assert result["category"] == "cost_drag"
    assert result["category_label"] == "成本滑点吞噬"
    assert result["stage"] == "post_trade"


def test_classify_market_context_reason():
    result = classify_false_positive(reason="大盘分歧放大", net_return_pct=-1.1)

    assert result["category"] == "market_context"
    assert result["category_label"] == "市场环境拖累"
    assert result["stage"] == "context"


def test_classify_signal_invalid_reason():
    result = classify_false_positive(reason="信号当时真实不成立，但后验收益为正", executed=True, net_return_pct=1.2)

    assert result["category"] == "signal_invalid"
    assert result["category_label"] == "信号当时不成立"
    assert result["stage"] == "signal"

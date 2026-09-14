from app.engine.aggregator import _compute_strategy_rule_score


def test_single_low_weight_signal_keeps_score_scale():
    assert _compute_strategy_rule_score([{"score": 90, "weight": 0.2}]) == 90
    assert _compute_strategy_rule_score([
        {"score": 90, "weight": 0.2}, {"score": 60, "weight": 0.3}
    ]) == 72
    assert _compute_strategy_rule_score([{"score": 90, "weight": 0}]) == 50

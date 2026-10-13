from app.engine.drawdown_control import (
    DrawdownControlConfig, DrawdownControlState, apply_drawdown_control, record_day,
)


def test_disabled_passthrough():
    s = DrawdownControlState()
    out, why = apply_drawdown_control(["a", "b"], regime="弱势", day_index=0, state=s, cfg=DrawdownControlConfig())
    assert out == ["a", "b"] and why is None


def test_weak_throttle():
    cfg = DrawdownControlConfig(enabled=True, weak_max_tickets=1)
    s = DrawdownControlState()
    assert apply_drawdown_control(["a", "b"], regime="弱势", day_index=0, state=s, cfg=cfg) == (["a"], "weak_market_throttle")
    assert apply_drawdown_control(["a", "b"], regime="震荡", day_index=0, state=s, cfg=cfg)[0] == ["a", "b"]


def test_breaker_uses_only_settled_days():
    cfg = DrawdownControlConfig(enabled=True, breaker_threshold=-5, lookback_days=4, settle_lag_days=6, pause_days=2)
    s = DrawdownControlState()
    for i in range(4):
        record_day(s, i, [-3.0])
    # day 8: settled = indices <= 2 -> 3 days, sum -9 -> trips
    assert apply_drawdown_control(["a"], regime="震荡", day_index=8, state=s, cfg=cfg) == ([], "drawdown_breaker_tripped")
    assert apply_drawdown_control(["a"], regime="震荡", day_index=9, state=s, cfg=cfg) == ([], "drawdown_breaker_paused")
    # day 7: settled indices <= 1 -> 2 days < min 3 -> not tripped
    s2 = DrawdownControlState(history=list(s.history))
    assert apply_drawdown_control(["a"], regime="震荡", day_index=7, state=s2, cfg=cfg)[0] == ["a"]

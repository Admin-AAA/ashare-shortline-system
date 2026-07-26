from app.services.execution_confirmation import _derive_decisions


def _signal(**overrides):
    signal = {
        "hard_veto": False,
        "veto_reason": "",
        "score": 72.0,
        "setup_match": "平台突破",
        "entry_rule": "放量突破平台",
    }
    signal.update(overrides)
    return signal


def test_fresh_next_day_support_allows_entry_and_holding():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错", "summary": "主线和买点仍成立"},
        current_price=15.2,
        stop_loss=14.5,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "hold"


def test_strategy_veto_cancels_entry_and_exits_holding():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(hard_veto=True, veto_reason="非主线热点"),
        review={"stance": "veto", "action": "不做"},
        current_price=15.0,
        stop_loss=14.5,
    )

    assert entry == {"code": "cancel", "label": "取消买入", "reason": "非主线热点"}
    assert holding["code"] == "exit"


def test_stale_snapshot_never_emits_actionable_buy_or_sell():
    entry, holding = _derive_decisions(
        fresh=False,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错"},
        current_price=15.2,
        stop_loss=14.5,
    )

    assert entry["code"] == "not_ready"
    assert holding["code"] == "observe"


def test_old_recommendation_cannot_be_reopened_even_if_strategy_recovers():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=2,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错"},
        current_price=15.2,
        stop_loss=14.5,
    )

    assert entry["code"] == "expired"
    assert holding["code"] == "hold"


def test_original_stop_loss_overrides_supportive_agent_review():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(),
        review={"stance": "support", "action": "轻仓试错"},
        current_price=14.4,
        stop_loss=14.5,
    )

    assert entry["code"] == "cancel"
    assert holding["code"] == "exit"


def test_agent_failure_uses_high_confidence_rule_signal():
    entry, holding = _derive_decisions(
        fresh=True,
        signal_age_trade_days=1,
        entry_window_closed=False,
        prefilter_ok=True,
        signal=_signal(score=75.0),
        review={"status": "fallback", "error": "provider unavailable"},
        current_price=15.2,
        stop_loss=14.5,
    )

    assert entry["code"] == "buy"
    assert holding["code"] == "hold"

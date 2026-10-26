"""全日累计量在盘中的可判定性判据。

核心断言：下限类门槛在盘中未达标时不可判定（全日仍可能达标），上限类门槛已越界时可以
立即判定（当日累计量只增不减），与累计量无关的条件不受影响。
"""

from app.services.intraday_cumulative_policy import intraday_cumulative_inconclusive


def _detail(expression: str, observed: dict) -> dict:
    return {
        "passed": False,
        "scope": "universe_include",
        "code": "universe_include",
        "label": "测试条件",
        "expression": expression,
        "observed": observed,
    }


def test_lower_bound_on_session_amount_is_inconclusive_during_session():
    """开盘十几分钟累计 2.601 亿低于 3 亿下限，全日仍可能达标，盘中不可判定。"""
    detail = _detail("amount_yi >= 3", {"amount_yi": 2.601})

    assert intraday_cumulative_inconclusive(detail) is True


def test_upper_bound_already_breached_is_conclusive():
    """累计成交额已越过 15 亿上限，当日累计量只增不减，可以立即判定否决。"""
    detail = _detail("amount_yi < 15", {"amount_yi": 18.4})

    assert intraday_cumulative_inconclusive(detail) is False


def test_interval_rule_still_reachable_is_inconclusive():
    """区间门槛在当前累计值之上仍存在可达的通过区间时，同样不可判定。"""
    detail = _detail("amount_yi >= 3 and amount_yi < 15", {"amount_yi": 0.424})

    assert intraday_cumulative_inconclusive(detail) is True


def test_non_cumulative_condition_is_conclusive():
    """20 日长上影次数与当日累计量无关，失败就是失败。"""
    detail = _detail("upper_shadow_count20 <= 6", {"upper_shadow_count20": 10})

    assert intraday_cumulative_inconclusive(detail) is False


def test_compound_rule_blocked_by_non_cumulative_term_is_conclusive():
    """累计量再大也过不了的复合条件必须判定为真失败，不能借累计量蒙混过关。"""
    detail = _detail(
        "amount_yi >= 3 and upper_shadow_count20 <= 6",
        {"amount_yi": 0.5, "upper_shadow_count20": 10},
    )

    assert intraday_cumulative_inconclusive(detail) is False


def test_turnover_rate_lower_bound_is_inconclusive():
    """换手率同属当日累计量，早盘偏低不构成取消依据。"""
    detail = _detail("turnover_rate >= 1.5", {"turnover_rate": 0.31})

    assert intraday_cumulative_inconclusive(detail) is True


def test_include_any_disjunction_reachable_is_inconclusive():
    """include_any 的或表达式里只要存在一支靠累计量能满足，就不可判定。"""
    detail = _detail(
        "(is_main_theme == true and breakout20 == 1) or "
        "(industry_capacity_like_count >= 2 and pct >= 2 and amount_yi >= 4)",
        {
            "is_main_theme": False,
            "breakout20": 0,
            "industry_capacity_like_count": 3,
            "pct": 3.4,
            "amount_yi": 1.2,
        },
    )

    assert intraday_cumulative_inconclusive(detail) is True


def test_passed_detail_is_never_inconclusive():
    detail = {**_detail("amount_yi >= 3", {"amount_yi": 9.0}), "passed": True}

    assert intraday_cumulative_inconclusive(detail) is False


def test_missing_detail_is_never_inconclusive():
    assert intraday_cumulative_inconclusive(None) is False
    assert intraday_cumulative_inconclusive({}) is False
    assert intraday_cumulative_inconclusive(_detail("", {"amount_yi": 1.0})) is False


# ── 硬否决同样认「盘中累计量还不能下结论」 ───────────────────────────────────

def _veto_skill(when: str):
    from types import SimpleNamespace
    return SimpleNamespace(
        skill_id="veto-test", name="否决测试", version="1", contract_hash="test", contract={
            "phases": [{"name": "常规", "when": "price > 0"}, {"name": "缩量", "when": when, "hard_veto": True}],
            "scoring": {"base": 60},
        },
    )


def _stock(**fields):
    return {"code": "600000", "name": "测试", "industry": "银行", "price": 10.0, **fields}


def test_hard_veto_on_session_amount_shortfall_is_inconclusive():
    from app.services.intraday_cumulative_policy import hard_veto_intraday_inconclusive
    assert hard_veto_intraday_inconclusive(_veto_skill("amount_yi < 3"), _stock(amount_yi=1.83), []) is True


def test_hard_veto_on_breached_upper_bound_is_conclusive():
    from app.services.intraday_cumulative_policy import hard_veto_intraday_inconclusive
    assert hard_veto_intraday_inconclusive(_veto_skill("amount_yi > 15"), _stock(amount_yi=18.0), []) is False


def test_structural_hard_veto_is_conclusive():
    from app.services.intraday_cumulative_policy import hard_veto_intraday_inconclusive
    assert hard_veto_intraday_inconclusive(
        _veto_skill("upper_shadow_count20 > 6"), _stock(amount_yi=1.0, upper_shadow_count20=10), [],
    ) is False


def test_inconclusive_hard_veto_does_not_exit_holding():
    from app.services.execution_confirmation import _derive_decisions
    base = {"hard_veto": True, "veto_reason": "缩量", "score": 72.0, "setup_match": "x"}
    _, conclusive = _derive_decisions(
        fresh=True, signal_age_trade_days=1, entry_window_closed=False, prefilter_ok=True,
        signal=base, review=None, current_price=10.0, stop_loss=9.0,
    )
    _, pending = _derive_decisions(
        fresh=True, signal_age_trade_days=1, entry_window_closed=False, prefilter_ok=True,
        signal={**base, "hard_veto_inconclusive": True}, review=None, current_price=10.0, stop_loss=9.0,
    )
    assert conclusive["code"] == "exit"
    assert pending["code"] != "exit"

"""RSI6 恐慌出清（命令式观察策略）与逐策略出场覆盖回归。"""
from __future__ import annotations

from app.core.config import get_settings
from app.engine.imperative_strategy import DescriptorBoundImperativeStrategy
from app.engine.signal import ENTRY_MODE_NEXT_OPEN
from app.engine.skill_loader import load_skill
from app.engine.strategy import StrategyContext, instantiate_strategy
from app.services.entry_policy import execution_policy_for_next_open_entry
from app.services.outcome_simulation import _backtest_stop_loss_price, _resolve_take_profit_price
from app.services.strategy_exit_overrides import (
    apply_exit_overrides,
    exit_overrides_for,
    load_exit_overrides,
)

_SKILL_ID = "rsi6-capitulation"


def _stock(**extra) -> dict:
    base = {
        "code": "600000",
        "name": "测试股",
        "amount_yi": 20.0,
        "is_st": False,
        "is_limit_up": False,
        "mkt_rsi6": 10.0,
        "mkt_lu_ld": 0.2,
        "limit_up_count": 10,
        "limit_down_count": 50,
    }
    base.update(extra)
    return base


def test_real_skill_loads_as_imperative_and_declares_exit_overrides():
    skill = load_skill(get_settings().skills_dir / _SKILL_ID)
    assert skill.skill_id == _SKILL_ID
    assert skill.mode == "imperative"
    assert skill.executable is True
    assert skill.contract is not None
    load_exit_overrides.cache_clear()
    assert exit_overrides_for(_SKILL_ID) == {
        "take_profit_pct": 5.0,
        "stop_loss_pct": 4.5,
        "activation_gain_pct": 99.0,
    }


def test_trigger_only_on_rsi6_oversold_and_limit_down_dominance():
    skill = load_skill(get_settings().skills_dir / _SKILL_ID)
    strategy = instantiate_strategy(skill)
    assert isinstance(strategy, DescriptorBoundImperativeStrategy)
    ctx = StrategyContext()

    hit = strategy.evaluate(_stock(), ctx)
    assert hit["score"] >= 80.0
    assert hit["phase"] == "RSI6恐慌出清"
    assert hit["entry_mode"] == ENTRY_MODE_NEXT_OPEN
    assert "RSI6超卖+跌停家数占优" == hit["setup_match"]

    for patch in (
        {"mkt_rsi6": 0.0},      # 缺数据/未超卖
        {"mkt_rsi6": 30.0},     # 未超卖
        {"mkt_lu_ld": 0.5},     # 涨停家数占优，不是恐慌
    ):
        miss = strategy.evaluate(_stock(**patch), ctx)
        assert miss["score"] == 0.0
        assert miss["phase"] == "市场未触发"


def test_strategy_exit_override_aligns_tp5_stop45_and_disables_trailing():
    p = execution_policy_for_next_open_entry()
    p2 = apply_exit_overrides(p, _SKILL_ID)
    assert p2.take_profit_pct == 5.0
    assert p2.stop_loss_pct == 4.5
    assert p2.trailing.activation_gain_pct == 99.0
    assert p2.horizon_days == 5


def test_settlement_helpers_accept_strategy_overrides():
    assert _resolve_take_profit_price(10.0, {}, take_profit_pct=5.0) == 10.5
    assert abs(_backtest_stop_loss_price(10.0, stop_loss_pct=4.5) - 9.55) < 1e-9

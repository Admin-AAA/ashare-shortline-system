"""策略回测档案必须使用策略自己声明的入场方式。"""
from __future__ import annotations

from pathlib import Path

import app.services.backtest.strategy_profile as strategy_profile_module
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY, EntryPriceSource
from app.engine.skill_loader import load_skill
from app.engine.backtest import BarInput
from app.services.backtest.strategy_profile import StrategyBacktestProfiles
from app.services.backtest.policy_experiment import ScoredCandidateDay, _simulate_card
from app.services.entry_policy import (
    execution_policy_for_entry_mode,
    execution_policy_for_next_open_entry,
    execution_policy_with_entry_mode,
    execution_policy_fingerprint_for_entry_policy,
)


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
NEXT_OPEN_SKILLS = ("chao-gu-yang-jia-skill",)
TARGET_TOUCH_SKILL = "trend-pullback-continuation"


def test_next_open_entry_keeps_exit_contract_of_current_policy() -> None:
    policy = execution_policy_for_entry_mode("next_open")

    assert policy.entry.price_source is EntryPriceSource.NEXT_TRADING_DAY_OPEN
    assert policy.entry.max_entry_trade_days == 1
    assert policy.entry.allow_target_touch is False
    assert policy.horizon_days == DEFAULT_EXECUTION_POLICY.horizon_days
    assert policy.stop_loss == DEFAULT_EXECUTION_POLICY.stop_loss
    assert policy.trailing == DEFAULT_EXECUTION_POLICY.trailing
    assert policy.fees == DEFAULT_EXECUTION_POLICY.fees
    assert policy.position == DEFAULT_EXECUTION_POLICY.position
    assert policy.fingerprint() == execution_policy_fingerprint_for_entry_policy(
        "next_trading_day_open_v1"
    )
    assert execution_policy_for_entry_mode("target_touch") == DEFAULT_EXECUTION_POLICY
    assert execution_policy_for_entry_mode(None) == DEFAULT_EXECUTION_POLICY


def test_current_targets_use_declared_entry_mode(monkeypatch) -> None:
    skills = [
        load_skill(path)
        for path in sorted(SKILLS_DIR.iterdir())
        if path.is_dir() and (path / "strategy.yaml").exists()
    ]
    monkeypatch.setattr(strategy_profile_module, "sync_skills", lambda _db: list(skills))
    profiles = StrategyBacktestProfiles(object())  # type: ignore[arg-type]
    monkeypatch.setattr(
        profiles,
        "_latest_fully_settleable_signal_date",
        lambda _policy, as_of=None: "2026-09-11",
    )

    targets = {
        target.strategy_id: target for target in profiles.current_targets()
    }

    next_open_fingerprint = execution_policy_for_next_open_entry().fingerprint()
    for skill_id in NEXT_OPEN_SKILLS:
        policy = targets[skill_id].execution_policy
        assert policy.entry.price_source is EntryPriceSource.NEXT_TRADING_DAY_OPEN
        assert policy.fingerprint() == next_open_fingerprint
    assert (
        targets[TARGET_TOUCH_SKILL].execution_policy.fingerprint()
        == DEFAULT_EXECUTION_POLICY.fingerprint()
    )


def test_simulate_card_uses_declared_entry_mode() -> None:
    bars = (
        BarInput("000001", "2026-06-02", 12.0, 12.4, 11.8, 12.2),
        BarInput("000001", "2026-06-03", 10.5, 10.8, 10.3, 10.6),
        BarInput("000001", "2026-06-04", 10.6, 10.9, 10.4, 10.8),
    )
    day = ScoredCandidateDay(
        signal_date="2026-06-01",
        market_snapshot={},
        cards=(),
        outcome_bars={"000001": bars},
    )
    base_card = {"code": "000001", "recommend_price": 10.0, "stop_loss": 9.0}

    touch_trade = _simulate_card(
        day, {**base_card, "entry_mode": "target_touch"}, DEFAULT_EXECUTION_POLICY
    )["trade"]
    next_open_trade = _simulate_card(
        day, {**base_card, "entry_mode": "next_open"}, DEFAULT_EXECUTION_POLICY
    )["trade"]

    # 首日跳空超过 6% 上限：次日开盘入场（等待窗口 1 天）直接放弃，
    # 买点触碰方式（等待窗口 2 天）继续看下一根。
    assert touch_trade.entry_date == "2026-06-03"
    assert next_open_trade.entry_date is None


def test_execution_policy_with_entry_mode_keeps_exit_and_cost() -> None:
    policy = execution_policy_with_entry_mode(DEFAULT_EXECUTION_POLICY, "next_open")

    assert policy.entry.price_source is EntryPriceSource.NEXT_TRADING_DAY_OPEN
    assert policy.take_profit == DEFAULT_EXECUTION_POLICY.take_profit
    assert policy.gap_risk == DEFAULT_EXECUTION_POLICY.gap_risk
    assert policy.slippage == DEFAULT_EXECUTION_POLICY.slippage

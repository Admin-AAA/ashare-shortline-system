"""策略回测档案必须使用策略自己声明的入场方式。"""
from __future__ import annotations

from pathlib import Path

import app.services.backtest.strategy_profile as strategy_profile_module
from app.engine.execution_policy import DEFAULT_EXECUTION_POLICY, EntryPriceSource
from app.engine.skill_loader import load_skill
from app.services.backtest.strategy_profile import StrategyBacktestProfiles
from app.services.entry_policy import (
    execution_policy_for_entry_mode,
    execution_policy_for_next_open_entry,
    execution_policy_fingerprint_for_entry_policy,
)


SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
NEXT_OPEN_SKILLS = ("chao-gu-yang-jia-skill", "daily-stock-analysis-skill")
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

from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.engine.skill_loader import validate_skill
from app.engine.strategy_runner import run_strategy
from app.models.tables import DailyKbar, DragonTiger, StockMetadataHistory
from scripts.research_dragon_tiger_reaccumulation import load_evaluator, metadata_on


@pytest.mark.parametrize("code,signal_date", [
    ("000737", "2025-02-05"),
    ("603333", "2026-01-19"),
    ("002300", "2026-08-04"),
])
def test_real_history_signal_excludes_future_and_same_day_events(code, signal_date):
    evaluator = load_evaluator()
    with SessionLocal() as db:
        bars = list(db.scalars(select(DailyKbar).where(
            DailyKbar.code == code, DailyKbar.trade_date >= "2025-01-01",
            DailyKbar.trade_date <= "2026-09-18",
        ).order_by(DailyKbar.trade_date)))
        events = list(db.scalars(select(DragonTiger).where(
            DragonTiger.code == code, DragonTiger.trade_date <= "2026-09-18",
        ).order_by(DragonTiger.trade_date)))
        calendar = list(db.scalars(select(DailyKbar.trade_date).where(
            DailyKbar.trade_date >= "2025-01-01", DailyKbar.trade_date <= "2026-09-18",
        ).distinct().order_by(DailyKbar.trade_date)))
        metadata = metadata_on(list(db.scalars(select(StockMetadataHistory).where(
            StockMetadataHistory.code == code,
        ))), signal_date)
    full = evaluator.build_context(code, signal_date, bars, events, metadata, calendar)
    bounded = evaluator.build_context(
        code, signal_date,
        [bar for bar in bars if bar.trade_date <= signal_date],
        [event for event in events if event.trade_date < signal_date],
        metadata, [day for day in calendar if day <= signal_date],
    )
    assert any(bar.trade_date > signal_date for bar in bars)
    assert full == bounded
    assert full["dtr_data_ready"]
    assert full["dtr_event_date"] < signal_date
    assert all(day < signal_date for day in full["dtr_limit_dates"])
    signal = run_strategy(evaluator.SKILL, full, [])
    assert signal["setup_match"] == "缩量整理后温和放量"
    assert not signal["hard_veto"]
    assert signal["entry_mode"] == evaluator.SKILL.contract["default_entry_mode"]

    if code == "002300":
        event_day = "2026-07-24"
        assert any(event.trade_date == event_day for event in events)
        on_listing_day = evaluator.build_context(code, event_day, bars, events, metadata, calendar)
        known_events = [event for event in events if "2026-06-24" <= event.trade_date < event_day]
        assert on_listing_day["dtr_event_count"] == len(known_events)

    missing_day = full["dtr_history_start"]
    incomplete = evaluator.build_context(
        code, signal_date, [bar for bar in bars if bar.trade_date != missing_day],
        events, metadata, calendar,
    )
    assert not incomplete["dtr_data_ready"]
    assert run_strategy(evaluator.SKILL, incomplete, [])["hard_veto"]


def test_research_strategy_is_disabled_and_requires_historical_evidence():
    skill = load_evaluator().SKILL
    assert validate_skill(skill) == []
    assert skill.status == "disabled"
    assert skill.enabled is False
    assert skill.contract["enabled"] is False
    assert "default_preset_groups" not in skill.contract["meta"]
    assert run_strategy(skill, {}, [])["hard_veto"]
    assert (Path(skill.path) / "evaluator.py").is_file()

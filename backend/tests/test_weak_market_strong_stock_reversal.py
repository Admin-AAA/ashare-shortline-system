"""Weak-market confirmed-reversal strategy and research-context regressions."""
from __future__ import annotations

from types import SimpleNamespace

from app.core.config import get_settings
from app.engine.skill_loader import load_skills
from app.engine.strategy_runner import run_strategy
from app.services.backtest.research_runner import _historical_market_context
from app.services.factors.registry import bootstrap_default_factor_registry
from app.services.feature_builder import build_features


def _skill():
    return next(
        skill
        for skill in load_skills(get_settings().skills_dir)
        if skill.skill_id == "weak-market-strong-stock-reversal"
    )


def _stock(**overrides):
    stock = {
        "code": "000001",
        "name": "测试股",
        "industry": "电子",
        "is_st": False,
        "market_regime": "弱势",
        "limit_down_count": 20,
        "amount_yi": 4.0,
        "turnover_rate": 4.0,
        "chg20": 21.0,
        "pos60": 72.0,
        "off_high_pct": -5.0,
        "pct": 0.8,
        "twenty_cm_risk_gate": "normal",
        "announcement_negative_count": 0,
        "news_risk_count": 0,
        "max_dd20": -6.0,
        "vol5_20": 0.8,
        "support_ma5": True,
        "support_ma10": True,
        "ma5": 11.0,
        "ma10": 10.5,
        "ma20": 10.0,
        "rsi_12": 58.0,
        "dsa_macd_score": 10.0,
        "market_sample_count": 1000,
        "data_completeness_score": 100.0,
        "evidence_quality_score": 100.0,
    }
    stock.update(overrides)
    return stock


def test_weak_market_strong_stock_confirmation_is_actionable_but_panic_is_not() -> None:
    skill = _skill()

    confirmed = run_strategy(skill, _stock(), [])
    panic = run_strategy(skill, _stock(limit_down_count=101), [])

    assert confirmed["hard_veto"] is False
    assert confirmed["setup_match"] == "强势股缩量止跌确认"
    assert confirmed["score"] >= 70
    assert panic["hard_veto"] is True
    assert panic["score"] == 0


def test_research_market_context_uses_full_cross_section_not_target_stock() -> None:
    rows = [SimpleNamespace(pct_chg=-1.0) for _ in range(70)] + [
        SimpleNamespace(pct_chg=1.0) for _ in range(20)
    ] + [SimpleNamespace(pct_chg=-10.0) for _ in range(10)]

    context = _historical_market_context(rows)

    assert context == {
        "sample_count": 100,
        "up_count": 20,
        "down_count": 80,
        "limit_up_count": 0,
        "limit_down_count": 10,
        "regime": "弱势",
    }


def test_live_feature_context_exposes_market_panic_fields_to_strategy() -> None:
    bootstrap_default_factor_registry()
    enriched = build_features(
        [_stock()],
        {
            "sample_count": 200,
            "up_count": 50,
            "down_count": 145,
            "limit_up_count": 2,
            "limit_down_count": 35,
            "main_industries": ["电子"],
            "breadth": {"regime": "弱势"},
        },
    )[0]

    assert enriched["market_regime"] == "弱势"
    assert enriched["limit_down_count"] == 35
    assert enriched["up_count"] == 50

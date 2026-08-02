"""推荐详情必须读取当前版本 canonical 回测画像，而非混合历史推荐。"""
from __future__ import annotations

from app.services.backtest.strategy_profile import StrategyBacktestProfiles
from app.services.outcome_service import strategy_perf_summary


def test_strategy_perf_summary_projects_current_version_backtest_profile(
    db, monkeypatch
) -> None:
    target = object()
    profile = {
        "strategy_version": "current-v3",
        "profile_fingerprint": "a" * 64,
        "backtest_id": "btr-profile",
        "portfolio_model": "capped_daily_exit_cohort_v1",
        "sample_count": 12,
        "executed_trade_count": 10,
        "win_count": 6,
        "win_rate_pct": 60.0,
        "average_net_return_pct": 1.25,
        "profit_loss_ratio": 1.8,
        "total_return_pct": 12.4,
        "max_drawdown_pct": 6.2,
        "monthly_returns": [
            {"month": "2026-06", "return_pct": 2.5, "executed_trade_count": 3}
        ],
        "date_from": "2026-01-01",
        "date_to": "2026-06-30",
        "generated_at": "2026-07-01T09:05:00",
    }
    monkeypatch.setattr(StrategyBacktestProfiles, "current_target", lambda self, strategy_id: target)
    monkeypatch.setattr(StrategyBacktestProfiles, "read", lambda self, value: profile)

    result = strategy_perf_summary(db, "daily-stock-analysis-skill")

    assert result is not None
    assert result["source"] == "canonical_backtest"
    assert result["strategy_version"] == "current-v3"
    assert result["labeled_count"] == 10
    assert result["hit_rate"] == 60.0
    assert result["total_return_pct"] == 12.4
    assert result["monthly_returns"] == profile["monthly_returns"]
    assert result["window_days"] == 180


def test_strategy_perf_summary_never_falls_back_when_profile_is_missing(
    db, monkeypatch
) -> None:
    monkeypatch.setattr(
        StrategyBacktestProfiles,
        "current_target",
        lambda self, strategy_id: object(),
    )
    monkeypatch.setattr(StrategyBacktestProfiles, "read", lambda self, target: None)

    assert strategy_perf_summary(db, "daily-stock-analysis-skill") is None


def test_strategy_perf_summary_empty_strategy_id_returns_none(db) -> None:
    assert strategy_perf_summary(db, "") is None
    assert strategy_perf_summary(db, None) is None  # type: ignore[arg-type]

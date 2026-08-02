from __future__ import annotations

from app.engine.backtest import ExecutionPolicy
from app.services.backtest.strategy_profile import StrategyBacktestProfileTarget
from scripts.refresh_strategy_backtest_profiles import build_parser, refresh_profiles


def _target(strategy_id: str) -> StrategyBacktestProfileTarget:
    return StrategyBacktestProfileTarget(
        strategy_id=strategy_id,
        strategy_version=f"{strategy_id}-v1",
        date_from="2026-01-01",
        date_to="2026-06-30",
        parameter_set={"minimum_signal_score": 72.0},
        execution_policy=ExecutionPolicy(),
    )


class FakeProfiles:
    def __init__(self) -> None:
        self.targets = (_target("cached"), _target("first-missing"), _target("deferred"))
        self.refreshed: list[str] = []

    def current_targets(self, *, as_of=None):
        assert as_of == "2026-07-31"
        return self.targets

    def read(self, target):
        if target.strategy_id == "cached":
            return {
                "backtest_id": "btr-cached",
                "profile_fingerprint": "a" * 64,
            }
        return None

    def refresh(self, target):
        self.refreshed.append(target.strategy_id)
        return {
            "status": "completed",
            "reused": False,
            "profile": {
                "backtest_id": f"btr-{target.strategy_id}",
                "profile_fingerprint": "b" * 64,
            },
        }


def test_high_frequency_refresh_reuses_cache_and_executes_at_most_one_missing_profile() -> None:
    profiles = FakeProfiles()

    result = refresh_profiles(
        profiles,  # type: ignore[arg-type]
        as_of="2026-07-31",
        max_runs=1,
    )

    assert result["status"] == "completed"
    assert result["executed_runs"] == 1
    assert profiles.refreshed == ["first-missing"]
    assert [item["status"] for item in result["profiles"]] == [
        "completed",
        "completed",
        "deferred",
    ]


def test_help_parsing_has_no_database_dependency() -> None:
    args = build_parser().parse_args(["--max-runs", "1", "--strategy-id", "alpha"])
    assert args.max_runs == 1
    assert args.strategy_id == ["alpha"]

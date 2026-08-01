from __future__ import annotations

from unittest.mock import patch

from app.services.backtest.comparison import run_p0_comparison


def _arm(backtest_id: str, score: float) -> dict:
    metrics = {
        "sample_count": 6,
        "executed_trade_count": 6,
        "unexecuted_count": 0,
        "win_count": 3,
        "loss_count": 3,
        "win_rate_pct": 50.0,
        "average_net_return_pct": score,
        "profit_loss_ratio": 1.2,
        "total_net_return_pct": score * 6,
        "max_drawdown_pct": 2.0,
        "confidence": "insufficient",
    }
    return {
        "backtest_id": backtest_id,
        "strategy_id": "fixture",
        "strategy_version": "v1",
        "configuration_fingerprint": score.hex().replace(".", "0").ljust(64, "0")[:64],
        "metrics": metrics,
        "split_metrics": {
            "train": metrics,
            "validation": metrics,
            "out_of_sample": metrics,
        },
        "cross_validation": {"passed": True},
    }


def test_comparison_endpoint_adapter_projects_the_canonical_decision() -> None:
    artifact = {
        "schema_version": 1,
        "experiment_id": "btexp_fixture",
        "status": "completed",
        "hypothesis": "fixture",
        "shared_snapshot_id": "bts_fixture",
        "split": {
            "train": ["2026-01-01"],
            "validation": ["2026-01-02"],
            "out_of_sample": ["2026-01-03"],
        },
        "arms": {
            "champion": _arm("btr_champion", 0.1),
            "challenger": _arm("btr_challenger", 0.3),
        },
        "truth_validation": {
            "passed": True,
            "validators": {"champion_vectorbt": True, "challenger_vectorbt": True},
        },
        "decision": {
            "decision": "PRODUCTION",
            "should_publish": True,
            "improvements": ["average_net_return_pct"],
            "regressions": [],
            "deltas": {"average_net_return_pct": 0.2},
        },
        "evidence_fingerprint": "e" * 64,
    }

    with patch(
        "app.services.backtest.comparison.CanonicalBacktestExperiment.run",
        return_value=artifact,
    ):
        result = run_p0_comparison(
            object(),
            "2026-01-01",
            "2026-01-03",
            strategy_id="fixture",
        )

    assert result["comparison"]["selected_variant"] == "candidate"
    assert result["comparison"]["decision"] == artifact["decision"]
    assert result["comparison"]["validation_profile"]["truth_validation"]["passed"] is True
    assert result["evidence_artifact"] == artifact
    assert result["result_source"] == "canonical_backtest_experiment"

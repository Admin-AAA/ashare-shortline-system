from __future__ import annotations

from scripts.run_canonical_backtest_experiment import build_parser, build_spec


def test_cli_builds_one_comparable_parameter_experiment() -> None:
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-03-31",
            "--strategy-id",
            "daily-stock-analysis-skill",
            "--champion-params",
            '{"minimum_signal_score":72}',
            "--challenger-params",
            '{"minimum_signal_score":65}',
            "--execution-policy",
            '{"horizon_days":5,"entry_slippage_bps":10,"exit_slippage_bps":10}',
            "--hypothesis",
            "降低评分线是否改善正式净收益",
            "--target-code",
            "000001",
        ]
    )

    spec = build_spec(args)

    assert spec.experiment_id.startswith("btexp_")
    assert spec.champion.parameter_set == {"minimum_signal_score": 72}
    assert spec.challenger.parameter_set == {"minimum_signal_score": 65}
    assert spec.champion.execution_policy == spec.challenger.execution_policy
    assert spec.champion.target_codes == spec.challenger.target_codes == ("000001",)

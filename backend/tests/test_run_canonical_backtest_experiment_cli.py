from __future__ import annotations

import json

from app.engine.backtest import BarInput
from app.services.backtest.policy_experiment import CanonicalPolicyExperimentSpec
from app.services.backtest.policy_experiment import ScoredCandidateSnapshot
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


def test_cli_builds_one_rule_production_policy_spec(tmp_path) -> None:
    days = []
    for index in range(24):
        signal_day = f"2026-01-{index + 1:02d}"
        outcome_day = f"2026-02-{index + 1:02d}"
        bar = BarInput("300001", outcome_day, 10, 11, 9, 10.5)
        days.append(
            {
                "signal_date": signal_day,
                "market_snapshot": {
                    "sample_count": 100,
                    "market_regime": "结构性强势",
                },
                "cards": [
                    {"code": "300001", "final_score": 90, "recommend_price": 10}
                ],
                "outcome_bars": {"300001": [bar.canonical_payload()]},
            }
        )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "production-rule-replay",
                "days": days,
            }
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(
        [
            "--experiment-kind",
            "production-policy",
            "--candidate-snapshot-file",
            str(snapshot),
            "--challenger-policy-patch",
            '{"structural_strong_hard_veto":true}',
            "--hypothesis",
            "收紧结构性强势 veto",
        ]
    )

    spec = build_spec(args)

    assert isinstance(spec, CanonicalPolicyExperimentSpec)
    assert spec.changed_rule_id == "structural_strong_hard_veto"


def test_cli_can_build_rule_replay_snapshot_without_live_persistence(
    tmp_path, monkeypatch
) -> None:
    payload = {
        "schema_version": 1,
        "source": "production-rule-replay",
        "days": [
            {
                "signal_date": f"2026-03-{day:02d}",
                "market_snapshot": {"sample_count": 1, "market_regime": "弱势"},
                "cards": [],
                "outcome_bars": {},
            }
            for day in range(1, 25)
        ],
    }
    frozen = ScoredCandidateSnapshot.from_payload(payload)
    calls = []

    class FakeBuilder:
        def __init__(self, db):
            calls.append(db)

        def build(self, **kwargs):
            calls.append(kwargs)
            return frozen

    monkeypatch.setattr(
        "scripts.run_canonical_backtest_experiment.ProductionRuleReplayCandidateBuilder",
        FakeBuilder,
    )
    output = tmp_path / "generated.json"
    args = build_parser().parse_args(
        [
            "--experiment-kind", "production-policy",
            "--start", "2026-03-01",
            "--end", "2026-03-24",
            "--candidate-snapshot-output", str(output),
            "--challenger-policy-patch", '{"oscillation_hard_veto":true}',
            "--hypothesis", "收紧震荡 veto",
        ]
    )
    db = object()
    spec = build_spec(args, db)

    assert spec.candidate_snapshot.fingerprint == frozen.fingerprint
    assert calls[0] is db
    assert output.exists()

#!/usr/bin/env python3
"""Run the five pre-registered selection-policy recovery experiments as FINDINGs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.engine.backtest import ExecutionPolicy  # noqa: E402
from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY  # noqa: E402
from app.services.backtest.policy_experiment import (  # noqa: E402
    CanonicalPolicyExperimentSpec,
    ProductionPolicyBacktestRunner,
    ScoredCandidateSnapshot,
)
from app.services.backtest.research_repository import ResearchBacktestRepository  # noqa: E402


def recovery_policy_patches() -> tuple[tuple[str, str, Any], ...]:
    excluded = list(DEFAULT_PRODUCTION_SELECTION_POLICY.excluded_20cm_buckets)
    return (
        ("structural_strong_veto_off", "structural_strong_hard_veto", False),
        ("oscillation_veto_off", "oscillation_hard_veto", False),
        ("all_boards", "board_universe", "all"),
        (
            "restore_daily_stock_analysis_bucket",
            "excluded_strategy_buckets",
            [item for item in excluded if item != "daily-stock-analysis-skill"],
        ),
        (
            "restore_chao_gu_yang_jia_bucket",
            "excluded_strategy_buckets",
            [item for item in excluded if item != "chao-gu-yang-jia-skill"],
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-snapshot-file", type=Path, required=True)
    parser.add_argument("--execution-policy", default="{}")
    parser.add_argument("--score-line", type=float, default=72.0)
    parser.add_argument("--output-count", type=int, default=6)
    parser.add_argument("--experiment-prefix", default="policy_recovery")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.loads(args.candidate_snapshot_file.read_text(encoding="utf-8"))
    snapshot = ScoredCandidateSnapshot.from_payload(payload)
    if snapshot.source != "production-rule-replay":
        raise ValueError("formal recovery requires production-rule-replay snapshot")
    execution_overrides = json.loads(args.execution_policy)
    if not isinstance(execution_overrides, dict):
        raise ValueError("--execution-policy must be a JSON object")
    session = SessionLocal()
    try:
        runner = ProductionPolicyBacktestRunner(ResearchBacktestRepository(session))
        findings = []
        for label, rule_id, value in recovery_policy_patches():
            challenger = DEFAULT_PRODUCTION_SELECTION_POLICY.with_rule_patch(rule_id, value)
            artifact = runner.run(
                CanonicalPolicyExperimentSpec(
                    experiment_id=f"{args.experiment_prefix}_{label}",
                    hypothesis=f"单独修改 {rule_id} 能否恢复 OOS 正期望与策略活性",
                    candidate_snapshot=snapshot,
                    champion_policy=DEFAULT_PRODUCTION_SELECTION_POLICY,
                    challenger_policy=challenger,
                    changed_rule_id=rule_id,
                    score_line=args.score_line,
                    output_count=args.output_count,
                    execution_policy=ExecutionPolicy(**execution_overrides),
                )
            )
            oos = (artifact.get("arms") or {}).get("challenger", {}).get("split_metrics", {}).get("out_of_sample", {})
            coverage = (artifact.get("arms") or {}).get("challenger", {}).get("split_coverage_evidence", {}).get("out_of_sample", {})
            findings.append(
                {
                    "label": label,
                    "rule_id": rule_id,
                    "evidence_fingerprint": artifact["evidence_fingerprint"],
                    "decision": artifact["decision"]["decision"],
                    "should_publish": artifact["decision"]["should_publish"],
                    "average_net_return_pct": oos.get("average_net_return_pct", 0.0),
                    "profit_factor": oos.get("profit_loss_ratio"),
                    "executed_trades": coverage.get("executed_trades", 0),
                    "opportunity_capture_rate_pct": coverage.get("opportunity_capture_rate_pct", 0.0),
                }
            )
        findings.sort(
            key=lambda item: (
                bool(item["should_publish"]),
                float(item["average_net_return_pct"] or 0),
                float(item["opportunity_capture_rate_pct"] or 0),
            ),
            reverse=True,
        )
        print(
            json.dumps(
                {
                    "decision": "FINDING",
                    "audit_only": True,
                    "auto_publish": False,
                    "candidate_snapshot_fingerprint": snapshot.fingerprint,
                    "ranking": findings,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())

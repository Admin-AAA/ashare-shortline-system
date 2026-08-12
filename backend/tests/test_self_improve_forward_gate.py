"""测试 self-improve publish 的前瞻样本闸门（P4.1 软冻结）。

背景（2026-08-02 诊断）：self-improve 每小时级改策略、无前瞻验证。publish
闸门已有 canonical evidence 链，本测试锁定新增的 forward_samples_minimum
门槛：OOS 段执行笔数 < MIN_FORWARD_SAMPLES(20) 必须拒绝发布。
"""
import pytest

from app.services.backtest.policy_experiment import RELEASE_POLICY_FINGERPRINT
from scripts.self_improve_state import (
    MIN_FORWARD_SAMPLES,
    _validate_selection_policy_publish,
)

PROPOSED = {"policy_id": "production-selection", "version": 2, "fingerprint": "fp_challenger"}


def _state(evidence_id: str = "exp_1") -> dict:
    return {
        "canonical_production": {
            "selection_policy": {"fingerprint": "fp_champion"},
        },
        "selection_policy_governance": {
            "mode": "autonomous",
            "phase": "canary_passed",
            "audit_only": False,
            "strategy_inactive": False,
            "policy_artifact_fingerprint": "fp_champion",
            "policy_artifact_mismatch": False,
        },
        "release_state": {
            "status": "canary_passed",
            "evidence_id": evidence_id,
            "candidate_id": f"{evidence_id}:fp_challenger",
            "policy_fingerprint": "fp_challenger",
            "deployment_adapter": "local-policy-adapter:v1",
            "mode": "active",
            "deployment_fingerprint": "dep_1",
            "receipt": {"deployment_fingerprint": "dep_1"},
            "canary_evidence_id": "canary_1",
        },
    }


def _evidence(oos_executed_trades: int) -> dict:
    return {
        "schema_version": 2,
        "experiment_kind": "production_policy",
        "status": "completed",
        "decision": {"should_publish": True, "decision": "PRODUCTION_CANDIDATE", "gates": {
            "single_rule_change": True,
            "oos_executed_trade_required": True,
            "oos_productive_day_required": True,
            "productive_day_retention": True,
            "opportunity_capture_retention": True,
            "max_consecutive_opportunity_abstain": True,
            "changed_rule_no_dropped_positive_trade": True,
            "counterfactual_outcomes_complete": True,
            "vectorbt_truth": True,
        }},
        "champion_policy_fingerprint": "fp_champion",
        "challenger_policy_fingerprint": "fp_challenger",
        "changed_rule_id": "weak_regime_score_cap",
        "release_policy_fingerprint": RELEASE_POLICY_FINGERPRINT,
        "split_coverage_evidence": {
            "out_of_sample": {"executed_trades": oos_executed_trades},
        },
    }


def test_publish_rejected_when_forward_samples_below_minimum():
    err = _validate_selection_policy_publish(
        state=_state(),
        proposed_policy=PROPOSED,
        evidence_id="exp_1",
        evidence_loader=lambda eid: _evidence(MIN_FORWARD_SAMPLES - 1),
    )
    assert err is not None
    assert err["error"] == "CANONICAL_EVIDENCE_REJECTED"
    assert "forward_samples_minimum" in err["failed_checks"]


def test_publish_accepted_when_forward_samples_meet_minimum():
    err = _validate_selection_policy_publish(
        state=_state(),
        proposed_policy=PROPOSED,
        evidence_id="exp_1",
        evidence_loader=lambda eid: _evidence(MIN_FORWARD_SAMPLES),
    )
    assert err is None, err


def test_publish_rejected_without_evidence():
    err = _validate_selection_policy_publish(
        state=_state(),
        proposed_policy=PROPOSED,
        evidence_id=None,
        evidence_loader=None,
    )
    assert err is not None
    assert err["error"] == "CANONICAL_EVIDENCE_REQUIRED"


def test_publish_accepts_arms_challenger_layout():
    """policy_experiment 实际落库结构：OOS 证据在 arms.challenger.split_coverage_evidence。"""
    evidence = _evidence(MIN_FORWARD_SAMPLES)
    evidence["arms"] = {"challenger": {"split_coverage_evidence": evidence.pop("split_coverage_evidence")}}
    err = _validate_selection_policy_publish(
        state=_state(),
        proposed_policy=PROPOSED,
        evidence_id="exp_1",
        evidence_loader=lambda eid: evidence,
    )
    assert err is None, err

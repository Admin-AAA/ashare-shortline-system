from __future__ import annotations

from datetime import datetime
import random
import string

import pytest

from app.engine.production_policy import DEFAULT_PRODUCTION_SELECTION_POLICY
from scripts.capture_production_evidence_baseline import (
    _schema_shape,
    assert_safe_output,
    group_evidence_rows,
    project_backtest_trade_payload,
    project_champion_state,
)


def test_group_evidence_rows_preserves_all_three_grouping_totals() -> None:
    rows = [
        ("formal", "live_forward", datetime(2026, 8, 1, 9), "2026-08-01"),
        ("formal", "live_forward", datetime(2026, 8, 1, 10), "2026-08-01"),
        ("formal", "historical_replay", datetime(2026, 8, 2, 9), "2026-07-31"),
        ("backtest", None, datetime(2026, 8, 2, 10), None),
    ]

    grouped = group_evidence_rows(rows)

    assert grouped["total"] == 4
    assert grouped["invariants"] == {
        "scope_sum_matches_total": True,
        "started_date_sum_matches_total": True,
        "trade_date_sum_matches_total": True,
    }
    assert {
        (item["run_mode"], item["evaluation_scope"], item["count"])
        for item in grouped["by_run_mode_and_evaluation_scope"]
    } == {
        ("formal", "live_forward", 2),
        ("formal", "historical_replay", 1),
        ("backtest", "__null__", 1),
    }


def test_champion_projection_marks_identity_drift_without_copying_full_state() -> None:
    policy = DEFAULT_PRODUCTION_SELECTION_POLICY
    state = {
        "schema_version": 3,
        "last_checked_at": "2026-08-01T00:00:00Z",
        "canonical_production": {
            "commit": "deadbee",
            "branch": "main",
            "provisional": True,
            "metrics": {"n": 55, "win_rate_pct": 63.6, "private_note": "omit"},
            "rules": {
                "formal_board_universe": policy.board_universe,
                "excluded_20cm_buckets": list(policy.excluded_20cm_buckets),
                "structural_strong_hard_veto": not policy.structural_strong_hard_veto,
                "oscillation_hard_veto": policy.oscillation_hard_veto,
                "unrelated_rule": "omit",
            },
        },
        "published_findings": [{"large": "payload must not be copied"}],
    }

    projected = project_champion_state(state, git_revision="0123456789abcdef")

    assert projected["identity_status"] == "stale_identity"
    assert projected["identity_checks"]["mismatched_rule_fields"] == [
        "structural_strong_hard_veto"
    ]
    assert "private_note" not in projected["declared"]["metrics"]
    assert "unrelated_rule" not in projected["declared"]["rules"]
    assert "published_findings" not in projected


def test_backtest_trade_projection_keeps_only_parity_fields() -> None:
    projected = project_backtest_trade_payload(
        {
            "trade_id": "trade-1",
            "code": "000001",
            "entry_date": "2026-08-01",
            "exit_date": "2026-08-02",
            "net_return_pct": 1.25,
            "raw_candidate_payload": {"must": "not leak"},
            "request_json": {"must": "not leak"},
        }
    )

    assert projected == {
        "code": "000001",
        "entry_date": "2026-08-01",
        "exit_date": "2026-08-02",
        "net_return_pct": 1.25,
        "trade_id": "trade-1",
    }


def test_schema_shape_drops_descriptions_examples_and_extensions() -> None:
    shaped = _schema_shape(
        {
            "type": "object",
            "description": "arbitrary prose",
            "example": {"full": "payload"},
            "x-internal": "omit",
            "properties": {
                "run_id": {"type": "string", "title": "Run Id"},
                "count": {"type": "integer", "minimum": 0},
            },
            "required": ["run_id"],
        }
    )

    assert shaped == {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 0},
            "run_id": {"type": "string"},
        },
        "required": ["run_id"],
    }


def test_safe_output_rejects_sensitive_keys_values_and_configured_literals() -> None:
    with pytest.raises(ValueError, match="unsafe output key"):
        assert_safe_output({"database_url": "omitted"})
    with pytest.raises(ValueError, match="unsafe output value"):
        assert_safe_output({"message": "Bearer abcdefghijklmnop"})
    with pytest.raises(ValueError, match="configured sensitive literal"):
        assert_safe_output(
            {"innocent_field": "prefix-sensitive-value-suffix"},
            forbidden_literals=("sensitive-value",),
        )


def test_allowlist_property_drops_arbitrary_trade_payload_fields() -> None:
    """Generative property: unknown per-trade fields never survive projection."""
    rng = random.Random(20260803)
    alphabet = string.ascii_lowercase
    for _ in range(200):
        unknown_key = "unknown_" + "".join(rng.choice(alphabet) for _ in range(12))
        raw = {
            "trade_id": "trade-1",
            "entry_price": rng.random() * 100,
            unknown_key: "arbitrary-value",
        }
        projected = project_backtest_trade_payload(raw)
        assert unknown_key not in projected
        assert set(projected) <= {
            "trade_id",
            "entry_price",
        }
        assert_safe_output(projected)


def test_legacy_formal_execution_identity_is_deterministic_and_safe() -> None:
    from scripts.capture_production_evidence_baseline import (
        collect_legacy_formal_execution_identity,
    )

    first = collect_legacy_formal_execution_identity()
    second = collect_legacy_formal_execution_identity()

    assert first == second
    assert first["canonical_policy_object"] is False
    assert first["settlement"] == {
        "t_plus_one": True,
        "entry_bar_exit_allowed": False,
    }
    assert len(first["fingerprint"]) == 64
    assert set(first["fingerprint"]) <= set(string.hexdigits.lower())
    assert_safe_output(first)


def test_current_schema_champion_identity_does_not_require_legacy_rules() -> None:
    policy = DEFAULT_PRODUCTION_SELECTION_POLICY
    state = {
        "schema_version": 3,
        "canonical_production": {
            "commit": "0123456",
            "selection_policy": {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "fingerprint": policy.fingerprint,
            },
        },
        "selection_policy_governance": {
            "policy_artifact_fingerprint": policy.fingerprint,
        },
    }

    projected = project_champion_state(state, git_revision="0123456789abcdef")

    assert projected["identity_status"] == "declared_current"
    assert projected["declared"]["legacy_rules_present"] is False
    assert projected["declared"]["rules"] == {}
    assert projected["identity_checks"]["mismatched_rule_fields"] == []

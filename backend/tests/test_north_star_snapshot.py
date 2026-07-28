import pathlib
import sys


SCRIPTS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from north_star_snapshot import (  # noqa: E402
    choose_next_action,
    evaluate_g0,
    evaluate_g1,
    evaluate_g2,
    evaluate_g3,
    evaluate_g4,
)


def test_g0_blocks_invalid_evidence():
    result = evaluate_g0(
        core_rows=100,
        latest_core_date="2026-07-27",
        latest_formal_date="2026-07-28",
        orphan_outcomes=2,
        evidence_keys_unique=False,
    )
    assert result["status"] == "FAIL"
    assert "未去重" in result["summary"]
    assert "孤立" in result["summary"]


def test_g1_requires_sample_days_and_version_isolation():
    full = {"n": 100, "net_avg": 2.0, "net_pl": 2.0}
    small = evaluate_g1(
        full=full,
        forward={"n": 29, "net_avg": 2.0, "net_pl": 2.0},
        forward_trade_days=12,
        version_isolated=True,
    )
    assert small["status"] == "WARN"
    mixed = evaluate_g1(
        full=full,
        forward={"n": 40, "net_avg": 2.0, "net_pl": 2.0},
        forward_trade_days=10,
        version_isolated=False,
    )
    assert mixed["status"] == "WARN"
    assert "execution_version" in mixed["summary"]


def test_g1_blocks_sufficient_negative_oos():
    result = evaluate_g1(
        full={"n": 100, "net_avg": 2.0, "net_pl": 2.0},
        forward={"n": 40, "net_avg": -0.1, "net_pl": 0.9},
        forward_trade_days=12,
        version_isolated=True,
    )
    assert result["status"] == "FAIL"


def test_g2_and_next_action_prioritize_stability_after_g0():
    g0 = evaluate_g0(
        core_rows=5000,
        latest_core_date="2026-07-28",
        latest_formal_date="2026-07-28",
        orphan_outcomes=0,
        evidence_keys_unique=True,
    )
    g2 = evaluate_g2(
        missing_formal_dates=["2026-07-27"],
        failed_scheduler_tasks=["post_sync"],
        stale_running_runs=0,
    )
    g3 = evaluate_g3(formal_p95_seconds=1000, api_p95_seconds=None)
    snapshot = {
        "G0": g0,
        "G1": {"status": "WARN"},
        "G2": g2,
        "G3": g3,
        "G4": {"status": "BLOCKED"},
    }
    assert choose_next_action(snapshot) == "T12 pipeline稳定性"


def test_g3_does_not_pass_when_measurement_is_missing():
    result = evaluate_g3(formal_p95_seconds=1000, api_p95_seconds=None)
    assert result["status"] == "WARN"
    assert "不完整" in result["summary"]


def test_g4_is_blocked_by_engineering_gate_and_requires_oos():
    blocked = evaluate_g4(
        prerequisites=[{"status": "PASS"}, {"status": "FAIL"}, {"status": "WARN"}],
        candidate_oos={"n": 100, "trade_days": 20, "net_expectancy": 1, "profit_factor": 2},
    )
    assert blocked["status"] == "BLOCKED"
    waiting = evaluate_g4(
        prerequisites=[{"status": "PASS"}, {"status": "PASS"}, {"status": "WARN"}],
        candidate_oos={"n": 0, "trade_days": 0, "net_expectancy": None, "profit_factor": None},
    )
    assert waiting["status"] == "WARN"

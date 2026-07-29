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
    split_recovered_scheduler_failures,
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


def test_g2_replay_recovered_gap_passes_but_stays_visible():
    """安全网 historical_replay 补跑闭合 G2 断档（与安全网缺口判定同口径），
    但补跑日期必须在 summary/metrics 中保留可见，且不冒充 live_forward。"""
    result = evaluate_g2(
        missing_formal_dates=["2026-07-27"],
        failed_scheduler_tasks=[],
        stale_running_runs=0,
        replay_recovered_dates=["2026-07-27"],
    )
    assert result["status"] == "PASS"
    assert "2026-07-27" in result["summary"]
    assert "replay不计入OOS" in result["summary"]
    assert result["metrics"]["missing_formal_dates"] == []
    assert result["metrics"]["replay_recovered_dates"] == ["2026-07-27"]


def test_g2_unrecovered_gap_still_fails_alongside_replay_recovered():
    """只被部分补跑时，未修复的缺口日仍必须 FAIL。"""
    result = evaluate_g2(
        missing_formal_dates=["2026-07-24", "2026-07-27"],
        failed_scheduler_tasks=[],
        stale_running_runs=0,
        replay_recovered_dates=["2026-07-27"],
    )
    assert result["status"] == "FAIL"
    assert "2026-07-24" in result["summary"]
    assert result["metrics"]["missing_formal_dates"] == ["2026-07-24"]


def test_g2_accepts_postclose_failure_recovered_by_newer_safety_net():
    unresolved, recovered = split_recovered_scheduler_failures(
        failed_tasks=[("post_sync_formal_recommendation", "2026-07-28 11:02:22")],
        missing_formal_dates=[],
        stale_running_runs=0,
        safety_net_status="completed",
        safety_net_last_run_at="2026-07-29 00:30:00",
    )
    result = evaluate_g2(
        missing_formal_dates=[],
        failed_scheduler_tasks=unresolved,
        stale_running_runs=0,
        recovered_scheduler_tasks=recovered,
    )
    assert result["status"] == "PASS"
    assert result["metrics"]["recovered_scheduler_tasks"] == ["post_sync_formal_recommendation"]


def test_g2_does_not_hide_unrecovered_or_unrelated_scheduler_failure():
    unresolved, recovered = split_recovered_scheduler_failures(
        failed_tasks=[
            ("post_sync_formal_recommendation", "2026-07-29 01:00:00"),
            ("other_task", "2026-07-28 01:00:00"),
        ],
        missing_formal_dates=[],
        stale_running_runs=0,
        safety_net_status="completed",
        safety_net_last_run_at="2026-07-29 00:30:00",
    )
    assert unresolved == ["post_sync_formal_recommendation", "other_task"]
    assert recovered == []


def test_g3_does_not_pass_when_measurement_is_missing():
    result = evaluate_g3(formal_p95_seconds=1000, api_p95_seconds=None)
    assert result["status"] == "WARN"
    assert "不完整" in result["summary"]


def test_g3_downgrades_small_sample_p95_to_warn_not_fail():
    """小样本（<500）单点噪声不应把 G3 判 FAIL 连锁阻断 G4。

    实测：07-29 api_request_metrics n=186，第 177 名恰好 2078ms，差 78ms 过阈，
    nearest-rank p95 把 G3 误判 FAIL、NEXT_ACTION 误导到 T4。
    """
    result = evaluate_g3(
        formal_p95_seconds=1000,
        api_p95_seconds=2.202,
        api_p95_sample=186,
        api_p99_seconds=25.395,
    )
    assert result["status"] == "WARN"
    assert "样本不足" in result["summary"]


def test_g3_downgrades_single_point_noise_p95_to_warn():
    """样本充足但 p99/p95 比值过大（单点异常拉高 p95）也降级 WARN。"""
    result = evaluate_g3(
        formal_p95_seconds=1000,
        api_p95_seconds=2.1,
        api_p95_sample=800,
        api_p99_seconds=12.0,  # 比值 5.7x > 3x
    )
    assert result["status"] == "WARN"
    assert "单点噪声" in result["summary"]


def test_g3_fails_when_p95_genuinely_over_slo_with_healthy_sample():
    """样本充足且分布正常时，p95 真过阈仍应 FAIL（护栏不掩盖真退化）。"""
    result = evaluate_g3(
        formal_p95_seconds=1000,
        api_p95_seconds=2.5,
        api_p95_sample=800,
        api_p99_seconds=3.2,  # 比值 1.28x < 3x，分布正常
    )
    assert result["status"] == "FAIL"


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

from scripts.research_wr70_entry_guard import _metric_payload
from scripts.research_wr70_exit_screen import _variants


def test_entry_guard_empty_window_is_recorded_as_ineligible() -> None:
    metrics = _metric_payload(
        [
            {
                "signal_date": "2026-09-18",
                "settled": False,
                "executed": False,
            }
        ],
        panel_days=1,
    )

    assert metrics["n"] == 0
    assert metrics["coverage_pct"] == 0.0
    assert metrics["active_day_coverage_pct"] == 0.0


def test_exit_screen_variants_keep_production_policy_as_baseline() -> None:
    variants = _variants()
    baseline = variants["baseline"]
    take_profit = variants["take_profit_1"]

    assert baseline.version == "5.0.0"
    assert baseline.take_profit.enabled is False
    assert take_profit.take_profit.enabled is True
    assert take_profit.take_profit.default_gain_pct == 1.0
    assert take_profit.entry == baseline.entry
    assert take_profit.position == baseline.position

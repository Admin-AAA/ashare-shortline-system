import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from scripts.research_dragon_seat_repeat import market_days, normalize_seats, recent_limit_features, seat_signals
from scripts.research_limit_up_benchmark import load_frame, signal_features

ROOT = Path(__file__).resolve().parents[2]


def test_real_seat_amounts_reconcile_and_unknown_sides_remain_unknown():
    payload = json.loads((Path(__file__).parent / "fixtures/dragon_seat_real_history.json").read_text())
    seats, quality = normalize_seats(pd.DataFrame(payload["rows"]))
    assert len(seats) == 3
    shanghai = seats[seats.code.eq("600076")].iloc[0]
    assert shanghai.net_buy == pytest.approx(6724590.38 - 3278764)
    assert seats[seats.code.eq("002584")].iloc[0].net_buy == pytest.approx(-2230821.03)
    assert quality["duplicate_ranking_or_reason_rows"] == 1
    assert quality["unknown_side_seat_events"] == 1
    assert not seats.exalter.str.contains("机构专用|黄陂南路").any()
    assert "002453" not in set(seats.code)


@pytest.mark.integration
def test_real_signals_are_point_in_time_and_artifact_metrics_reconcile():
    artifact = json.loads((ROOT / "output/dragon-seat-repeat-metrics-2026-09-22.json").read_text())
    assert artifact["caliber"]["output_count"] == 6
    assert artifact["windows"]["W1"]["trade_days"] == 244
    assert artifact["windows"]["W3"]["trade_days"] == 40
    for window in artifact["windows"].values():
        for key, arm in window["strategies"].items():
            rows, metrics = arm["records"], arm["metrics"]
            assert max(Counter(row["signal_date"] for row in rows).values()) <= 6
            returns = [row["net_return_pct"] for row in rows if row["settled"]]
            assert len(returns) == metrics["n"]
            assert sum(returns) == pytest.approx(metrics["cumulative_net_return_pct"])
            assert 100 * sum(value > 0 for value in returns) / len(returns) == pytest.approx(metrics["win_rate_pct"])
            for row in rows:
                day = row["signal_date"]
                month_start = (pd.Timestamp(day) - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
                assert month_start <= row["last_sealed_date"] < day
                for event in row["seat_evidence"]:
                    assert event["event_date"] < day
                    assert all(month_start <= value < day for value in event["positive_dates"])
                if key == "seat_repeat":
                    assert any(len(set(event["positive_dates"])) >= 2 for event in row["seat_evidence"])
                if row["settled"]:
                    assert day < row["entry_date"] < row["exit_date"]
    frame, _ = load_frame("2025-01-01", "2025-03-31", ["000737", "600789", "002300"])
    calendar = sorted(frame.trade_date.unique())
    full = recent_limit_features(signal_features(frame, calendar))
    cutoff = "2025-02-14"
    bounded = recent_limit_features(signal_features(frame[frame.trade_date <= cutoff], [day for day in calendar if day <= cutoff]))
    fields = ["code", "trade_date", "last_sealed_date", "recent_limit"]
    pd.testing.assert_frame_equal(full.loc[full.trade_date <= cutoff, fields], bounded[fields])
    assert full.recent_limit.any()
    days = market_days("2024-12-01", "2026-09-21")
    raw = pd.DataFrame(json.loads((Path(__file__).parent / "fixtures/dragon_seat_real_history.json").read_text())["rows"])
    seats, _ = normalize_seats(raw)
    full_signals = seat_signals(seats, days)
    bounded_signals = seat_signals(seats[seats.event_date < "2026-09-18"], days)
    assert len(full_signals) > len(bounded_signals) > 0
    pd.testing.assert_frame_equal(full_signals[full_signals.trade_date <= "2026-09-18"].reset_index(drop=True), bounded_signals.reset_index(drop=True))


@pytest.mark.parametrize("name,candidate", [("institution-agreement", "institution_agreement"), ("seat-reversal", "seat_reversal")])
def test_real_additional_directions_keep_event_evidence_and_reconcile_metrics(name, candidate):
    artifact = json.loads((ROOT / f"output/{name}-metrics-2026-09-22.json").read_text())
    for window in artifact["windows"].values():
        arm = window["strategies"][candidate]
        rows = arm["records"]
        settled = [row for row in rows if row["settled"]]
        assert len(settled) == arm["metrics"]["n"] >= 20
        returns = [row["net_return_pct"] for row in settled]
        assert sum(returns) == pytest.approx(arm["metrics"]["cumulative_net_return_pct"])
        assert 100 * sum(value > 0 for value in returns) / len(returns) == pytest.approx(arm["metrics"]["win_rate_pct"])
        assert max(Counter(row["signal_date"] for row in rows).values()) <= 6
        for row in rows:
            month_start = (pd.Timestamp(row["signal_date"]) - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
            if candidate == "institution_agreement":
                assert row["institution_evidence"]
                for event in row["institution_evidence"]:
                    assert event["buy"] is not None and event["sell"] is not None
                    assert event["buy"] > event["sell"]
                    assert pd.Timestamp(event["trade_date"]) < pd.Timestamp(row["signal_date"])
            else:
                evidence = [event for event in row["seat_evidence"] if event["reversal"]]
                assert evidence
                for event in evidence:
                    prior = event["previous_event"]
                    assert prior["net_buy"] < 0
                    assert prior["exalter"] == event["exalter"]
                    assert month_start <= prior["event_date"] < event["event_date"] < row["signal_date"]

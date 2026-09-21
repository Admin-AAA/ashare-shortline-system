import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts.research_limit_up_benchmark import CONFIG_PATH, attach_labels, load_frame, signal_features


@pytest.mark.integration
def test_real_history_patterns_are_causal_and_labels_use_next_day():
    frame, _ = load_frame("2025-01-01", "2025-03-31", ["000737", "600789", "002300"])
    calendar = sorted(frame.trade_date.unique())
    full = signal_features(frame, calendar)
    cutoff = "2025-02-14"
    bounded = signal_features(frame[frame.trade_date <= cutoff], [day for day in calendar if day <= cutoff])
    fields = ["code", "trade_date", "first_board", "failed_touch", "first_pullback"]
    pd.testing.assert_frame_equal(full.loc[full.trade_date <= cutoff, fields], bounded[fields])
    assert full.first_board.any()
    labelled = attach_labels(full)
    assert labelled.loc[labelled.trade_date == calendar[-1], "next_sealed"].isna().all()
    raw = {(row.code, row.trade_date): row for row in frame.itertuples()}
    checked = 0
    for row in labelled.itertuples():
        if pd.isna(row.next_sealed):
            continue
        following = raw[(row.code, calendar[calendar.index(row.trade_date) + 1])]
        assert bool(row.next_sealed) == (abs(following.close - following.up_limit) < 0.005)
        assert bool(row.next_touch) == (abs(following.high - following.up_limit) < 0.005)
        checked += 1
    assert checked > 100


def test_generated_real_records_reconcile_metrics_and_trade_timing():
    path = Path(__file__).resolve().parents[2] / "output" / "limit-up-benchmark-metrics-2026-09-21.json"
    artifact = json.loads(path.read_text())
    config = yaml.safe_load(CONFIG_PATH.read_text())
    assert artifact["caliber"]["output_count"] == config["output_count"] == 6
    assert artifact["caliber"]["entry_mode"] == config["default_entry_mode"] == "next_open"
    assert artifact["windows"]["W3"]["trade_days"] == 40
    assert artifact["windows"]["W3_given_dates"]["trade_days"] == 35
    for window in artifact["windows"].values():
        for strategy in window["strategies"].values():
            records, metrics = strategy["records"], strategy["metrics"]
            assert max(Counter(row["signal_date"] for row in records).values()) <= 6
            settled = [row for row in records if row["settled"]]
            returns = [row["net_return_pct"] for row in settled]
            assert metrics["n"] == len(settled)
            assert metrics["cumulative_net_return_pct"] == pytest.approx(sum(returns))
            assert metrics["win_rate_pct"] == pytest.approx(100 * sum(value > 0 for value in returns) / len(returns))
            assert metrics["profit_factor"] == pytest.approx(sum(value for value in returns if value > 0) / abs(sum(value for value in returns if value < 0)))
            for row in settled:
                assert row["signal_date"] < row["entry_date"] < row["exit_date"]
            for field in ("next_sealed", "next_touch"):
                known = [row[field] for row in records if row[field] is not None]
                assert metrics["accuracy"][field]["rate_pct"] == pytest.approx(np.mean(known) * 100)

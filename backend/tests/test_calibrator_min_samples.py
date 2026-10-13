"""校准器最低样本门槛：样本不足时只报告，不产出新权重（Stage 5，2026-10-13）。"""
from __future__ import annotations

import app.services.calibrator as calibrator
from app.engine.aggregator import COMPONENT_KEYS, DEFAULT_AGG_WEIGHTS


def _samples(n: int) -> list[dict]:
    return [
        {
            "trade_date": f"2026-01-{(i % 28) + 1:02d}",
            "label": i % 2,
            "close_return": 1.0 if i % 2 else -1.0,
            "component_scores": {k: float((i * 7 + j * 3) % 100) for j, k in enumerate(COMPONENT_KEYS)},
        }
        for i in range(n)
    ]


def test_aggregation_calibration_refuses_below_minimum(monkeypatch):
    n = calibrator.MIN_CALIBRATION_SAMPLES - 1
    monkeypatch.setattr(calibrator, "_load_labeled_samples", lambda db, run_mode=None: _samples(n))
    result = calibrator.calibrate_aggregation_weights(db=None)
    assert result["optimal_weights"] == dict(DEFAULT_AGG_WEIGHTS)
    assert result["total_sample_count"] == n
    assert str(calibrator.MIN_CALIBRATION_SAMPLES) in result["error"]


def test_walk_forward_calibration_refuses_below_minimum(monkeypatch):
    n = calibrator.MIN_CALIBRATION_SAMPLES - 1
    monkeypatch.setattr(calibrator, "_load_labeled_samples", lambda db, run_mode=None: _samples(n))
    result = calibrator.walk_forward_calibrate(db=None, n_windows=3)
    assert result["windows"] == []
    assert str(calibrator.MIN_CALIBRATION_SAMPLES) in result["error"]


def test_minimum_is_not_trivially_small():
    # 5000 组随机权重在几十笔样本上必然挑出噪声，门槛必须是统计意义上的量级。
    assert calibrator.MIN_CALIBRATION_SAMPLES >= 200

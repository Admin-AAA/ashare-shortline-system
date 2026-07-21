"""D 项低 score_line formal 污染护栏单测（纯函数，不碰 DB）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_lsl", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def test_no_pollution_when_all_above_floor():
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 72.0),
        ("000002", "2026-07-08", "failed", -5.0, 72.0),
        ("000003", "2026-07-09", "success", 4.0, 80.0),
    ]
    assert sc._low_score_line_pollution(rows) is None


def test_no_pollution_when_low_sl_also_covered_by_clean_run():
    """同日同股既有 sl=55 又有 sl=72 → 不算 pure pollution（高线已覆盖）。"""
    rows = [
        ("000001", "2026-07-16", "failed", -5.0, 55.0),
        ("000001", "2026-07-16", "success", 3.0, 72.0),  # 同股同日高线
    ]
    assert sc._low_score_line_pollution(rows) is None


def test_detects_pure_low_score_line_pollution():
    """仅低线 run 产出的独立票应被标记污染，并给出剔除后反事实 WR。"""
    rows = [
        # clean
        ("000001", "2026-07-08", "success", 5.0, 72.0),
        ("000002", "2026-07-08", "success", 4.0, 72.0),
        ("000003", "2026-07-09", "failed", -5.0, 72.0),
        # pure pollution (only appear under sl=55)
        ("600001", "2026-07-16", "failed", -5.0, 55.0),
        ("600002", "2026-07-16", "failed", -5.0, 55.0),
        ("600003", "2026-07-16", "success", 3.0, 55.0),
    ]
    result = sc._low_score_line_pollution(rows)
    assert result is not None
    assert result["polluted_n"] == 3
    assert result["polluted_wr"] == pytest.approx(33.3, abs=0.1)
    assert result["clean_n"] == 3
    assert result["clean_wr"] == pytest.approx(66.7, abs=0.1)
    assert result["full_n"] == 6
    assert result["full_wr"] == 50.0
    assert result["delta_pp"] == pytest.approx(16.7, abs=0.1)
    assert 55.0 in result["sl_buckets"]


def test_dedup_keeps_worst_return_within_polluted():
    """同股同日多笔低线 run，取最差 ret。"""
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 72.0),
        ("600001", "2026-07-16", "success", 3.0, 55.0),
        ("600001", "2026-07-16", "failed", -5.5, 55.0),  # worse
    ]
    result = sc._low_score_line_pollution(rows)
    assert result is not None
    assert result["polluted_n"] == 1
    assert result["polluted_wr"] == 0.0  # worst label is failed


def test_empty_input_returns_none():
    assert sc._low_score_line_pollution([]) is None


def test_custom_floor():
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 70.0),
        ("600001", "2026-07-16", "failed", -5.0, 60.0),
    ]
    # floor=65 → 70 干净、60 污染
    result = sc._low_score_line_pollution(rows, floor=65.0)
    assert result is not None
    assert result["polluted_n"] == 1
    assert result["clean_n"] == 1

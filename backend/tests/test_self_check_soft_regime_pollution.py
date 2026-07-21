"""D 项 soft@banned-regime 历史污染护栏单测（纯函数，不碰 DB）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_soft_pol", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def test_no_pollution_when_no_soft():
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 0, "弱势"),
        ("000002", "2026-07-08", "failed", -5.0, False, "震荡"),
    ]
    assert sc._soft_regime_pollution(rows) is None


def test_no_pollution_when_soft_has_hard_twin():
    """同日同股既有 soft@震荡 又有 hard-pass → 不算 pure pollution。"""
    rows = [
        ("000001", "2026-07-16", "failed", -5.0, 1, "震荡", "活跃"),
        ("000001", "2026-07-16", "success", 3.0, 0, "震荡", "活跃"),
    ]
    assert sc._soft_regime_pollution(rows) is None


def test_detects_pure_soft_oscillation_pollution():
    rows = [
        # clean hard
        ("000001", "2026-07-08", "success", 5.0, 0, "弱势", "活跃"),
        ("000002", "2026-07-08", "success", 4.0, 0, "弱势", "活跃"),
        ("000003", "2026-07-09", "failed", -5.0, 0, "弱势", "活跃"),
        # pure soft@震荡
        ("600001", "2026-07-09", "failed", -5.0, 1, "震荡", "高潮"),
        ("600002", "2026-07-09", "failed", -5.0, True, "震荡", "高潮"),
        ("600003", "2026-07-16", "success", 3.0, "true", "震荡", "活跃"),
        ("600004", "2026-07-16", "failed", -5.0, 1, "震荡", "活跃"),
    ]
    result = sc._soft_regime_pollution(rows)
    assert result is not None
    assert result["polluted_n"] == 4
    assert result["polluted_wr"] == 25.0
    assert result["clean_n"] == 3
    assert result["clean_wr"] == pytest.approx(66.7, abs=0.1)
    assert result["full_n"] == 7
    assert result["independent_days"] == 2
    assert result["day_buckets"]["2026-07-09"] == 2
    assert result["day_buckets"]["2026-07-16"] == 2
    assert result["sent_buckets"]["高潮"] == 2
    assert result["sent_buckets"]["活跃"] == 2
    assert result["banned_bases"] == ["震荡"]


def test_weak_soft_not_counted_as_pollution():
    """弱势 soft 不在 ban 范围，不报污染。"""
    rows = [
        ("000001", "2026-06-03", "success", 7.0, 1, "弱势", "高潮"),
        ("000002", "2026-06-03", "success", 6.0, 1, "弱势", "高潮"),
        ("000003", "2026-07-08", "success", 4.0, 0, "弱势", "活跃"),
    ]
    assert sc._soft_regime_pollution(rows) is None


def test_empty_input_returns_none():
    assert sc._soft_regime_pollution([]) is None


def test_custom_banned_bases():
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 0, "弱势"),
        ("600001", "2026-07-10", "failed", -5.0, 1, "强牛", "高潮"),
    ]
    # 默认只 ban 震荡 → 无污染
    assert sc._soft_regime_pollution(rows) is None
    # 自定义 ban 强牛
    result = sc._soft_regime_pollution(rows, banned_bases={"强牛"})
    assert result is not None
    assert result["polluted_n"] == 1
    assert result["polluted_wr"] == 0.0

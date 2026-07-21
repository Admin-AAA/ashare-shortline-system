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


# ---------------------------------------------------------------------------
# _extract_clean_dedup_rows — clean≥floor 独立票提取（D 项 clean 口径净成本）
# ---------------------------------------------------------------------------


def test_extract_clean_drops_below_floor_rows():
    """sl<floor 的 run 不进 clean 总体。"""
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 72.0),
        ("600001", "2026-07-16", "failed", -5.0, 55.0),  # 低线，被丢
    ]
    out = sc._extract_clean_dedup_rows(rows)
    assert len(out) == 1
    assert out[0][0] == "000001"


def test_extract_clean_dedupes_same_stock_day_keeps_worst_ret():
    """同股同日多条 clean run，取最差 ret（与 _fetch_dedup_live_formal 一致）。"""
    rows = [
        ("000001", "2026-07-16", "success", 3.0, 80.0),
        ("000001", "2026-07-16", "failed", -5.0, 72.0),  # 更差 → 留这条
    ]
    out = sc._extract_clean_dedup_rows(rows)
    assert len(out) == 1
    assert out[0][3] == -5.0
    assert out[0][2] == "failed"


def test_extract_clean_low_line_only_when_no_clean_cover():
    """同股同日既有 sl=55 又有 sl=72 → clean 总体只含 sl≥72 那笔。"""
    rows = [
        ("000001", "2026-07-16", "failed", -5.0, 55.0),
        ("000001", "2026-07-16", "success", 3.0, 72.0),
    ]
    out = sc._extract_clean_dedup_rows(rows)
    assert len(out) == 1
    assert out[0][3] == 3.0


def test_extract_clean_empty_when_all_below_floor():
    rows = [
        ("600001", "2026-07-16", "failed", -5.0, 55.0),
    ]
    assert sc._extract_clean_dedup_rows(rows) == []


def test_extract_clean_custom_floor():
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 70.0),
        ("000002", "2026-07-16", "failed", -5.0, 60.0),
    ]
    out = sc._extract_clean_dedup_rows(rows, floor=65.0)
    assert len(out) == 1
    assert out[0][0] == "000001"


def test_extract_clean_excludes_soft_floor_by_default():
    """exclude_soft=True（默认）排除 dynamic_soft_floor_applied 票。

    soft 票即便 sl≥floor（靠软降线放行）也不属于「硬过线」clean 口径。
    """
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 74.0, 0),  # 硬过线
        ("600001", "2026-07-16", "failed", -5.0, 74.0, 1),  # soft@震荡放行
    ]
    out = sc._extract_clean_dedup_rows(rows)
    assert len(out) == 1
    assert out[0][0] == "000001"


def test_extract_clean_keeps_soft_floor_when_exclude_off():
    """exclude_soft=False 保留 soft 票（与旧口径一致，向后兼容）。"""
    rows = [
        ("000001", "2026-07-08", "success", 5.0, 74.0, 0),
        ("600001", "2026-07-16", "failed", -5.0, 74.0, 1),
    ]
    out = sc._extract_clean_dedup_rows(rows, exclude_soft=False)
    assert len(out) == 2


def test_extract_clean_soft_flag_truthy_variants():
    """soft 标志取值 1/True/'true' 都应触发排除。"""
    for soft_val in (1, True, "true"):
        rows = [("600001", "2026-07-16", "failed", -5.0, 74.0, soft_val)]
        assert sc._extract_clean_dedup_rows(rows) == [], f"soft={soft_val!r} 应被排除"


def test_extract_clean_five_tuple_ignores_soft_flag():
    """5 列历史/测试输入不触发软排除（len 守卫）。"""
    rows = [("600001", "2026-07-16", "failed", -5.0, 74.0)]
    out = sc._extract_clean_dedup_rows(rows)
    assert len(out) == 1

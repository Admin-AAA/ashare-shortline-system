"""E 项低胜率 vs 低覆盖标签：n≥20 才可判低胜率（T24）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_e_tag", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def test_n1_zero_wr_is_low_coverage_not_low_winrate():
    """small-cap n=1 WR=0% 先例：只能标低覆盖，禁止低胜率。"""
    assert sc._format_e_coverage_tag(1, 0.0) == "低覆盖"
    assert sc._format_e_coverage_tag(19, 0.0) == "低覆盖"


def test_n20_low_wr_is_low_winrate():
    assert sc._format_e_coverage_tag(20, 44.9) == "低胜率"
    assert sc._format_e_coverage_tag(40, 30.0) == "低胜率"


def test_n_between_floor_and_50_healthy_wr_is_low_coverage():
    """n∈[20,50) 且 WR≥45 → 仍列在表里（HAVING n<50）但标签是低覆盖。"""
    assert sc._format_e_coverage_tag(25, 50.0) == "低覆盖"
    assert sc._format_e_coverage_tag(45, 60.0) == "低覆盖"


def test_boundary_exactly_45_not_low_winrate():
    assert sc._format_e_coverage_tag(30, 45.0) == "低覆盖"
    assert sc._format_e_coverage_tag(30, 44.9) == "低胜率"

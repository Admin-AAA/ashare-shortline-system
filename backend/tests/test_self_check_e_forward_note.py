"""E 项前瞻胜率附注：样本过少勿判真低胜率（T18）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "self_check.py"
_spec = importlib.util.spec_from_file_location("self_check_e_fwd", _SCRIPTS)
sc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sc)


def test_forward_zero_settled():
    assert sc._format_e_forward_note(0, 0) == " | 前瞻0笔(等结算)"


def test_forward_small_n_not_true_low_winrate():
    """high-range 3 笔@07-16 污染日先例：勿标真低胜率。"""
    note = sc._format_e_forward_note(3, 1, full_n=11, min_oos=10)
    assert "样本过少" in note
    assert "勿判" in note
    assert "(真低胜率)" not in note  # 标签本身不出现，仅「勿判真低胜率」说明
    assert "WR=33.3%" in note


def test_forward_enough_n_low_is_true_low():
    note = sc._format_e_forward_note(12, 3, full_n=40, min_oos=10)
    assert "真低胜率" in note
    assert "旧污染" not in note
    assert "仍" in note  # full_n>=20


def test_forward_enough_n_high_is_old_pollution():
    note = sc._format_e_forward_note(12, 8, full_n=15, min_oos=10)
    assert "旧污染" in note
    assert "仍" not in note  # full_n<20


def test_forward_boundary_min_oos():
    # n == min_oos - 1 → 样本过少
    note = sc._format_e_forward_note(9, 0, min_oos=10)
    assert "样本过少" in note
    # n == min_oos → 可判
    note2 = sc._format_e_forward_note(10, 2, min_oos=10)
    assert "真低胜率" in note2

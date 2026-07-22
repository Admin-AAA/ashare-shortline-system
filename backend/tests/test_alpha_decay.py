"""diagnose_alpha_decay.py 纯函数单测：point-biserial 数学 + 分母伪信号护栏。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.diagnose_alpha_decay import (  # noqa: E402
    evaluate_composite_edge,
    evaluate_factor_decay,
    point_biserial,
    split_by_median_date,
)


def _row(trade_date, win, features):
    return {"trade_date": trade_date, "final_score": 70.0,
            "consensus_score": 70.0, "features": features, "win": win}


# --------------------------------------------------------------------------- #
# point_biserial 数学
# --------------------------------------------------------------------------- #
def test_point_biserial_perfect_positive():
    xs = [10, 10, 10, 0, 0, 0]
    ys = [1, 1, 1, 0, 0, 0]
    assert abs(point_biserial(xs, ys) - 1.0) < 1e-9


def test_point_biserial_perfect_negative():
    xs = [0, 0, 0, 10, 10, 10]
    ys = [1, 1, 1, 0, 0, 0]
    assert abs(point_biserial(xs, ys) + 1.0) < 1e-9


def test_point_biserial_degenerate_returns_none():
    # 单一类别或零方差
    assert point_biserial([1, 2, 3], [1, 1, 1]) is None
    assert point_biserial([5, 5, 5], [1, 0, 1]) is None
    assert point_biserial([1, 2], [1, 0]) is None  # n<4


def test_point_biserial_bounded():
    r = point_biserial([1, 9, 2, 8, 3, 7], [1, 0, 1, 0, 1, 0])
    assert -1.0 <= r <= 1.0


# --------------------------------------------------------------------------- #
# split_by_median_date
# --------------------------------------------------------------------------- #
def test_split_balanced():
    rows = [_row(f"2026-01-{i:02d}", i % 2 == 0, {}) for i in range(1, 11)]
    prior, recent = split_by_median_date(rows)
    assert len(prior) == 5 and len(recent) == 5
    assert prior[-1]["trade_date"] <= recent[0]["trade_date"]


def test_split_empty():
    assert split_by_median_date([]) == ([], [])


# --------------------------------------------------------------------------- #
# 分母伪信号护栏（核心不变量）
# --------------------------------------------------------------------------- #
def _make_windows():
    """前期：'chg20' 完美分离(r≈1)；'pct' 近零基线。近期：'chg20' 翻转(r≈-1) 触发真实衰减；
    'pct' 翻转但基线极弱 → 不可标。用真实因子名（evaluate_factor_decay 仅遍历 NUMERIC_FEATURES）。"""
    prior, recent = [], []
    for i in range(12):
        prior.append(_row(f"2026-01-{i+1:02d}", i % 2 == 0,
                          {"chg20": 10.0 if i % 2 == 0 else 0.0,
                           "pct": 1.0 + 0.05 * i}))
        recent.append(_row(f"2026-02-{i+1:02d}", i % 2 == 0,
                           {"chg20": 0.0 if i % 2 == 0 else 10.0,
                            "pct": 1.05 + 0.05 * i}))
    return prior, recent


def test_material_decay_flagged():
    prior, recent = _make_windows()
    fac = evaluate_factor_decay(prior, recent)
    x = next(f for f in fac if f["feature"] == "chg20")
    assert x["flag"] == "MATERIAL_DECAY"
    assert abs(x["decay_pct"]) > 30


def test_weak_baseline_not_flagged():
    prior, recent = _make_windows()
    fac = evaluate_factor_decay(prior, recent)
    weak = next(f for f in fac if f["feature"] == "pct")
    # 即使 decay% 很大（翻转），因 |r_前期|<MIN_ABS_R 必不标 MATERIAL_DECAY
    assert weak["flag"] != "MATERIAL_DECAY"
    assert weak["baseline_weak"] is True


def test_no_low_n_false_positive():
    # 窗口样本不足 MIN_N 的因子不应标 material
    prior = [_row(f"2026-01-{i+1:02d}", i % 2 == 0, {"chg20": 10.0 if i % 2 == 0 else 0.0})
             for i in range(6)]
    recent = [_row(f"2026-02-{i+1:02d}", i % 2 == 0, {"chg20": 0.0 if i % 2 == 0 else 10.0})
              for i in range(6)]
    fac = evaluate_factor_decay(prior, recent)
    x = next(f for f in fac if f["feature"] == "chg20")
    assert x["flag"] == "skip_low_n"


# --------------------------------------------------------------------------- #
# evaluate_composite_edge
# --------------------------------------------------------------------------- #
def test_composite_edge_shape():
    prior, recent = _make_windows()
    comp = evaluate_composite_edge(prior, recent)
    assert set(comp.keys()) == {"final_score", "consensus_score"}
    for v in comp.values():
        assert "r_prior" in v and "r_recent" in v
        assert v["n_prior"] == 12 and v["n_recent"] == 12

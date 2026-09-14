"""Regression guard: self_check E 项 0 曝光 / 覆盖盲区细分类（2026-07-21 T3.6）。

背景: weak-market-strong-stock-reversal 近 60 天 formal 0 命中、近 30 天 0 曝光，
但 07-21 曝光补跑已 completed（skill_gate 24 票全「不在策略股票池」→ rec_n=0）。
原话术「补跑未覆盖」/ 裸「覆盖盲区」会误导下一轮去放宽 universe 或手动重触发补跑。

两枚纯函数锁住分类边界：
- _classify_zero_exposure: 补跑已覆盖·0 产出 vs 真未入队
- _classify_coverage_blind: preset 静默死亡 vs 已注册但条件稀疏
"""
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = BACKEND_ROOT.parent / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import self_check  # type: ignore[import-not-found]


def test_zero_exp_covered_when_experiment_completed_for_skill():
    """有 completed experiment（selected_skills 含该 skill）= 补跑已覆盖，非未覆盖。"""
    out = self_check._classify_zero_exposure(
        ["weak-market-strong-stock-reversal", "ghost-skill"],
        {"weak-market-strong-stock-reversal", "chip-single-peak-breakout"},
    )
    assert out["covered_zero"] == ["weak-market-strong-stock-reversal"]
    assert out["uncovered"] == ["ghost-skill"]


def test_zero_exp_all_uncovered_when_no_experiment_runs():
    """近 7 天无任何 completed experiment → 全部真未入队。"""
    out = self_check._classify_zero_exposure(
        ["weak-market-strong-stock-reversal", "small-cap-thematic-momentum"],
        set(),
    )
    assert out["covered_zero"] == []
    assert out["uncovered"] == [
        "small-cap-thematic-momentum",
        "weak-market-strong-stock-reversal",
    ]


def test_zero_exp_empty_input():
    out = self_check._classify_zero_exposure([], {"any-skill"})
    assert out == {"covered_zero": [], "uncovered": []}


def test_blind_silent_death_when_not_in_formal_preset():
    """enabled 但不在 preset_formal_opening = 静默死亡（真故障）。"""
    out = self_check._classify_coverage_blind(
        ["new-orphan-skill", "weak-market-strong-stock-reversal"],
        {"weak-market-strong-stock-reversal", "daily-stock-analysis-skill"},
    )
    assert out["silent_death"] == ["new-orphan-skill"]
    assert out["expected_sparse"] == ["weak-market-strong-stock-reversal"]


def test_blind_all_expected_sparse_when_all_in_preset():
    """全在 formal preset → 全部预期稀疏（勿放宽 universe）。"""
    out = self_check._classify_coverage_blind(
        ["weak-market-strong-stock-reversal"],
        {"weak-market-strong-stock-reversal", "other"},
    )
    assert out["silent_death"] == []
    assert out["expected_sparse"] == ["weak-market-strong-stock-reversal"]


def test_blind_empty_preset_skips_classification():
    """formal_preset_skills 空（查表失败）→ 两端都空，调用方跳过细分类。"""
    out = self_check._classify_coverage_blind(
        ["weak-market-strong-stock-reversal"],
        set(),
    )
    assert out == {"silent_death": [], "expected_sparse": []}

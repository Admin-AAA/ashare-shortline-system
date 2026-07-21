"""sector-relative-strength-leader 赢家触发放宽回归。

背景（2026-07-21 T1）：LIVE formal primary n=14 WR=92.9%，但多笔赢家 final_score
63–70 难过 FORMAL 地板 72——主因 pe20 强而 ladder 低（0–50），缺梯队加分。
另有赢家 oh∈[-12,-8)（如 688047 -11.7）原 phase「相对强度观察」要求 oh>=-8 匹配不到。

改动：
1. phase 相对强度观察：off_high >= -12（与 setup 主线相对强度观察对齐）
2. scoring pe20>=15: 10→12；pe20>=8: 6→8
弱市 score_cap=68 / 非主线 hard_veto 不动。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.engine.evaluator import evaluate
from app.engine.skill_loader import load_skills

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_ID = "sector-relative-strength-leader"


def _yaml() -> dict:
    return yaml.safe_load((SKILLS_DIR / SKILL_ID / "strategy.yaml").read_text(encoding="utf-8"))


def test_sector_rs_relative_strength_phase_oh_relaxed_to_minus_12():
    phases = {p["name"]: p for p in _yaml().get("phases") or []}
    p = phases["相对强度观察"]
    assert "off_high_pct >= -12" in p["when"]
    assert "off_high_pct >= -8" not in p["when"]
    assert "primary_excess20 >= 8" in p["when"]
    assert p.get("score_cap") == 86


def test_sector_rs_relative_strength_phase_matches_deep_pullback_winner():
    """688047 型：主线 + pe20 高 + oh≈-11.7 应落入相对强度观察 phase。"""
    when = (
        "is_main_theme == true and primary_excess20 >= 8 and off_high_pct >= -12"
    )
    assert evaluate(when, {
        "is_main_theme": True,
        "primary_excess20": 12.3,
        "off_high_pct": -11.7,
    }) is True
    assert evaluate(when, {
        "is_main_theme": True,
        "primary_excess20": 12.3,
        "off_high_pct": -13.0,  # 仍过深
    }) is False


def test_sector_rs_pe20_scoring_bumped():
    rules = {r["reason"]: r for r in (_yaml().get("scoring") or {}).get("rules") or []}
    assert rules["个股20日超额收益强"]["delta"] == 12
    assert rules["个股20日超额收益占优"]["delta"] == 8
    # 表达式门槛未变
    assert "primary_excess20 >= 15" in rules["个股20日超额收益强"]["when"]
    assert "primary_excess20 >= 8" in rules["个股20日超额收益占优"]["when"]


def test_sector_rs_weak_market_cap_unchanged():
    """弱市降权 score_cap=68 不得被本轮动（PL 0.82 净负护栏）。"""
    phases = {p["name"]: p for p in _yaml().get("phases") or []}
    assert phases["弱市降权"]["score_cap"] == 68
    assert "market_regime == '弱势'" in phases["弱市降权"]["when"]


def test_sector_rs_skill_loads():
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    assert SKILL_ID in skills
    assert skills[SKILL_ID].enabled is True

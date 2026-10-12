"""daily-stock-analysis 中度回撤毒区（oh∈[-10,-5)）回归。

T3 2026-07-21：DSA formal primary n=107 中 oh∈[-10,-5) WR=38.5% avg=-1.98，
剔除后 WR 57.9%→64.2%（Δ+6.3pp），跨 7 日。phase hard_veto 锁定。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.engine.evaluator import evaluate
from app.engine.skill_loader import load_skills

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_ID = "daily-stock-analysis-skill"


def _yaml() -> dict:
    return yaml.safe_load((SKILLS_DIR / SKILL_ID / "strategy.yaml").read_text(encoding="utf-8"))


def test_dsa_mid_pullback_phase_present():
    phases = {p["name"]: p for p in _yaml().get("phases") or []}
    assert "中度回撤毒区" in phases
    p = phases["中度回撤毒区"]
    assert p.get("hard_veto") is True
    assert p.get("score_cap") == 50
    assert "off_high_pct >= -10" in p["when"]
    assert "off_high_pct < -5" in p["when"]


def test_dsa_mid_pullback_expression_matches_band():
    when = "off_high_pct >= -10 and off_high_pct < -5"
    assert evaluate(when, {"off_high_pct": -7.0}) is True
    assert evaluate(when, {"off_high_pct": -5.0}) is False  # 边界不入
    assert evaluate(when, {"off_high_pct": -10.0}) is True
    assert evaluate(when, {"off_high_pct": -10.01}) is False
    assert evaluate(when, {"off_high_pct": -4.9}) is False
    assert evaluate(when, {"off_high_pct": -12.0}) is False


def test_dsa_skill_loads_with_new_phase():
    # 2026-10-13 DSA 已下线（L-161）：仍须可加载且契约完整，但必须保持 disabled。
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    assert SKILL_ID in skills
    assert skills[SKILL_ID].enabled is False

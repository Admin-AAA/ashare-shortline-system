"""距高点回撤15%~30%断裂区毒区（off_high_pct ∈ [-30, -15]）回归。

T3 2026-07-21：LIVE formal 全策略 off_high∈[-30,-15] 区间 n=28 netWR=32.1% netPL=0.44
totNet=-62.4（净负），跨 8 个交易日分布，非单日 regime 噪声。该区间属上升趋势破位/高位派发区，
非健康回踩（健康回踩 off_high>-15；极端超跌 off_high<-30 反有企稳反弹）。集中在 limit-up-reseal-relay
(n=7, totNet=-19.75) 与当时仍在线的 dragon-tiger-smart-money (n=7, totNet=-40.63, WR0%)。
2026-09-15 dragon-tiger-smart-money 退役（docs/strategy-retire-2026-09-15.md），本守卫只保留
仍在线的 limit-up-reseal-relay。
反事实验证（全量 LIVE rec_ n=467）：剔除该区间 → 净收益率 +62.4（delta_net_return>0，过拟合护栏通过），
剩余 netWR 67.7%→69.9% / netPL 2.15→2.47（双升）。仅作用于两策略（chao-gu 等有意买深跌，不动）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.engine.evaluator import evaluate
from app.engine.skill_loader import load_skills

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
PHASE_NAME = "距高点回撤15%-30%断裂区(毒区)"
WHEN = "off_high_pct >= -30 and off_high_pct < -15"
AFFECTED = ["limit-up-reseal-relay"]


def _yaml(skill_id: str) -> dict:
    return yaml.safe_load((SKILLS_DIR / skill_id / "strategy.yaml").read_text(encoding="utf-8"))


def test_phase_present_in_both_strategies():
    for sid in AFFECTED:
        phases = {p["name"]: p for p in _yaml(sid).get("phases") or []}
        assert PHASE_NAME in phases, f"{sid} 缺少断裂区毒区 phase"
        p = phases[PHASE_NAME]
        assert p.get("hard_veto") is True
        assert p.get("score_cap") == 50
        assert WHEN in p["when"]


def test_expression_matches_band():
    # 区间内命中：[-30, -15) 下界含、上界不含
    assert evaluate(WHEN, {"off_high_pct": -30.0}) is True   # 下界含
    assert evaluate(WHEN, {"off_high_pct": -29.99}) is True
    assert evaluate(WHEN, {"off_high_pct": -22.0}) is True
    assert evaluate(WHEN, {"off_high_pct": -15.01}) is True
    # 区间外不命中
    assert evaluate(WHEN, {"off_high_pct": -15.0}) is False   # 上界不含(exclusive)
    assert evaluate(WHEN, {"off_high_pct": -14.99}) is False  # 健康回踩，不否决
    assert evaluate(WHEN, {"off_high_pct": -30.01}) is False  # 极端超跌 rebound，不否决
    assert evaluate(WHEN, {"off_high_pct": -35.0}) is False
    assert evaluate(WHEN, {"off_high_pct": -5.0}) is False


def test_skill_loads_with_new_phase():
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    for sid in AFFECTED:
        assert sid in skills
        assert skills[sid].enabled is True

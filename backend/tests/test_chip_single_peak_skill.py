"""筹码单峰突破 skill contract test.

锁定 T1 新增策略的加载契约：能被自动发现、通过 validate_skill、所有 when
表达式只用 evaluator 白名单字段、且声明 formal_opening 预设组（R143 自愈注册）。
"""
import ast
from pathlib import Path

import pytest
import yaml

from app.engine import evaluator
from app.engine.skill_loader import load_skills, validate_skill

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_ID = "chip-single-peak-breakout"


def _all_when_exprs(spec):
    exprs = []
    for lst in ("include", "exclude"):
        for e in (spec.get("universe", {}).get(lst) or []):
            if isinstance(e, str):
                exprs.append(e)
    m = spec.get("prefilter", {}).get("market", {})
    for lst in ("include", "exclude", "include_any"):
        for it in (m.get(lst) or []):
            if isinstance(it, dict):
                exprs.append(it.get("when"))
    for sec in ("phases", "setups", "risk"):
        for it in (spec.get(sec) or []):
            if isinstance(it, dict):
                exprs.append(it.get("when"))
    for it in (spec.get("scoring", {}).get("rules") or []):
        if isinstance(it, dict):
            exprs.append(it.get("when"))
    return [e for e in exprs if isinstance(e, str) and e]


def test_chip_skill_discovered_and_valid():
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    assert SKILL_ID in skills, "chip-single-peak-breakout 未被 load_skills 自动发现"
    skill = skills[SKILL_ID]
    assert skill.agent_enabled is True, "SKILL.md 为空 -> LLM 层不可用"
    assert skill.executable is True
    errs = validate_skill(skill)
    assert errs == [], f"validate_skill 报错: {errs}"


def test_chip_skill_registers_formal_preset():
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    spec = yaml.safe_load((SKILLS_DIR / SKILL_ID / "strategy.yaml").read_text(encoding="utf-8"))
    groups = spec["meta"]["default_preset_groups"]
    assert "formal_opening" in groups, "未声明 formal_opening -> 不会被自愈注册进每日 formal run"


def test_chip_skill_when_exprs_use_allowed_names():
    spec = yaml.safe_load((SKILLS_DIR / SKILL_ID / "strategy.yaml").read_text(encoding="utf-8"))
    bad = set()
    for expr in _all_when_exprs(spec):
        node = ast.parse(expr.replace(" AND ", " and ").replace(" OR ", " or "), mode="eval")
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id not in ("true", "false") and n.id not in evaluator.ALLOWED_NAMES:
                bad.add(n.id)
    assert bad == set(), f"when 表达式含非白名单字段（将被 evaluator 判为 False，策略变死代码）: {bad}"


def test_chip_skill_signal_grounded_in_history():
    """回归护栏：核心因子 chip_concentration>=20 & 突破 必须在历史 formal 推荐中显著优于基线。"""
    from app.db.session import SessionLocal
    from sqlalchemy import text
    import json

    db = SessionLocal()
    rows = db.execute(text(
        'SELECT r.feature_snapshot_json, o.outcome_label FROM recommendations r '
        'JOIN recommendation_runs u ON r.run_id=u.run_id '
        'LEFT JOIN recommendation_outcomes o ON o.recommendation_id=r.recommendation_id '
        'WHERE u.run_mode="formal" AND r.feature_snapshot_json IS NOT NULL'
    )).fetchall()

    def num(d, k):
        v = d.get(k)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return None
        return v

    lab = succ = 0
    sig_n = sig_s = 0
    for snap, label in rows:
        d = json.loads(snap) if isinstance(snap, str) else snap
        c = num(d, "chip_concentration")
        if label in ("success", "failed"):
            lab += 1
            succ += label == "success"
            bo = d.get("breakout20") in (1, True) or d.get("breakout60") in (1, True)
            if c is not None and c >= 20 and bo:
                sig_n += 1
                sig_s += label == "success"
    base_wr = succ * 100 / lab if lab else 0
    sig_wr = sig_s * 100 / sig_n if sig_n else 0
    # 样本不足是等待前瞻数据，不是代码回归；保留为明确 skip，禁止用小样本 claim lift。
    if sig_n < 100:
        pytest.skip(f"核心因子历史样本不足 ({sig_n}/100)，暂不评价信号 lift")
    assert sig_wr >= base_wr + 5, f"核心因子 WR {sig_wr:.1f}% 未显著优于基线 {base_wr:.1f}%"

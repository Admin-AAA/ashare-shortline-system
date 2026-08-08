"""小盘题材动量 skill contract test.

锁定 T1 新增策略的加载契约：能被自动发现、通过 validate_skill、所有 when
表达式只用 evaluator 白名单字段、且声明 formal_opening 预设组（R143 自愈注册）。
核心因子（小盘+相对强度+动量+主线+未偏离+未过热）必须在历史推荐中显著优于基线。
"""
import ast
from pathlib import Path

import pytest
import yaml

from app.engine import evaluator
from app.engine.skill_loader import load_skills, validate_skill

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_ID = "small-cap-thematic-momentum"


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


def test_small_cap_skill_discovered_and_valid():
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    assert SKILL_ID in skills, "small-cap-thematic-momentum 未被 load_skills 自动发现"
    skill = skills[SKILL_ID]
    assert skill.agent_enabled is True, "SKILL.md 为空 -> LLM 层不可用"
    assert skill.executable is True
    errs = validate_skill(skill)
    assert errs == [], f"validate_skill 报错: {errs}"


def test_small_cap_skill_registers_formal_preset():
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    spec = yaml.safe_load((SKILLS_DIR / SKILL_ID / "strategy.yaml").read_text(encoding="utf-8"))
    groups = spec["meta"]["default_preset_groups"]
    assert "formal_opening" in groups, "未声明 formal_opening -> 不会被自愈注册进每日 formal run"


def test_small_cap_skill_when_exprs_use_allowed_names():
    spec = yaml.safe_load((SKILLS_DIR / SKILL_ID / "strategy.yaml").read_text(encoding="utf-8"))
    bad = set()
    for expr in _all_when_exprs(spec):
        node = ast.parse(expr.replace(" AND ", " and ").replace(" OR ", " or "), mode="eval")
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and n.id not in ("true", "false") and n.id not in evaluator.ALLOWED_NAMES:
                bad.add(n.id)
    assert bad == set(), f"when 表达式含非白名单字段（将被 evaluator 判为 False，策略变死代码）: {bad}"


@pytest.mark.integration
def test_small_cap_skill_signal_grounded_in_history():
    """回归护栏：核心因子组合必须在历史推荐中显著优于基线（n>=100, lift>=5pp）。"""
    from app.db.session import SessionLocal
    from sqlalchemy import text
    import json

    db = SessionLocal()
    rows = db.execute(text(
        'SELECT r.feature_snapshot_json, o.outcome_label FROM recommendations r '
        'LEFT JOIN recommendation_outcomes o ON o.recommendation_id=r.recommendation_id '
        'WHERE r.feature_snapshot_json IS NOT NULL'
    )).fetchall()

    def num(d, k):
        v = d.get(k)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return None
        return v

    def is_signal(d):
        return (num(d, "free_mv_yi") or 0) < 50 and (num(d, "pos20") or 0) >= 70 \
            and (num(d, "rsi_12") or 0) >= 60 and d.get("is_main_theme") is True \
            and (num(d, "dist20") or 99) < 10 and (num(d, "turnover_rate") or 0) < 8

    lab = succ = 0
    sig_n = sig_s = 0
    for snap, label in rows:
        d = json.loads(snap) if isinstance(snap, str) else snap
        if label in ("success", "failed"):
            lab += 1
            succ += label == "success"
            if is_signal(d):
                sig_n += 1
                sig_s += label == "success"
    base_wr = succ * 100 / lab if lab else 0
    sig_wr = sig_s * 100 / sig_n if sig_n else 0
    # 样本不足是等待前瞻数据，不是代码回归；保留为明确 skip，禁止用小样本 claim lift。
    if sig_n < 100:
        pytest.skip(f"核心因子历史样本不足 ({sig_n}/100)，暂不评价信号 lift")
    assert sig_wr >= base_wr + 5, f"核心因子 WR {sig_wr:.1f}% 未显著优于基线 {base_wr:.1f}%"

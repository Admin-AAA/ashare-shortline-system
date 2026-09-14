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


@pytest.mark.integration
def test_chip_skill_signal_grounded_in_history():
    """回归护栏：核心因子 chip_concentration ∈ (4,10]（90%成本带宽窄=单峰密集）
    & 突破 必须在历史 formal 推荐中显著优于基线。
    2026-09-13 语义修复（深审 A3-1）：原判定 c>=20 实际选中带宽≥20% 的分散股
    （方向反了），现改为修复后的密集带宽口径。历史推荐多按旧口径产生，
    样本不足时按设计 skip（等待前瞻数据），禁止用小样本 claim lift。

    R3 修正 (Sep 2026): 历史测试必须限定 canonical 前瞻及有效结算身份。
    仅筛 run_mode='formal' 会混入 historical_replay、实验性 run、重复结果及未结算样本，
    不能作为发布证据 (L-096 证明 canonical vs 混合口径可使精确 0 的效果被放大)。
    本测试为工程回归 (边界锁定)，不替代路径 A 三窗验证。测试通过不等于策略已验证；
    历史统计需使用样本身份、执行政策、结算有效性、配对日 CI 的证据输出。
    """
    from app.db.session import SessionLocal
    from sqlalchemy import select
    from app.models.tables import Recommendation, RecommendationOutcome, RecommendationRun
    from app.services.official_sample_scope import official_sample_conditions
    import json

    # canonical 身份由唯一选择器定义；结算标签之外仍须验证成交和信号有效性。
    with SessionLocal() as db:
        rows = db.execute(
            select(Recommendation.feature_snapshot_json, RecommendationOutcome.outcome_label, RecommendationOutcome.hit_invalid)
            .join(RecommendationRun, RecommendationRun.run_id == Recommendation.run_id)
            .join(RecommendationOutcome, RecommendationOutcome.recommendation_id == Recommendation.recommendation_id)
            .where(
                *official_sample_conditions(),
                Recommendation.feature_snapshot_json.is_not(None),
                RecommendationOutcome.outcome_label.in_(("success", "failed")),
                RecommendationOutcome.entry_triggered.is_(True),
            )
        ).all()

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
    for snap, label, hit_invalid in rows:
        d = json.loads(snap) if isinstance(snap, str) else snap
        c = num(d, "chip_concentration")
        if label in ("success", "failed"):
            lab += 1
            succ += label == "success" and not hit_invalid
            bo = d.get("breakout20") in (1, True) or d.get("breakout60") in (1, True)
            if c is not None and 4 < c <= 10 and bo:
                sig_n += 1
                sig_s += label == "success" and not hit_invalid
    base_wr = succ * 100 / lab if lab else 0
    sig_wr = sig_s * 100 / sig_n if sig_n else 0
    # 样本不足是等待前瞻数据，不是代码回归；保留为明确 skip，禁止用小样本 claim lift。
    if sig_n < 100:
        pytest.skip(f"核心因子历史样本不足 ({sig_n}/100)，暂不评价信号 lift")
    assert sig_wr >= base_wr + 5, f"核心因子 WR {sig_wr:.1f}% 未显著优于基线 {base_wr:.1f}%"

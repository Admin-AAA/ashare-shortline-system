"""chen-xiaoqun-skill 触发放宽回归：主线强度 prefilter off_high 门槛。

背景（2026-07-21 T1）：LIVE formal 主策略=chen-xiaoqun 去重样本 WR=100% (n=26)，
但 E 项曝光仅 6.8%、prefilter `off_high_pct >= -6` 把主线深度回踩票挡在门外。
实证：含 CXQ 的 oh∈[-10,-8) n=8 WR100%；主策略 oh<-8 n=8 WR100% avg+5.5%。
放宽到 -10 扩大赢家触发面，不改加速末期/退潮 hard_veto。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.engine.evaluator import evaluate
from app.engine.skill_loader import load_skills

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SKILL_ID = "chen-xiaoqun-skill"


def _cxq_yaml() -> dict:
    return yaml.safe_load((SKILLS_DIR / SKILL_ID / "strategy.yaml").read_text(encoding="utf-8"))


def test_chen_xiaoqun_main_strength_off_high_relaxed_to_minus_10():
    """prefilter include_any 主线强度门槛 off_high >= -10（原 -6）。"""
    spec = _cxq_yaml()
    any_rules = (spec.get("prefilter") or {}).get("market", {}).get("include_any") or []
    strength = [r for r in any_rules if r.get("reason") == "requires_main_strength"]
    assert len(strength) == 1, f"expected 1 requires_main_strength, got {strength}"
    when = strength[0]["when"]
    assert "off_high_pct >= -10" in when, when
    assert "off_high_pct >= -6" not in when, "旧 -6 门槛不得残留"
    assert "is_main_theme == true" in when
    assert "pct >= 1" in when


def test_chen_xiaoqun_main_strength_expression_accepts_deep_pullback():
    """表达式求值：主线 + pct>=1 + oh=-9 应过线；oh=-11 不过线。"""
    when = (
        "is_main_theme == true and pct >= 1 and off_high_pct >= -10"
    )
    ok = {
        "is_main_theme": True,
        "pct": 3.0,
        "off_high_pct": -9.0,
    }
    assert evaluate(when, ok) is True
    deep = {**ok, "off_high_pct": -11.0}
    assert evaluate(when, deep) is False
    # 非主线仍不过
    assert evaluate(when, {**ok, "is_main_theme": False}) is False


def test_chen_xiaoqun_skill_loads_and_enabled():
    skills = {s.skill_id: s for s in load_skills(SKILLS_DIR)}
    assert SKILL_ID in skills
    assert skills[SKILL_ID].enabled is True


def test_chen_xiaoqun_bias_ma5_cap_and_field_is_top_level():
    """L-182：prefilter 加 bias_ma5<=5；该字段必须由 _apply_dsa_context 写到候选顶层（L-178 教训）。"""
    from app.services.feature_builder import _apply_dsa_context

    spec = _cxq_yaml()
    inc = (spec.get("prefilter") or {}).get("market", {}).get("include") or []
    assert any(r.get("when") == "bias_ma5 <= 5" for r in inc)
    item = {"price": 10.6, "close": 10.6, "dist20": 8.0, "dist60": 12.0, "pct": 3.0}
    _apply_dsa_context(item, {"ma5": 10.0, "ma10": 9.8, "ma20": 9.6, "ma60": 9.0})
    assert item["bias_ma5"] == 6.0
    assert evaluate("bias_ma5 <= 5", item) is False
    item2 = {**item, "price": 10.3, "close": 10.3}
    _apply_dsa_context(item2, {"ma5": 10.0, "ma10": 9.8, "ma20": 9.6, "ma60": 9.0})
    assert evaluate("bias_ma5 <= 5", item2) is True

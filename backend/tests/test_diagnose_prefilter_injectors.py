"""回归测试：prefilter 诊断必须复刻生产管线的 prefilter 前字段注入。

背景（第165轮 T3.6 发现）：
  get_market_universe_from_pool 返回的池子不含 prefilter 阶段才注入的
  twenty_cm_risk_gate / dragon_tiger_is_on_list 等字段。生产管线在
  workbench_service.py 的 prefilter 前调用 inject_limit_up_flags +
  inject_dragon_tiger_flags 注入这些字段；diagnose_skill_prefilter.py 旧版
  没有调用，导致 strict_missing 下依赖这些字段的 universe 规则拒绝全部股票
  （small-cap/sector/limit-up-reseal/chip/dragon-tiger 被误诊为 0 命中）。

  本测试锁定：① 缺字段时 gated 策略被 strict_missing 拒绝；② 注入后字段非空
  且策略通过；③ 诊断用的整池注入后 twenty_cm_risk_gate 全非空。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.engine.skill_loader import load_skills  # noqa: E402
from app.engine.strategy_runner import evaluate_skill_prefilter  # noqa: E402
from app.services.market_pool import (  # noqa: E402
    get_market_universe_from_pool,
    inject_limit_up_flags,
    inject_dragon_tiger_flags,
)


def _gated_skill():
    settings = get_settings()
    skills = {s.skill_id: s for s in load_skills(settings.skills_dir)}
    # sector-relative-strength-leader 的 universe.include 含 twenty_cm_risk_gate != 'block'
    assert "sector-relative-strength-leader" in skills
    return skills["sector-relative-strength-leader"]


def test_twenty_cm_gate_requires_injection():
    """缺 twenty_cm_risk_gate 时 strict_missing 拒绝；注入 not_applicable 后通过。"""
    db = SessionLocal()
    try:
        skill = _gated_skill()
        stock = {
            "code": "000001",
            # universe.include
            "amount_yi": 5.0,
            "turnover_rate": 3.0,
            "free_mv_yi": 30.0,
            "primary_excess20": 10.0,  # >=3 且满足 include_any 选项2 (>=8)
            "max_dd20": -5.0,
            "off_high_pct": -5.0,
            "is_st": False,
            "announcement_negative_count": 0,
            "news_risk_count": 0,
            # prefilter.market.include
            "upper_shadow_count20": 2,
            "industry_capacity_like_count": 5,
            "industry_positive_excess20_count": 5,
            # prefilter.market.include_any (选项2: is_main_theme & primary_excess20>=8)
            "is_main_theme": True,
            "sector_ladder_completeness_score": 60,
            "shortline_leader_score": 70,
            # 其余字段省略=缺失，验证 strict_missing 仅在 twenty_cm 这一处被注入修复
        }
        ok_missing, reason = evaluate_skill_prefilter(skill, stock, stage="market")
        assert ok_missing is False, "缺字段时 strict_missing 应拒绝 gated 策略"
        assert stock.get("twenty_cm_risk_gate") is None

        inject_limit_up_flags(db, [stock], "2026-07-09")
        assert stock.get("twenty_cm_risk_gate") is not None, "inject 必须填充 twenty_cm_risk_gate"
        ok_after, _ = evaluate_skill_prefilter(skill, stock, stage="market")
        assert ok_after is True, "注入 not_applicable 后 twenty_cm != 'block' 应通过"
    finally:
        db.close()


def test_diagnostic_pool_twenty_cm_populated_after_injection():
    """诊断整池注入后 twenty_cm_risk_gate 应全非空（复刻生产管线）。"""
    db = SessionLocal()
    try:
        stocks, snap = get_market_universe_from_pool(db, refresh=False)
        td = str(snap.get("trade_date") or "")
        inject_limit_up_flags(db, stocks, td)
        inject_dragon_tiger_flags(db, stocks, td)
        none_count = sum(1 for s in stocks if s.get("twenty_cm_risk_gate") is None)
        assert none_count == 0, f"{none_count} 只股票注入后仍缺 twenty_cm_risk_gate"
    finally:
        db.close()
